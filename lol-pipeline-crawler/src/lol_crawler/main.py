"""Crawler service — reads PUUIDs from stream:puuid and emits match IDs."""

from __future__ import annotations

import logging
import os
import socket
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.constants import PLAYER_DATA_TTL_SECONDS
from lol_pipeline.helpers import is_system_halted
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import clear_priority
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


async def _fetch_match_ids_paginated(
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    puuid: str,
    region: str,
    known: set[str],
    log: logging.Logger,
) -> tuple[int, int]:
    """Paginate through Riot match-ID API and publish new IDs to stream:match_id.

    Returns ``(published, pages_fetched)`` — the number of newly published match
    IDs and the number of API pages successfully fetched.  Raises Riot API
    exceptions (NotFoundError, AuthError, RateLimitError, ServerError) to the
    caller.
    """
    start = 0
    count = 100
    published = 0
    pages_fetched = 0

    while True:
        if await is_system_halted(r):
            log.info("system halted — aborting pagination", extra={"puuid": puuid})
            break
        try:
            await wait_for_token(r, limit_per_second=cfg.api_rate_limit_per_second)
        except TimeoutError:
            log.warning("rate limiter timeout — aborting crawl", extra={"puuid": puuid})
            break
        page: list[str] = await riot.get_match_ids(puuid, region, start=start, count=count)
        pages_fetched += 1
        log.debug(
            "fetched match ids page",
            extra={"puuid": puuid, "start": start, "returned": len(page)},
        )
        if not page:
            break

        new_ids = [mid for mid in page if mid not in known]
        log.debug(
            "match id page filtered",
            extra={"puuid": puuid, "page_size": len(page), "new": len(new_ids)},
        )
        # P14-OPT-4: Batch all publishes for the page into a single pipeline.
        if new_ids:
            async with r.pipeline(transaction=False) as pipe:
                for match_id in new_ids:
                    env = MessageEnvelope(
                        source_stream=_OUT_STREAM,
                        type="match_id",
                        payload={"match_id": match_id, "puuid": puuid, "region": region},
                        max_attempts=cfg.max_attempts,
                    )
                    pipe.xadd(_OUT_STREAM, env.to_redis_fields())  # type: ignore[arg-type]
                await pipe.execute()
            published += len(new_ids)

        # Stop early if a full page was entirely known
        if len(page) == count and not new_ids:
            log.debug("full page already known — stopping", extra={"puuid": puuid})
            break
        if len(page) < count:
            break
        start += count

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
            r, riot, cfg, puuid, region, known, log
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
    if published == 0:
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
