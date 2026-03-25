"""Fetcher service — fetches raw match JSON from Riot API and stores it."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from pydantic import ValidationError

from lol_pipeline.config import Config
from lol_pipeline.helpers import consumer_id, handle_riot_api_error, is_system_halted
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.rate_limiter import wait_for_token
from lol_pipeline.raw_store import RawStore
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import AuthError, NotFoundError, RateLimitError, RiotClient, ServerError
from lol_pipeline.service import run_consumer
from lol_pipeline.opgg_client import OpggClient, OpggParseError, OpggRateLimitError
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

    # RDB-1: Daily-bucketed seen:matches sets.  Each bucket covers one UTC day
    # and expires after 8 days.  The crawler checks today's and yesterday's buckets
    # to decide whether a match has already been fetched.
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    seen_key = f"seen:matches:{today}"
    match_key = f"match:{match_id}"
    async with r.pipeline(transaction=False) as pipe:
        pipe.hset(match_key, mapping={"status": "fetched"})
        pipe.expire(match_key, cfg.match_data_ttl_seconds)
        pipe.sadd(seen_key, match_id)
        pipe.ttl(seen_key)
        results = await pipe.execute()

    # Only set TTL when none exists (ttl < 0) to avoid resetting expiry on every write.
    seen_ttl: int = results[3]
    if seen_ttl < 0:
        await r.expire(seen_key, 8 * 86400)  # 8 days

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


async def _try_opgg(
    opgg: OpggClient,
    raw_store: RawStore,
    match_id: str,
    log: logging.Logger,
) -> dict[str, Any] | None:
    """Try fetching match data from op.gg. Returns data dict or None on failure.

    Op.gg failures are logged as warnings and never set system:halted.
    """
    try:
        existing = await raw_store.get(match_id)
        if existing is not None:
            return json.loads(existing)
    except (OpggParseError, OpggRateLimitError, json.JSONDecodeError, Exception) as exc:
        log.warning(
            "op.gg fetch failed — falling through to Riot API",
            extra={"match_id": match_id, "error": str(exc)},
        )
    return None


async def _fetch_match(  # noqa: PLR0913
    r: aioredis.Redis,
    riot: RiotClient,
    raw_store: RawStore,
    cfg: Config,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
    opgg: OpggClient | None = None,
) -> None:
    if await is_system_halted(r):
        log.critical("system halted — skipping message")
        return

    match_id: str = envelope.payload["match_id"]
    region: str = envelope.payload["region"]
    puuid: str = envelope.payload.get("puuid", "")
    source: str = envelope.payload.get("source", "riot")
    log.info("processing match", extra={"match_id": match_id, "region": region, "puuid": puuid})

    # Determine which RawStore to use based on source
    is_opgg_source = cfg.opgg_enabled and source == "opgg" and opgg is not None
    if is_opgg_source:
        opgg_raw_store = RawStore(r, data_dir=cfg.opgg_match_data_dir, key_prefix="raw:opgg:match:")
    else:
        opgg_raw_store = None

    # Idempotency: check the appropriate store
    active_store = opgg_raw_store if opgg_raw_store is not None else raw_store
    if await active_store.exists(match_id):
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

    # Try op.gg first when enabled and source is opgg
    if is_opgg_source and opgg_raw_store is not None:
        opgg_data = await _try_opgg(opgg, opgg_raw_store, match_id, log)
        if opgg_data is not None:
            await _store_and_publish(r, riot, opgg_raw_store, cfg, msg_id, envelope, opgg_data)
            return
        log.warning("op.gg source failed — falling through to Riot API", extra={"match_id": match_id})

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
    opgg: OpggClient | None = OpggClient(cfg) if cfg.opgg_enabled else None
    consumer = consumer_id()

    async def _handler(msg_id: str, envelope: MessageEnvelope) -> None:
        await _fetch_match(r, riot, raw_store, cfg, msg_id, envelope, log, opgg=opgg)

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
