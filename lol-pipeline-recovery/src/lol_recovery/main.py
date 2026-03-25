"""Recovery service — processes DLQ entries, requeues or archives them."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import signal
import time

import redis.asyncio as aioredis
from lol_pipeline._helpers import consumer_id, is_system_halted
from lol_pipeline.config import Config
from lol_pipeline.constants import DELAYED_MESSAGES_KEY
from lol_pipeline.log import get_logger
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from lol_pipeline.redis_client import get_redis
from lol_pipeline.streams import consume_typed
from redis.exceptions import RedisError

from lol_recovery._constants import (
    _ARCHIVE_STREAM,
    _GROUP,
    _IN_STREAM,
)

# Re-export default so tests can import _CLAIM_IDLE_MS from main.
# Runtime code uses cfg.recovery_claim_idle_ms instead.
_CLAIM_IDLE_MS: int = 60_000


async def _consume_dlq(
    r: aioredis.Redis,
    consumer: str,
    count: int,
    block: int,
    *,
    claim_idle_ms: int = _CLAIM_IDLE_MS,
) -> list[tuple[str, DLQEnvelope]]:
    """Read DLQ entries as DLQEnvelopes via consume_typed()."""
    return await consume_typed(
        r,
        _IN_STREAM,
        _GROUP,
        consumer,
        deserializer=DLQEnvelope.from_redis_fields,
        count=count,
        block=block,
        autoclaim_min_idle_ms=claim_idle_ms,
    )


async def _write_archive(
    r: aioredis.Redis,
    dlq: DLQEnvelope,
    cfg: Config,
) -> None:
    """Write a single DLQ entry to the archive stream (capped)."""
    await r.xadd(
        _ARCHIVE_STREAM,
        dlq.to_redis_fields(),  # type: ignore[arg-type]
        maxlen=cfg.recovery_archive_maxlen,
        approximate=True,
    )


def _archive_with_match_status(
    pipe: aioredis.client.Pipeline,
    dlq: DLQEnvelope,
    match_id: str,
    cfg: Config,
) -> None:
    """Build a Redis pipeline that archives a DLQ entry and marks the match as failed."""
    match_key = f"match:{match_id}"
    pipe.xadd(
        _ARCHIVE_STREAM,
        dlq.to_redis_fields(),  # type: ignore[arg-type]
        maxlen=cfg.recovery_archive_maxlen,
        approximate=True,
    )
    pipe.hset(match_key, mapping={"status": "failed"})
    pipe.expire(match_key, cfg.match_data_ttl_seconds)
    pipe.sadd("match:status:failed", match_id)
    pipe.ttl("match:status:failed")


async def _archive(
    r: aioredis.Redis,
    dlq: DLQEnvelope,
    log: logging.Logger,
    cfg: Config,
) -> None:
    match_id: str | None = dlq.payload.get("match_id")
    if match_id:
        async with r.pipeline(transaction=False) as pipe:
            _archive_with_match_status(pipe, dlq, match_id, cfg)
            results = await pipe.execute()
        # Only set TTL when none exists (ttl < 0) — same guard as parser
        failed_ttl: int = results[4]
        if failed_ttl < 0:
            await r.expire("match:status:failed", cfg.match_data_ttl_seconds)
    else:
        await _write_archive(r, dlq, cfg)
    log.warning(
        "archived exhausted DLQ entry",
        extra={"id": dlq.id, "failure_code": dlq.failure_code, "dlq_attempts": dlq.dlq_attempts},
    )


async def _requeue_delayed(
    r: aioredis.Redis,
    dlq: DLQEnvelope,
    delay_ms: int,
    msg_id: str,
) -> None:
    """Store a MessageEnvelope in delayed:messages and ACK the DLQ entry atomically.

    RDB-5: The ZSET member is the envelope ``id`` (~36 bytes) rather than the
    full JSON blob (~500 bytes).  The serialized envelope is stored separately
    in ``delayed:envelope:{id}`` so the Delay Scheduler can look it up.
    """
    # Restore to the original stream with its original type (strip "stream:" prefix)
    original_type = dlq.original_stream.removeprefix("stream:")
    env = MessageEnvelope(
        id=dlq.id,
        source_stream=dlq.original_stream,
        type=original_type,
        payload=dlq.payload,
        max_attempts=dlq.max_attempts,
        attempts=dlq.attempts,
        enqueued_at=dlq.enqueued_at,
        dlq_attempts=dlq.dlq_attempts + 1,
        priority=dlq.priority,
        correlation_id=dlq.correlation_id,
    )
    ready_ms = int(time.time() * 1000) + delay_ms
    envelope_data = json.dumps(env.to_redis_fields())
    envelope_key = f"delayed:envelope:{env.id}"
    async with r.pipeline(transaction=True) as pipe:
        await pipe.zadd(DELAYED_MESSAGES_KEY, {env.id: ready_ms})
        await pipe.hset(envelope_key, "data", envelope_data)
        await pipe.xack(_IN_STREAM, _GROUP, msg_id)
        await pipe.execute()


def _backoff_ms(
    dlq_attempts: int,
    cfg: Config | None = None,
) -> int:
    default = [5_000, 15_000, 60_000, 300_000]
    backoff_schedule = cfg.recovery_backoff_ms if cfg is not None else default
    idx = min(dlq_attempts, len(backoff_schedule) - 1)
    base = backoff_schedule[idx]
    # R3: Jitter — multiply by 0.5..1.5 to avoid thundering herd
    return int(base * (0.5 + random.random()))  # noqa: S311


async def _handle_transient(
    r: aioredis.Redis,
    cfg: Config,
    msg_id: str,
    dlq: DLQEnvelope,
    log: logging.Logger,
) -> None:
    fc = dlq.failure_code
    if dlq.dlq_attempts >= cfg.dlq_max_attempts:
        await _archive(r, dlq, log, cfg)
        await r.xack(_IN_STREAM, _GROUP, msg_id)
    else:
        delay = (
            dlq.retry_after_ms
            if fc == "http_429" and dlq.retry_after_ms
            else _backoff_ms(dlq.dlq_attempts, cfg)
        )
        await _requeue_delayed(r, dlq, delay, msg_id)
        log.info(
            "requeued with delay",
            extra={
                "id": dlq.id,
                "failure_code": fc,
                "delay_ms": delay,
                "dlq_attempts": dlq.dlq_attempts + 1,
            },
        )


async def _handle_404(
    r: aioredis.Redis,
    cfg: Config,
    msg_id: str,
    dlq: DLQEnvelope,
    log: logging.Logger,
) -> None:
    log.info("404 — permanent discard", extra={"id": dlq.id, "payload": dlq.payload})
    await r.xack(_IN_STREAM, _GROUP, msg_id)


async def _handle_parse_error(
    r: aioredis.Redis,
    cfg: Config,
    msg_id: str,
    dlq: DLQEnvelope,
    log: logging.Logger,
) -> None:
    log.warning(
        "parse_error — archiving for operator review",
        extra={"id": dlq.id, "payload": dlq.payload},
    )
    await _archive(r, dlq, log, cfg)
    await r.xack(_IN_STREAM, _GROUP, msg_id)


_HANDLERS = {
    "http_429": _handle_transient,
    "http_5xx": _handle_transient,
    "http_404": _handle_404,
    "parse_error": _handle_parse_error,
    "handler_crash": _handle_transient,
}


async def _process(
    r: aioredis.Redis,
    cfg: Config,
    consumer: str,
    msg_id: str,
    dlq: DLQEnvelope,
    log: logging.Logger,
) -> bool:
    """Process a DLQ entry. Returns True if the message was handled (ACK'd or requeued)."""
    fc = dlq.failure_code

    # 403 is always handled immediately, regardless of halt state
    if fc == "http_403":
        await r.set("system:halted", "1")
        log.critical("403 in DLQ — system halted, archiving", extra={"id": dlq.id})
        await _archive(r, dlq, log, cfg)
        await r.xack(_IN_STREAM, _GROUP, msg_id)
        return True

    if await is_system_halted(r):
        log.info(
            "system halted — leaving DLQ entry in PEL",
            extra={"id": dlq.id, "failure_code": fc},
        )
        return False

    handler = _HANDLERS.get(fc)
    if handler:
        await handler(r, cfg, msg_id, dlq, log)
    else:
        log.error(
            "unknown failure_code — archiving for operator review",
            extra={"id": dlq.id, "failure_code": fc},
        )
        await _archive(r, dlq, log, cfg)
        await r.xack(_IN_STREAM, _GROUP, msg_id)
    return True


async def main() -> None:
    """Recovery worker loop — continues even when system:halted."""
    shutdown_event = asyncio.Event()

    log = get_logger("recovery")
    cfg = Config()
    r = get_redis(cfg.redis_url)
    consumer = consumer_id()

    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, OSError):
        loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)

    log.info("recovery started", extra={"consumer": consumer})
    try:
        while not shutdown_event.is_set():
            try:
                any_handled = False
                for msg_id, dlq in await _consume_dlq(
                    r,
                    consumer,
                    count=cfg.recovery_count,
                    block=cfg.recovery_block_ms,
                    claim_idle_ms=cfg.recovery_claim_idle_ms,
                ):
                    handled = await _process(r, cfg, consumer, msg_id, dlq, log)
                    any_handled = any_handled or handled
                if not any_handled and await is_system_halted(r):
                    await asyncio.sleep(cfg.recovery_halt_sleep_s)
            except (RedisError, OSError):
                log.exception("consume error — retrying in 1s")
                await asyncio.sleep(1)
        log.info("SIGTERM received — shutting down gracefully")
    finally:
        await r.aclose()
