"""Fetcher service — fetches raw match JSON from Riot API and stores it."""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
from typing import Any

import redis.asyncio as aioredis
from pydantic import ValidationError

from lol_pipeline.config import Config
from lol_pipeline.helpers import handle_riot_api_error, is_system_halted
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.rate_limiter import wait_for_token
from lol_pipeline.raw_store import RawStore
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import AuthError, NotFoundError, RateLimitError, RiotClient, ServerError
from lol_pipeline.service import run_consumer
from lol_pipeline.streams import ack, publish

_IN_STREAM = "stream:match_id"
_OUT_STREAM = "stream:parse"
_GROUP = "fetchers"
_log = logging.getLogger("fetcher")


async def _store_and_publish(
    r: aioredis.Redis,
    riot: RiotClient,
    raw_store: RawStore,
    cfg: Config,
    msg_id: str,
    envelope: MessageEnvelope,
    data: dict[str, Any],
) -> None:
    """Store fetched match data, update metadata, and publish to parse stream.

    Redis metadata writes (match status, TTL, seen:matches) are batched into a
    single pipeline round-trip.  ``raw_store.set()`` stays sequential because it
    checks its return value (SET NX) to gate disk writes and has rollback logic.
    ``publish()`` and ``ack()`` stay sequential because ack must not run if
    publish fails (otherwise the message is lost).
    """
    match_id: str = envelope.payload["match_id"]
    region: str = envelope.payload["region"]
    puuid: str = envelope.payload.get("puuid", "")

    await raw_store.set(match_id, json.dumps(data))

    # Batch independent metadata writes into a single Redis round-trip.
    # TODO: seen:matches is a single SET that grows unbounded within its TTL window.
    # At high throughput (~1M matches/week) this can consume significant memory.
    # Consider daily-bucketed sets (seen:matches:{YYYY-MM-DD}) with per-bucket TTL
    # and multi-key SISMEMBER checks in crawler dedup. This is a larger architectural
    # change — see docs/architecture/04-storage.md for current schema.
    match_key = f"match:{match_id}"
    async with r.pipeline(transaction=False) as pipe:
        pipe.hset(match_key, mapping={"status": "fetched"})
        pipe.expire(match_key, cfg.match_data_ttl_seconds)
        pipe.sadd("seen:matches", match_id)
        pipe.ttl("seen:matches")
        results = await pipe.execute()

    # Only set TTL when none exists (ttl < 0) to avoid resetting expiry on every write.
    seen_ttl: int = results[3]
    if seen_ttl < 0:
        await r.expire("seen:matches", cfg.seen_matches_ttl_seconds)

    # Timeline fetch (non-critical, doubles API usage)
    if cfg.fetch_timeline:
        try:
            await wait_for_token(
                r,
                limit_per_second=cfg.api_rate_limit_per_second,
                region=region,
            )
            timeline = await riot.get_match_timeline(match_id, region)
            timeline_json = json.dumps(timeline)
            await r.set(f"raw:timeline:{match_id}", timeline_json, ex=cfg.match_data_ttl_seconds)
        except Exception:
            _log.debug(
                "timeline fetch failed — non-critical",
                extra={"match_id": match_id},
                exc_info=True,
            )

    out = MessageEnvelope(
        source_stream=_OUT_STREAM,
        type="parse",
        payload={"match_id": match_id, "region": region},
        max_attempts=cfg.max_attempts,
        priority=envelope.priority,
        correlation_id=envelope.correlation_id,
    )
    await publish(r, _OUT_STREAM, out)
    await ack(r, _IN_STREAM, _GROUP, msg_id)
    _log.info("fetched and stored", extra={"match_id": match_id, "region": region, "puuid": puuid})


async def _fetch_match(
    r: aioredis.Redis,
    riot: RiotClient,
    raw_store: RawStore,
    cfg: Config,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
) -> None:
    if await is_system_halted(r):
        log.critical("system halted — skipping message")
        return

    match_id: str = envelope.payload["match_id"]
    region: str = envelope.payload["region"]
    puuid: str = envelope.payload.get("puuid", "")
    log.info("processing match", extra={"match_id": match_id, "region": region, "puuid": puuid})

    # Idempotency: if raw blob already exists, skip fetch and re-publish to parse
    if await raw_store.exists(match_id):
        extras = {"match_id": match_id, "puuid": puuid}
        log.info("raw blob already stored — skipping fetch", extra=extras)
        out = MessageEnvelope(
            source_stream=_OUT_STREAM,
            type="parse",
            payload={"match_id": match_id, "region": region},
            max_attempts=cfg.max_attempts,
            priority=envelope.priority,
            correlation_id=envelope.correlation_id,
        )
        await publish(r, _OUT_STREAM, out)
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        log.info("idempotent re-delivery — raw blob exists", extra=extras)
        return

    try:
        await wait_for_token(
            r,
            limit_per_second=cfg.api_rate_limit_per_second,
            region=region,
        )
        data = await riot.get_match(match_id, region)
    except TimeoutError:
        log.warning(
            "rate limiter timeout — leaving in PEL for retry",
            extra={"match_id": match_id},
        )
        return
    except NotFoundError:
        await r.hset(f"match:{match_id}", mapping={"status": "not_found"})  # type: ignore[misc]
        await r.expire(f"match:{match_id}", cfg.match_data_ttl_seconds)
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        log.info("match not found — discarding", extra={"match_id": match_id})
        return
    except (AuthError, RateLimitError, ServerError) as exc:
        await handle_riot_api_error(
            r,
            exc=exc,
            envelope=envelope,
            msg_id=msg_id,
            failed_by="fetcher",
            in_stream=_IN_STREAM,
            group=_GROUP,
            log=log,
        )
        return

    await _store_and_publish(r, riot, raw_store, cfg, msg_id, envelope, data)


async def main() -> None:
    """Fetcher worker loop."""
    log = get_logger("fetcher")
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
    raw_store = RawStore(r, data_dir=cfg.match_data_dir)
    consumer = f"{socket.gethostname()}-{os.getpid()}"

    async def _handler(msg_id: str, envelope: MessageEnvelope) -> None:
        await _fetch_match(r, riot, raw_store, cfg, msg_id, envelope, log)

    log.info("fetcher started", extra={"consumer": consumer})
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
