"""Crawler service — reads PUUIDs from stream:puuid and emits match IDs."""

from __future__ import annotations

import logging
import os
import socket
import time
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.constants import PLAYER_DATA_TTL_SECONDS
from lol_pipeline.helpers import is_system_halted
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
from lol_pipeline.streams import ack, nack_to_dlq

_IN_STREAM = "stream:puuid"
_OUT_STREAM = "stream:match_id"
_GROUP = "crawlers"


async def _fetch_match_ids_paginated(  # noqa: PLR0913
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    puuid: str,
    region: str,
    known: set[str],
    log: logging.Logger,
    priority: str = "normal",
) -> tuple[int, int]:
    """Paginate through Riot match-ID API and publish new IDs to stream:match_id.

    Returns ``(published, pages_fetched)`` — the number of newly published match
    IDs and the number of API pages successfully fetched.  Raises Riot API
    exceptions (NotFoundError, AuthError, RateLimitError, ServerError) to the
    caller.

    After ``PRIORITY_DOWNGRADE_THRESHOLD`` (20) match IDs have been published,
    the priority tier is downgraded (e.g. manual_20 -> manual_20plus) for the
    remaining messages so that the first 20 get highest priority downstream.
    """
    start = 0
    count = 100
    published = 0
    pages_fetched = 0
    current_priority = priority

    # R2: Resume from saved cursor if available
    cursor_key = f"crawl:cursor:{puuid}"
    saved_start = await r.get(cursor_key)
    if saved_start:
        try:
            start = int(saved_start)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            start = 0

    while True:
        if await is_system_halted(r):
            log.info("system halted — aborting pagination", extra={"puuid": puuid})
            break
        # R1: Backpressure — pause if stream:match_id is too deep
        if cfg.match_id_backpressure_threshold > 0:
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
                break
        try:
            await wait_for_token(
                r, limit_per_second=cfg.api_rate_limit_per_second, region=region,
            )
        except TimeoutError:
            log.warning("rate limiter timeout — aborting crawl", extra={"puuid": puuid})
            break
        page: list[str] = await riot.get_match_ids(puuid, region, start=start, count=count)
        pages_fetched += 1
        # R2: Persist cursor after each successful page fetch
        await r.set(cursor_key, str(start + count), ex=600)  # 10-minute TTL
        log.debug(
            "fetched match ids page",
            extra={"puuid": puuid, "start": start, "returned": len(page)},
        )
        if not page:
            break

        new_ids = [mid for mid in page if mid not in known]
        # Global dedup: filter out matches already seen by any crawler
        if new_ids:
            async with r.pipeline(transaction=False) as check_pipe:
                for mid in new_ids:
                    check_pipe.sismember("seen:matches", mid)
                seen_results = await check_pipe.execute()
            new_ids = [mid for mid, seen in zip(new_ids, seen_results) if not seen]
        log.debug(
            "match id page filtered",
            extra={"puuid": puuid, "page_size": len(page), "new": len(new_ids)},
        )
        # P14-OPT-4: Batch all publishes for the page into a single pipeline.
        if new_ids:
            async with r.pipeline(transaction=False) as pipe:
                for match_id in new_ids:
                    # Downgrade priority after the threshold
                    if (
                        published >= PRIORITY_DOWNGRADE_THRESHOLD
                        and current_priority == priority
                    ):
                        current_priority = downgrade_priority(priority)
                    env = MessageEnvelope(
                        source_stream=_OUT_STREAM,
                        type="match_id",
                        payload={"match_id": match_id, "puuid": puuid, "region": region},
                        max_attempts=cfg.max_attempts,
                        priority=current_priority,
                    )
                    pipe.xadd(_OUT_STREAM, env.to_redis_fields())  # type: ignore[arg-type]
                    published += 1
                await pipe.execute()

        # Stop early if a full page was entirely known
        if len(page) == count and not new_ids:
            log.debug("full page already known — stopping", extra={"puuid": puuid})
            break
        if len(page) < count:
            break
        start += count

    # R2: Clear cursor on successful completion
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

    if isinstance(exc, AuthError):
        await r.set("system:halted", "1")
        log.critical("Riot API key rejected (403) — system halted", extra={"puuid": puuid})
        return  # do NOT ack — leave in PEL

    # RateLimitError | ServerError
    fc = "http_429" if isinstance(exc, RateLimitError) else "http_5xx"
    ram = exc.retry_after_ms if isinstance(exc, RateLimitError) else None
    log.error("Riot API error", extra={"error": str(exc), "failure_code": fc, "puuid": puuid})
    await nack_to_dlq(
        r,
        envelope,
        failure_code=fc,
        failed_by="crawler",
        original_message_id=msg_id,
        retry_after_ms=ram,
    )
    await ack(r, _IN_STREAM, _GROUP, msg_id)


async def _fetch_rank(  # noqa: PLR0913
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
            r, limit_per_second=cfg.api_rate_limit_per_second, region=region,
        )
        summoner = await riot.get_summoner_by_puuid(puuid, region)
        summoner_id: str = summoner.get("id", "")
        if not summoner_id:
            return
        # Store summoner level on the player hash if available
        level = summoner.get("summonerLevel")
        if level is not None:
            await r.hset(f"player:{puuid}", "summoner_level", str(level))  # type: ignore[misc]
        await wait_for_token(
            r, limit_per_second=cfg.api_rate_limit_per_second, region=region,
        )
        entries: list[dict[str, object]] = await riot.get_league_entries(summoner_id, region)
        rank_key = f"player:rank:{puuid}"
        for entry in entries:
            if entry.get("queueType") == "RANKED_SOLO_5x5":
                await r.hset(rank_key, mapping={  # type: ignore[misc]
                    "tier": str(entry.get("tier", "")),
                    "division": str(entry.get("rank", "")),
                    "lp": str(entry.get("leaguePoints", 0)),
                    "wins": str(entry.get("wins", 0)),
                    "losses": str(entry.get("losses", 0)),
                })
                await r.expire(rank_key, 86400)  # 24h TTL
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
        first_match = await r.zrange(
            f"player:matches:{puuid}", 0, 0, withscores=True,
        )
        if not first_match:
            return
        first_ts = first_match[0][1] / 1000  # ms to seconds
        total_matches = await r.zcard(f"player:matches:{puuid}")
        days = max((time.time() - first_ts) / 86400, 1)
        rate = total_matches / days
        await r.hset(f"player:{puuid}", "activity_rate", f"{rate:.2f}")  # type: ignore[misc]
        # Dynamic cooldown based on activity
        if rate > 5:  # >5 games/day
            cooldown_hours = 2
        elif rate > 1:  # >1 game/day
            cooldown_hours = 6
        else:
            cooldown_hours = 24
        recrawl_after = str(time.time() + cooldown_hours * 3600)
        await r.hset(f"player:{puuid}", "recrawl_after", recrawl_after)  # type: ignore[misc]
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
            r, riot, cfg, puuid, region, known, log, priority=envelope.priority
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
    await r.hset(f"player:{puuid}", mapping={"last_crawled_at": now_iso})  # type: ignore[misc]
    await r.expire(f"player:{puuid}", PLAYER_DATA_TTL_SECONDS)  # refreshed on each successful crawl
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
    cfg = Config()
    r = get_redis(cfg.redis_url)
    riot = RiotClient(cfg.riot_api_key, r=r)
    consumer = f"{socket.gethostname()}-{os.getpid()}"

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
