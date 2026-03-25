"""Crawler service — reads PUUIDs from stream:puuid and emits match IDs."""

from __future__ import annotations

import logging
import sys
import time
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from pydantic import ValidationError

from lol_pipeline.config import Config
from lol_pipeline.constants import PLAYER_DATA_TTL_SECONDS
from lol_pipeline.helpers import consumer_id, handle_riot_api_error, is_system_halted
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import PRIORITY_DOWNGRADE_THRESHOLD, clear_priority, downgrade_priority
from lol_pipeline.rate_limiter import wait_for_token
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    RiotClient,
    ServerError,
)
from lol_pipeline.service import run_consumer
from lol_pipeline.streams import MATCH_ID_STREAM_MAXLEN, ack

from lol_crawler._data import (
    _COOLDOWN_HIGH_HOURS,
    _COOLDOWN_HIGH_RATE,
    _COOLDOWN_LOW_HOURS,
    _COOLDOWN_MID_HOURS,
    _COOLDOWN_MID_RATE,
    _CURSOR_TTL,
    _GROUP,
    _IN_STREAM,
    _OUT_STREAM,
    _PAGE_SIZE,
    _RANK_TTL,
)
from lol_crawler._data import (
    _RANK_HISTORY_MAX as _RANK_HISTORY_MAX,
)


async def _check_backpressure(
    r: aioredis.Redis,
    cfg: Config,
    puuid: str,
    log: logging.Logger,
) -> bool:
    """Return True if stream:match_id depth exceeds the threshold."""
    if cfg.match_id_backpressure_threshold <= 0:
        return False
    depth = await r.xlen(_OUT_STREAM)
    if depth > cfg.match_id_backpressure_threshold:
        log.warning(
            "backpressure — match_id queue too deep, pausing crawl",
            extra={
                "depth": depth,
                "threshold": cfg.match_id_backpressure_threshold,
                "puuid": puuid,
            },
        )
        return True
    return False


