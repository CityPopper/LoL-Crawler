"""Recovery service — processes DLQ entries, requeues or archives them."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import time
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from lol_pipeline.redis_client import get_redis
from redis.exceptions import ResponseError

_IN_STREAM = "stream:dlq"
_ARCHIVE_STREAM = "stream:dlq:archive"
_DELAYED_KEY = "delayed:messages"
_GROUP = "recovery"

# Exponential backoff delays (ms) indexed by dlq_attempts
_BACKOFF_MS = [5_000, 15_000, 60_000, 300_000]


async def _ensure_group(r: aioredis.Redis) -> None:
    with contextlib.suppress(ResponseError):
        await r.xgroup_create(_IN_STREAM, _GROUP, id="0", mkstream=True)


async def _consume_dlq(
    r: aioredis.Redis,
    consumer: str,
    count: int = 10,
    block: int = 5000,
) -> list[tuple[str, DLQEnvelope]]:
    """Read DLQ entries as DLQEnvelopes, draining own PEL first."""
    await _ensure_group(r)

    # Drain own PEL first (stranded entries from crash/halt).
    # Note: Redis 7 returns [["stream", []]] (truthy!) when PEL is empty, so we
    # must check actual message count rather than the truthiness of the outer list.
    pending: list[Any] = await r.xreadgroup(_GROUP, consumer, {_IN_STREAM: "0"}, count=count)
    pel_messages: list[tuple[str, DLQEnvelope]] = [
        (msg_id, DLQEnvelope.from_redis_fields(fields))
        for _, entries in (pending or [])
        for msg_id, fields in entries
    ]
    if pel_messages:
        return pel_messages

    raw: list[Any] = await r.xreadgroup(
        _GROUP, consumer, {_IN_STREAM: ">"}, count=count, block=block
    )
    return [
        (msg_id, DLQEnvelope.from_redis_fields(fields))
        for _, entries in (raw or [])
        for msg_id, fields in entries
    ]


async def _archive(
    r: aioredis.Redis,
    dlq: DLQEnvelope,
    log: logging.Logger,
) -> None:
    await r.xadd(_ARCHIVE_STREAM, dlq.to_redis_fields())  # type: ignore[arg-type]
    match_id: str | None = dlq.payload.get("match_id")
    if match_id:
        await r.hset(f"match:{match_id}", mapping={"status": "failed"})  # type: ignore[misc]
        await r.sadd("match:status:failed", match_id)  # type: ignore[misc]
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
        attempts=0,
        enqueued_at=dlq.enqueued_at,
        dlq_attempts=dlq.dlq_attempts + 1,
    )
    ready_ms = int(time.time() * 1000) + delay_ms
    member = json.dumps(env.to_redis_fields())
    await r.zadd(_DELAYED_KEY, {member: ready_ms})


def _backoff_ms(dlq_attempts: int) -> int:
    idx = min(dlq_attempts, len(_BACKOFF_MS) - 1)
    return _BACKOFF_MS[idx]


async def _process(
    r: aioredis.Redis,
    cfg: Config,
    consumer: str,
    msg_id: str,
    dlq: DLQEnvelope,
    log: logging.Logger,
) -> None:
    halted = bool(await r.get("system:halted"))
    fc = dlq.failure_code

    if fc == "http_403":
        # Always handle 403 regardless of halt state
        await r.set("system:halted", "1")
        log.critical("403 in DLQ — system halted, archiving", extra={"id": dlq.id})
        await _archive(r, dlq, log)
        await r.xack(_IN_STREAM, _GROUP, msg_id)
        return

    if halted:
        # Leave recoverable entries unACKed while halted; they stay in PEL
        log.info(
            "system halted — leaving DLQ entry in PEL",
            extra={"id": dlq.id, "failure_code": fc},
        )
        return

    if fc in ("http_429", "http_5xx"):
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
        return

    if fc == "http_404":
        log.info("404 — permanent discard", extra={"id": dlq.id, "payload": dlq.payload})
        await r.xack(_IN_STREAM, _GROUP, msg_id)
        return

    if fc == "parse_error":
        log.warning(
            "parse_error — archiving for operator review",
            extra={"id": dlq.id, "payload": dlq.payload},
        )
        await _archive(r, dlq, log)
        await r.xack(_IN_STREAM, _GROUP, msg_id)
        return

    # unknown failure code — archive so the entry is not silently lost
    log.error(
        "unknown failure_code — archiving for operator review",
        extra={"id": dlq.id, "failure_code": fc},
    )
    await _archive(r, dlq, log)
    await r.xack(_IN_STREAM, _GROUP, msg_id)


async def main() -> None:
    """Recovery worker loop — continues even when system:halted."""
    log = get_logger("recovery")
    cfg = Config()
    r = get_redis(cfg.redis_url)
    consumer = f"{socket.gethostname()}-{os.getpid()}"

    log.info("recovery started", extra={"consumer": consumer})
    try:
        while True:
            for msg_id, dlq in await _consume_dlq(r, consumer):
                await _process(r, cfg, consumer, msg_id, dlq, log)
    finally:
        await r.aclose()
