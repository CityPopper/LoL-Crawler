"""Fetcher service — fetches raw match JSON via WaterfallCoordinator."""

from __future__ import annotations

import dataclasses
import json
import logging
import sys
from datetime import UTC, datetime

import redis.asyncio as aioredis
from lol_pipeline._helpers import consumer_id, is_system_halted
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.opgg_client import OpggClient
from lol_pipeline.rate_limiter_client import wait_for_token
from lol_pipeline.raw_store import RawStore
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.service import run_consumer
from lol_pipeline.sources.base import MATCH, DataType, Extractor, FetchContext
from lol_pipeline.sources.blob_store import BlobStore
from lol_pipeline.sources.coordinator import WaterfallCoordinator
from lol_pipeline.sources.opgg.extractors import OpggMatchExtractor
from lol_pipeline.sources.opgg.source import OpggSource
from lol_pipeline.sources.registry import SourceEntry, SourceRegistry
from lol_pipeline.sources.riot.source import RiotExtractor, RiotSource
from lol_pipeline.streams import ack, nack_to_dlq, publish
from pydantic import ValidationError

from lol_fetcher._constants import GROUP, IN_STREAM, OUT_STREAM

_IN_STREAM = IN_STREAM
_OUT_STREAM = OUT_STREAM
_GROUP = GROUP
_log = get_logger("fetcher")


async def _set_match_status(
    r: aioredis.Redis,
    match_id: str,
    status: str,
    ttl: int,
) -> None:
    """Set match:{match_id} status field and apply TTL."""
    match_key = f"match:{match_id}"
    async with r.pipeline(transaction=False) as pipe:
        pipe.hset(match_key, mapping={"status": status})
        pipe.expire(match_key, ttl)
        await pipe.execute()


async def _publish_and_ack(
    r: aioredis.Redis,
    cfg: Config,
    msg_id: str,
    envelope: MessageEnvelope,
) -> None:
    """Build a parse envelope, publish to stream:parse, and ACK the inbound message."""
    match_id: str = envelope.payload["match_id"]
    region: str = envelope.payload["region"]
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


async def _write_seen_match(
    r: aioredis.Redis,
    cfg: Config,
    match_id: str,
) -> None:
    """Add match_id to the daily-bucketed seen:matches set and set metadata.

    RDB-1: Each bucket covers one UTC day and expires after
    ``cfg.seen_matches_ttl_seconds``.  The crawler checks today's and
    yesterday's buckets to decide whether a match has already been fetched.
    """
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    seen_key = f"seen:matches:{today}"
    await _set_match_status(r, match_id, "fetched", cfg.match_data_ttl_seconds)
    async with r.pipeline(transaction=False) as pipe:
        pipe.sadd(seen_key, match_id)
        pipe.ttl(seen_key)
        results = await pipe.execute()

    # Only set TTL when none exists (ttl < 0) to avoid resetting expiry on every write.
    seen_ttl: int = results[1]
    if seen_ttl < 0:
        await r.expire(seen_key, cfg.seen_matches_ttl_seconds)


async def _fetch_timeline_if_needed(
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    match_id: str,
    region: str,
) -> None:
    """Fetch and store the match timeline when enabled (non-critical)."""
    if not cfg.fetch_timeline:
        return
    try:
        await wait_for_token("riot", "timeline")
        timeline = await riot.get_match_timeline(match_id, region)
        timeline_json = json.dumps(timeline)
        await r.set(f"raw:timeline:{match_id}", timeline_json, ex=cfg.match_data_ttl_seconds)
    except Exception:
        _log.debug(
            "timeline fetch failed — non-critical",
            extra={"match_id": match_id},
            exc_info=True,
        )


async def _after_fetch_success(
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    msg_id: str,
    envelope: MessageEnvelope,
) -> None:
    """Post-fetch bookkeeping: update seen set, fetch timeline, publish, ack.

    Called after the coordinator has already stored data via RawStore.
    """
    match_id: str = envelope.payload["match_id"]
    region: str = envelope.payload["region"]
    puuid: str = envelope.payload.get("puuid", "")

    await _write_seen_match(r, cfg, match_id)
    await _fetch_timeline_if_needed(r, riot, cfg, match_id, region)
    await _publish_and_ack(r, cfg, msg_id, envelope)
    _log.info(
        "fetched and stored",
        extra={"match_id": match_id, "region": region, "puuid": puuid},
    )