async def _dedup_ids(
    r: aioredis.Redis,
    ids: list[str],
    known: set[str],
) -> list[str]:
    """Filter out IDs that are locally known or globally seen.

    RDB-1: Checks both today's and yesterday's daily-bucketed seen:matches sets.
    """
    new_ids = [mid for mid in ids if mid not in known]
    if not new_ids:
        return new_ids
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    yesterday = (datetime.now(tz=UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    async with r.pipeline(transaction=False) as check_pipe:
        for mid in new_ids:
            check_pipe.sismember(f"seen:matches:{today}", mid)
        for mid in new_ids:
            check_pipe.sismember(f"seen:matches:{yesterday}", mid)
        results = await check_pipe.execute()
    n = len(new_ids)
    today_results = results[:n]
    yesterday_results = results[n:]
    return [
        mid
        for mid, t, y in zip(new_ids, today_results, yesterday_results, strict=True)
        if not t and not y
    ]


async def _publish_batch(  # noqa: PLR0913
    r: aioredis.Redis,
    cfg: Config,
    new_ids: list[str],
    puuid: str,
    region: str,
    published: int,
    priority: str,
    current_priority: str,
    correlation_id: str,
) -> tuple[int, str]:
    """Batch-publish match IDs to stream:match_id. Return (published, priority)."""
    async with r.pipeline(transaction=False) as pipe:
        for match_id in new_ids:
            if published >= PRIORITY_DOWNGRADE_THRESHOLD and current_priority == priority:
                current_priority = downgrade_priority(priority)
            env = MessageEnvelope(
                source_stream=_OUT_STREAM,
                type="match_id",
                payload={
                    "match_id": match_id,
                    "puuid": puuid,
                    "region": region,
                },
                max_attempts=cfg.max_attempts,
                priority=current_priority,
                correlation_id=correlation_id,
            )
            pipe.xadd(
                _OUT_STREAM,
                env.to_redis_fields(),  # type: ignore[arg-type]
                maxlen=MATCH_ID_STREAM_MAXLEN,
                approximate=True,
            )
            published += 1
        await pipe.execute()
    return published, current_priority


async def _should_stop_pagination(
    r: aioredis.Redis,
    cfg: Config,
    puuid: str,
    region: str,
    log: logging.Logger,
) -> bool:
    """Return True if pagination should be aborted (halt, backpressure, rate limit)."""
    if await is_system_halted(r):
        log.info("system halted — aborting", extra={"puuid": puuid})
        return True
    if await _check_backpressure(r, cfg, puuid, log):
        return True
    try:
        await wait_for_token(
            r,
            limit_per_second=cfg.api_rate_limit_per_second,
            region=region,
        )
    except TimeoutError:
        log.warning(
            "rate limiter timeout — aborting crawl",
            extra={"puuid": puuid},
        )
        return True
    return False


async def _resume_cursor(r: aioredis.Redis, puuid: str) -> int:
    """Return the saved cursor offset, or 0 if none exists."""
    saved = await r.get(f"crawl:cursor:{puuid}")
    if saved:
        try:
            return int(saved)
        except (ValueError, TypeError):
            pass
    return 0


async def _fetch_match_ids_paginated(  # noqa: PLR0913
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    puuid: str,
    region: str,
    known: set[str],
    log: logging.Logger,
    priority: str = "normal",
    correlation_id: str = "",
) -> tuple[int, int]:
    """Paginate through Riot match-ID API and publish new IDs.

    Returns ``(published, pages_fetched)``.  Raises Riot API exceptions
    to the caller.
    """
    start = await _resume_cursor(r, puuid)
    published = 0
    pages_fetched = 0
    current_priority = priority
    cursor_key = f"crawl:cursor:{puuid}"

    while not await _should_stop_pagination(r, cfg, puuid, region, log):
        page = await riot.get_match_ids(
            puuid,
            region,
            start=start,
            count=_PAGE_SIZE,
        )
        pages_fetched += 1
        await r.set(cursor_key, str(start + _PAGE_SIZE), ex=_CURSOR_TTL)
        if not page:
            break

        new_ids = await _dedup_ids(r, page, known)
        log.debug(
            "match id page filtered",
            extra={
                "puuid": puuid,
                "page_size": len(page),
                "new": len(new_ids),
            },
        )
        if new_ids:
            published, current_priority = await _publish_batch(
                r,
                cfg,
                new_ids,
                puuid,
                region,
                published,
                priority,
                current_priority,
                correlation_id,
            )

        if len(page) == _PAGE_SIZE and not new_ids:
            break
        if len(page) < _PAGE_SIZE:
            break
        start += _PAGE_SIZE

    await r.delete(cursor_key)
    return published, pages_fetched


async def _handle_crawl_error(
    r: aioredis.Redis,
    msg_id: str,
    envelope: MessageEnvelope,
    exc: NotFoundError | AuthError | RateLimitError | ServerError,
    puuid: str,
    log: logging.Logger,
) -> None:
    """Handle Riot API errors during crawl — ack/nack/halt as appropriate."""
    if isinstance(exc, NotFoundError):
        log.info("player not found (404) — discarding", extra={"puuid": puuid})
        await clear_priority(r, puuid)
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        return
    await handle_riot_api_error(
        r,
        exc=exc,
        envelope=envelope,
        msg_id=msg_id,
        failed_by="crawler",
        in_stream=_IN_STREAM,
        group=_GROUP,
        log=log,
    )


async def _fetch_rank(
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    puuid: str,
    region: str,
    log: logging.Logger,
) -> None:
    """Fetch player rank via summoner-v4 + league-v4 and store in Redis."""
    if not cfg.fetch_rank_on_crawl:
        return
    try:
        await wait_for_token(
            r,
            limit_per_second=cfg.api_rate_limit_per_second,
            region=region,
        )
        summoner = await riot.get_summoner_by_puuid(puuid, region)
        summoner_id: str = summoner.get("id", "")
        if not summoner_id:
            return
        # Store summoner level and profile icon on the player hash if available
        level = summoner.get("summonerLevel")
        icon_id = summoner.get("profileIconId")
        player_updates: dict[str, str] = {}
        if level is not None:
            player_updates["summoner_level"] = str(level)
        if icon_id is not None:
            player_updates["profile_icon_id"] = str(icon_id)
        if player_updates:
            await r.hset(f"player:{puuid}", mapping=player_updates)  # type: ignore[misc]
        await wait_for_token(
            r,
            limit_per_second=cfg.api_rate_limit_per_second,
            region=region,
        )
        entries: list[dict[str, object]] = await riot.get_league_entries(summoner_id, region)
        rank_key = f"player:rank:{puuid}"
        for entry in entries:
            if entry.get("queueType") == "RANKED_SOLO_5x5":
                tier = str(entry.get("tier", ""))
                division = str(entry.get("rank", ""))
                lp = str(entry.get("leaguePoints", 0))
                epoch_ms = int(time.time() * 1000)
                hist_key = f"player:rank:history:{puuid}"
                async with r.pipeline(transaction=False) as rank_pipe:
                    rank_pipe.hset(
                        rank_key,
                        mapping={
                            "tier": tier,
                            "division": division,
                            "lp": lp,
                            "wins": str(entry.get("wins", 0)),
                            "losses": str(entry.get("losses", 0)),
                        },
                    )
                    rank_pipe.expire(rank_key, _RANK_TTL)
                    rank_pipe.zadd(hist_key, {f"{tier}:{division}:{lp}": epoch_ms})
                    rank_pipe.zremrangebyrank(hist_key, 0, -(_RANK_HISTORY_MAX + 1))
                    rank_pipe.expire(hist_key, PLAYER_DATA_TTL_SECONDS)
                    await rank_pipe.execute()
                break
    except Exception:
        log.debug("rank fetch failed — non-critical", extra={"puuid": puuid}, exc_info=True)


async def _compute_activity_rate(
    r: aioredis.Redis,
    puuid: str,
    log: logging.Logger,
) -> None:
    """Compute activity rate (matches/day) and set dynamic recrawl cooldown."""
    try:
        matches_key = f"player:matches:{puuid}"
        async with r.pipeline(transaction=False) as read_pipe:
            read_pipe.zrange(matches_key, 0, 0, withscores=True)
            read_pipe.zcard(matches_key)
            results = await read_pipe.execute()
        first_match: list[tuple[str, float]] = results[0]
        if not first_match:
            return
        first_ts = first_match[0][1] / 1000  # ms to seconds
        total_matches: int = results[1]
        days = max((time.time() - first_ts) / 86400, 1)
        rate = total_matches / days
        # Dynamic cooldown based on activity
        if rate > _COOLDOWN_HIGH_RATE:
            cooldown_hours = _COOLDOWN_HIGH_HOURS
        elif rate > _COOLDOWN_MID_RATE:
            cooldown_hours = _COOLDOWN_MID_HOURS
        else:
            cooldown_hours = _COOLDOWN_LOW_HOURS
        recrawl_after = str(time.time() + cooldown_hours * 3600)
        player_key = f"player:{puuid}"
        await r.hset(  # type: ignore[misc]
            player_key,
            mapping={"activity_rate": f"{rate:.2f}", "recrawl_after": recrawl_after},
        )
    except Exception:
        log.debug("activity rate compute failed", extra={"puuid": puuid}, exc_info=True)


async def _crawl_player(
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
) -> None:
    if await is_system_halted(r):
        log.critical("system halted — skipping message")
        return

    puuid: str = envelope.payload["puuid"]
    region: str = envelope.payload["region"]
    game_name: str = envelope.payload.get("game_name", "")
    tag_line: str = envelope.payload.get("tag_line", "")

    last_crawled = await r.hget(f"player:{puuid}", "last_crawled_at")  # type: ignore[misc]
    if last_crawled:
        try:
            cutoff_dt = datetime.fromisoformat(last_crawled) - timedelta(days=7)
            cutoff_ms = cutoff_dt.timestamp() * 1000
            known: set[str] = set(
                await r.zrangebyscore(f"player:matches:{puuid}", cutoff_ms, "+inf")
            )
        except ValueError:
            log.warning(
                "corrupt last_crawled_at — falling back to full ZRANGE",
                extra={"puuid": puuid, "last_crawled_at": last_crawled},
            )
            known = set(await r.zrange(f"player:matches:{puuid}", 0, -1))
    else:
        known = set(await r.zrange(f"player:matches:{puuid}", 0, -1))
    log.info(
        "starting crawl",
        extra={
            "puuid": puuid,
            "game_name": game_name,
            "tag_line": tag_line,
            "region": region,
            "known_matches": len(known),
        },
    )

    try:
        published, pages_fetched = await _fetch_match_ids_paginated(
            r,
            riot,
            cfg,
            puuid,
            region,
            known,
            log,
            priority=envelope.priority,
            correlation_id=envelope.correlation_id,
        )
    except (NotFoundError, AuthError, RateLimitError, ServerError) as exc:
        await _handle_crawl_error(r, msg_id, envelope, exc, puuid, log)
        return

    if pages_fetched == 0:
        # Rate limiter timed out before any API call — do not update last_crawled_at.
        # The message is ACKed to avoid infinite redelivery, but we don't mark
        # the player as crawled so Discovery can re-seed them next cycle.
        log.warning(
            "crawl aborted before any API call (rate limiter timeout)",
            extra={"puuid": puuid},
        )
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        return

    now_iso = datetime.now(tz=UTC).isoformat()
    player_key = f"player:{puuid}"
    async with r.pipeline(transaction=False) as post_pipe:
        post_pipe.hset(player_key, mapping={"last_crawled_at": now_iso})
        post_pipe.expire(player_key, PLAYER_DATA_TTL_SECONDS)  # refreshed on each crawl
        await post_pipe.execute()
    # Rank data: fetch summoner + league entries (non-critical)
    await _fetch_rank(r, riot, cfg, puuid, region, log)
    # Activity rate: compute matches/day and dynamic recrawl cooldown
    if published > 0:
        await _compute_activity_rate(r, puuid, log)
    # R5: Always clear priority after a successful crawl.  Previously this only
    # happened when published==0, which left the priority key active (up to TTL)
    # if matches were still in-flight downstream.
    await clear_priority(r, puuid)
    log.info(
        "crawl complete",
        extra={
            "puuid": puuid,
            "game_name": game_name,
            "tag_line": tag_line,
            "region": region,
            "published": published,
        },
    )
    await ack(r, _IN_STREAM, _GROUP, msg_id)


async def main() -> None:
    """Crawler worker loop."""
    log = get_logger("crawler")
    try:
        cfg = Config()
    except ValidationError as exc:
        print(
            f"Configuration error: {exc}\nCheck .env.example for required variables.",
            file=sys.stderr,
        )
        sys.exit(1)
    r = get_redis(cfg.redis_url)
    riot = RiotClient(cfg.riot_api_key, r=r)
    consumer = consumer_id()

    async def _handler(msg_id: str, envelope: MessageEnvelope) -> None:
        await _crawl_player(r, riot, cfg, msg_id, envelope, log)

    log.info("crawler started", extra={"consumer": consumer})
    try:
        autoclaim_ms = cfg.stream_ack_timeout * 1000
        await run_consumer(
            r,
            _IN_STREAM,
            _GROUP,
            consumer,
            _handler,
            log,
            autoclaim_min_idle_ms=autoclaim_ms,
        )
    finally:
        await r.aclose()
        await riot.close()
