"""Recovery service — processes DLQ entries, requeues or archives them."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import time

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from lol_pipeline.redis_client import get_redis
from lol_pipeline.streams import consume_typed
from redis.exceptions import RedisError

MATCH_DATA_TTL_SECONDS: int = int(os.getenv("MATCH_DATA_TTL_SECONDS", "604800"))

_IN_STREAM = "stream:dlq"
_ARCHIVE_STREAM = "stream:dlq:archive"
_DELAYED_KEY = "delayed:messages"
_GROUP = "recovery"
_CLAIM_IDLE_MS = 60_000

# Exponential backoff delays (ms) indexed by dlq_attempts
_BACKOFF_MS = [5_000, 15_000, 60_000, 300_000]


async def _consume_dlq(
    r: aioredis.Redis,
    consumer: str,
    count: int = 10,
    block: int = 5000,
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
        autoclaim_min_idle_ms=_CLAIM_IDLE_MS,
    )


async def _archive(
    r: aioredis.Redis,
    dlq: DLQEnvelope,
    log: logging.Logger,
) -> None:
    await r.xadd(_ARCHIVE_STREAM, dlq.to_redis_fields(), maxlen=50_000, approximate=True)  # type: ignore[arg-type]
    match_id: str | None = dlq.payload.get("match_id")
    if match_id:
        await r.hset(f"match:{match_id}", mapping={"status": "failed"})  # type: ignore[misc]
        await r.expire(f"match:{match_id}", MATCH_DATA_TTL_SECONDS)
        await r.sadd("match:status:failed", match_id)  # type: ignore[misc]
        await r.expire("match:status:failed", 7776000)  # 90 days
    log.warning(
        "archived exhausted DLQ entry",
        extra={"id": dlq.id, "failure_code": dlq.failure_code, "dlq_attempts": dlq.dlq_attempts},
    )


async def _requeue_delayed(
    r: aioredis.Redis,
    dlq: DLQEnvelope,
    delay_ms: int,
) -> None:
    """Store a MessageEnvelope in delayed:messages for the Delay Scheduler to pick up."""
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
    )
    ready_ms = int(time.time() * 1000) + delay_ms
    member = json.dumps(env.to_redis_fields())
    await r.zadd(_DELAYED_KEY, {member: ready_ms})


def _backoff_ms(dlq_attempts: int) -> int:
    idx = min(dlq_attempts, len(_BACKOFF_MS) - 1)
    return _BACKOFF_MS[idx]


async def _handle_transient(
    r: aioredis.Redis,
    cfg: Config,
    msg_id: str,
    dlq: DLQEnvelope,
    log: logging.Logger,
) -> None:
    fc = dlq.failure_code
    if dlq.dlq_attempts >= cfg.dlq_max_attempts:
        await _archive(r, dlq, log)
    else:
        delay = (
            dlq.retry_after_ms
            if fc == "http_429" and dlq.retry_after_ms
            else _backoff_ms(dlq.dlq_attempts)
        )
        await _requeue_delayed(r, dlq, delay)
        log.info(
            "requeued with delay",
            extra={
                "id": dlq.id,
                "failure_code": fc,
                "delay_ms": delay,
                "dlq_attempts": dlq.dlq_attempts + 1,
            },
        )
    await r.xack(_IN_STREAM, _GROUP, msg_id)


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
    await _archive(r, dlq, log)
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
) -> None:
    fc = dlq.failure_code

    # 403 is always handled immediately, regardless of halt state
    if fc == "http_403":
        await r.set("system:halted", "1")
        log.critical("403 in DLQ — system halted, archiving", extra={"id": dlq.id})
        await _archive(r, dlq, log)
        await r.xack(_IN_STREAM, _GROUP, msg_id)
        return

    if await r.get("system:halted"):
        log.info(
            "system halted — leaving DLQ entry in PEL",
            extra={"id": dlq.id, "failure_code": fc},
        )
        return

    handler = _HANDLERS.get(fc)
    if handler:
        await handler(r, cfg, msg_id, dlq, log)
    else:
        log.error(
            "unknown failure_code — archiving for operator review",
            extra={"id": dlq.id, "failure_code": fc},
        )
        await _archive(r, dlq, log)
        await r.xack(_IN_STREAM, _GROUP, msg_id)


async def main() -> None:
    """Recovery worker loop — continues even when system:halted."""
    shutdown_event = asyncio.Event()

    log = get_logger("recovery")
    cfg = Config()
    r = get_redis(cfg.redis_url)
    consumer = f"{socket.gethostname()}-{os.getpid()}"

    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, OSError):
        loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)

    log.info("recovery started", extra={"consumer": consumer})
    try:
        while not shutdown_event.is_set():
            try:
                for msg_id, dlq in await _consume_dlq(r, consumer):
                    await _process(r, cfg, consumer, msg_id, dlq, log)
            except RedisError, OSError:
                log.exception("consume error — retrying in 1s")
                await asyncio.sleep(1)
        log.info("SIGTERM received — shutting down gracefully")
    finally:
        await r.aclose()
