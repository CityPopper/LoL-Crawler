"""Fetcher service — fetches raw match JSON from Riot API and stores it."""

from __future__ import annotations

import json
import logging
import os
import socket

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.rate_limiter import wait_for_token
from lol_pipeline.raw_store import RawStore
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import AuthError, NotFoundError, RateLimitError, RiotClient, ServerError
from lol_pipeline.service import run_consumer
from lol_pipeline.streams import ack, nack_to_dlq, publish

_IN_STREAM = "stream:match_id"
_OUT_STREAM = "stream:parse"
_GROUP = "fetchers"


async def _fetch_match(
    r: aioredis.Redis,
    riot: RiotClient,
    raw_store: RawStore,
    cfg: Config,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
) -> None:
    if await r.get("system:halted"):
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
        )
        await publish(r, _OUT_STREAM, out)
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        log.info("idempotent re-delivery — raw blob exists", extra=extras)
        return

    try:
        await wait_for_token(r, limit_per_second=cfg.api_rate_limit_per_second)
        data = await riot.get_match(match_id, region)
    except NotFoundError:
        await r.hset(f"match:{match_id}", mapping={"status": "not_found"})  # type: ignore[misc]
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        log.info("match not found — discarding", extra={"match_id": match_id})
        return
    except AuthError:
        await r.set("system:halted", "1")
        log.critical("Riot API key rejected (403) — system halted", extra={"match_id": match_id})
        return  # do NOT ack
    except RateLimitError as exc:
        await nack_to_dlq(
            r,
            envelope,
            failure_code="http_429",
            failed_by="fetcher",
            original_message_id=msg_id,
            retry_after_ms=exc.retry_after_ms,
        )
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        return
    except ServerError as exc:
        log.error("server error", extra={"error": str(exc), "match_id": match_id})
        await nack_to_dlq(
            r,
            envelope,
            failure_code="http_5xx",
            failed_by="fetcher",
            original_message_id=msg_id,
        )
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        return

    await raw_store.set(match_id, json.dumps(data))
    await r.hset(f"match:{match_id}", mapping={"status": "fetched"})  # type: ignore[misc]

    out = MessageEnvelope(
        source_stream=_OUT_STREAM,
        type="parse",
        payload={"match_id": match_id, "region": region},
        max_attempts=cfg.max_attempts,
    )
    await publish(r, _OUT_STREAM, out)
    await ack(r, _IN_STREAM, _GROUP, msg_id)
    log.info("fetched and stored", extra={"match_id": match_id, "region": region, "puuid": puuid})


async def main() -> None:
    """Fetcher worker loop."""
    log = get_logger("fetcher")
    cfg = Config()
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
