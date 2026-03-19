"""Stream operations: publish, consume, ack, nack_to_dlq."""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from lol_pipeline.models import DLQEnvelope, MessageEnvelope

_log = logging.getLogger("streams")

_DLQ_STREAM = "stream:dlq"


async def publish(
    r: aioredis.Redis,
    stream: str,
    envelope: MessageEnvelope,
) -> str:
    """XADD envelope to stream; return the Redis entry ID."""
    fields: dict[str, Any] = envelope.to_redis_fields()
    return await r.xadd(stream, fields)  # type: ignore[no-any-return,arg-type]


async def _ensure_group(r: aioredis.Redis, stream: str, group: str) -> None:
    with contextlib.suppress(ResponseError):
        await r.xgroup_create(stream, group, id="0", mkstream=True)


async def _deserialize_entries(
    r: aioredis.Redis,
    stream: str,
    group: str,
    raw_entries: list[Any],
) -> list[tuple[str, MessageEnvelope]]:
    """Deserialize raw xreadgroup entries, acking corrupt messages."""
    result: list[tuple[str, MessageEnvelope]] = []
    for _, entries in raw_entries or []:
        for msg_id, fields in entries:
            try:
                env = MessageEnvelope.from_redis_fields(fields)
                result.append((msg_id, env))
            except (KeyError, json.JSONDecodeError, ValueError, TypeError) as exc:
                _log.warning(
                    "corrupt message — acking and skipping",
                    extra={"msg_id": msg_id, "stream": stream, "error": str(exc)},
                )
                await r.xack(stream, group, msg_id)
    return result


async def consume(
    r: aioredis.Redis,
    stream: str,
    group: str,
    consumer: str,
    count: int = 10,
    block: int = 5000,
    autoclaim_min_idle_ms: int | None = None,
) -> list[tuple[str, MessageEnvelope]]:
    """Read up to count messages from stream via consumer group.

    On each call, first drains this consumer's own PEL (messages delivered but
    not yet acked — e.g. stranded after a crash or system halt).  Then, if
    ``autoclaim_min_idle_ms`` is set, runs XAUTOCLAIM to reclaim messages idle
    longer than that threshold from ANY consumer in the group (handles dead
    workers).  Only after both are empty does it block-wait for new messages.

    Corrupt entries that fail deserialization are logged and ACKed (removed from
    the PEL) to prevent infinite retry loops.
    """
    await _ensure_group(r, stream, group)

    # Drain own PEL first (id="0" returns already-delivered, unacked messages).
    # Note: Redis 7 returns [["stream", []]] (truthy!) when PEL is empty, so we
    # must check actual message count rather than the truthiness of the outer list.
    pending: list[Any] = await r.xreadgroup(group, consumer, {stream: "0"}, count=count)
    pel_messages = await _deserialize_entries(r, stream, group, pending)
    if pel_messages:
        return pel_messages

    # XAUTOCLAIM: reclaim idle messages from other consumers (dead workers).
    # Single call per consume(); the service loop handles further iterations.
    if autoclaim_min_idle_ms is not None:
        result: Any = await r.xautoclaim(
            stream, group, consumer, autoclaim_min_idle_ms, start_id="0-0", count=count
        )
        claimed_entries = result[1]  # xautoclaim returns [cursor, entries]
        claimed: list[tuple[str, MessageEnvelope]] = []
        for msg_id, fields in claimed_entries:
            if not fields:  # skip deleted entries (nil bodies)
                continue
            try:
                env = MessageEnvelope.from_redis_fields(fields)
                claimed.append((msg_id, env))
            except (KeyError, json.JSONDecodeError, ValueError, TypeError) as exc:
                _log.warning(
                    "corrupt message — acking and skipping",
                    extra={"msg_id": msg_id, "stream": stream, "error": str(exc)},
                )
                await r.xack(stream, group, msg_id)
        if claimed:
            return claimed

    # PEL is empty — block-wait for new messages.
    raw: list[Any] = await r.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block)
    return await _deserialize_entries(r, stream, group, raw)


async def ack(r: aioredis.Redis, stream: str, group: str, msg_id: str) -> None:
    """Acknowledge a message so it is removed from the PEL."""
    await r.xack(stream, group, msg_id)


async def nack_to_dlq(
    r: aioredis.Redis,
    envelope: MessageEnvelope,
    failure_code: str,
    failed_by: str,
    original_message_id: str,
    failure_reason: str = "",
    retry_after_ms: int | None = None,
) -> None:
    """Write a DLQEnvelope to stream:dlq without ACKing the source message."""
    dlq = DLQEnvelope(
        id=envelope.id,
        source_stream=_DLQ_STREAM,
        type="dlq",
        payload=envelope.payload,
        attempts=envelope.attempts,
        max_attempts=envelope.max_attempts,
        failure_code=failure_code,
        failure_reason=failure_reason or failure_code,
        failed_by=failed_by,
        original_stream=envelope.source_stream,
        original_message_id=original_message_id,
        retry_after_ms=retry_after_ms,
        enqueued_at=envelope.enqueued_at,
        priority=envelope.priority,
    )
    fields: dict[str, Any] = dlq.to_redis_fields()
    await r.xadd(_DLQ_STREAM, fields)  # type: ignore[arg-type]