def _build_coordinator(
    riot: RiotClient,
    raw_store: RawStore,
    cfg: Config,
    opgg: OpggClient | None = None,
) -> WaterfallCoordinator:
    """Construct a WaterfallCoordinator from service dependencies."""
    # Available source builders keyed by name.
    source_builders: dict[str, tuple[SourceEntry, list[Extractor]] | None] = {}

    # Riot is always available.
    riot_source = RiotSource(riot_client=riot)
    riot_extractor = RiotExtractor()
    source_builders["riot"] = (
        SourceEntry(name="riot", source=riot_source, priority=0, primary_for=frozenset({MATCH})),
        [riot_extractor],
    )

    # Op.gg is available only when enabled and client is present.
    if cfg.opgg_enabled and opgg is not None:
        try:
            opgg_source = OpggSource(opgg_client=opgg)
            opgg_extractor = OpggMatchExtractor()
            source_builders["opgg"] = (
                SourceEntry(name="opgg", source=opgg_source, priority=0),
                [opgg_extractor],
            )
        except Exception:
            _log.warning("source 'opgg' unavailable at startup, skipping", exc_info=True)
            source_builders["opgg"] = None
    else:
        source_builders["opgg"] = None

    # Parse source_waterfall_order to determine registration order and priorities.
    order = [s.strip() for s in cfg.source_waterfall_order.split(",") if s.strip()]
    entries: list[SourceEntry] = []
    extractors: list[Extractor] = []

    for idx, name in enumerate(order):
        builder = source_builders.get(name)
        if builder is None:
            _log.warning(
                "source %r in source_waterfall_order is not available, skipping",
                name,
            )
            continue
        entry, exts = builder
        # Override priority based on position in the order list.
        entries.append(
            SourceEntry(
                name=entry.name,
                source=entry.source,
                priority=idx * 10,
                primary_for=entry.primary_for,
            )
        )
        extractors.extend(exts)

    # Build extractor index for startup cross-check.
    extractor_index: dict[tuple[str, DataType], Extractor] = {
        (ext.source_name, dt): ext for ext in extractors for dt in ext.data_types
    }
    registry = SourceRegistry(entries, extractor_index=extractor_index)

    blob_store: BlobStore | None = None
    if cfg.blob_data_dir:
        try:
            blob_store = BlobStore(cfg.blob_data_dir)
        except Exception:
            _log.warning(
                "source 'blob_store' unavailable at startup, skipping", exc_info=True
            )
    return WaterfallCoordinator(registry, blob_store, raw_store, extractors)


async def _fetch_match(  # noqa: PLR0913
    r: aioredis.Redis,
    riot: RiotClient,
    raw_store: RawStore,
    cfg: Config,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
    opgg: OpggClient | None = None,
    coordinator: WaterfallCoordinator | None = None,
) -> None:
    """Fetch a match via WaterfallCoordinator and route the result."""
    if await is_system_halted(r):
        log.critical("system halted — skipping message")
        return

    match_id: str = envelope.payload["match_id"]
    region: str = envelope.payload["region"]
    puuid: str = envelope.payload.get("puuid", "")
    log.info("processing match", extra={"match_id": match_id, "region": region, "puuid": puuid})

    wf = coordinator or _build_coordinator(riot, raw_store, cfg, opgg)
    context = FetchContext(match_id=match_id, puuid=puuid, region=region)
    result = await wf.fetch_match(context, MATCH)

    if result.status == "cached":
        await _publish_and_ack(r, cfg, msg_id, envelope)
        log.info("idempotent re-delivery — raw blob exists", extra={"match_id": match_id})
        return

    if result.status == "success":
        await _after_fetch_success(r, riot, cfg, msg_id, envelope)
        return

    if result.status == "not_found":
        await _set_match_status(r, match_id, "not_found", cfg.match_data_ttl_seconds)
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        log.info("match not found — discarding", extra={"match_id": match_id})
        return

    if result.status == "auth_error":
        await r.set("system:halted", "1")
        log.critical("API key rejected (403) — system halted")
        return

    # all_exhausted
    if result.blob_validation_failed:
        # Force max_attempts=1 so recovery archives immediately (no retry cycles).
        archive_env = dataclasses.replace(envelope, max_attempts=1)
        await nack_to_dlq(
            r,
            archive_env,
            failure_code="blob_validation_failed",
            failed_by="fetcher",
            original_message_id=msg_id,
            failure_reason="blob failed can_extract — immediate archive",
        )
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        log.warning(
            "blob validation failed — routing to DLQ for archive",
            extra={"match_id": match_id},
        )
        return

    # Determine failure code from coordinator hints
    failure_code = "http_429" if result.retry_after_ms else "http_5xx"
    await nack_to_dlq(
        r,
        envelope,
        failure_code=failure_code,
        failed_by="fetcher",
        original_message_id=msg_id,
        retry_after_ms=result.retry_after_ms,
    )
    await ack(r, _IN_STREAM, _GROUP, msg_id)
    log.error(
        "all sources exhausted — routing to DLQ",
        extra={"match_id": match_id, "failure_code": failure_code},
    )


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
    opgg: OpggClient | None = (
        OpggClient(
            r=r,
            rate_limit_per_second=cfg.opgg_rate_limit_per_second,
            rate_limit_long=cfg.opgg_rate_limit_long,
        )
        if cfg.opgg_enabled
        else None
    )
    coordinator = _build_coordinator(riot, raw_store, cfg, opgg)
    consumer = consumer_id()

    async def _handler(msg_id: str, envelope: MessageEnvelope) -> None:
        await _fetch_match(
            r,
            riot,
            raw_store,
            cfg,
            msg_id,
            envelope,
            log,
            opgg=opgg,
            coordinator=coordinator,
        )

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
