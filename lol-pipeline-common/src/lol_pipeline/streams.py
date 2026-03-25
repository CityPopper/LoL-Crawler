"""Stream operations: publish, consume, ack, nack_to_dlq, replay_from_dlq."""

from __future__ import annotations

import json
import logging
import weakref
from collections.abc import Callable
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from lol_pipeline._streams_data import (
    _DEFAULT_MAXLEN as _DEFAULT_MAXLEN,
)
from lol_pipeline._streams_data import (
    _REPLAY_LUA,
    _REPLAY_MAXLEN_MAP,
)
from lol_pipeline._streams_data import (
    ANALYZE_STREAM_MAXLEN as ANALYZE_STREAM_MAXLEN,
)
from lol_pipeline._streams_data import (
    MATCH_ID_STREAM_MAXLEN as MATCH_ID_STREAM_MAXLEN,
)
from lol_pipeline.constants import STREAM_DLQ, STREAM_DLQ_ARCHIVE
from lol_pipeline.models import DLQEnvelope, MessageEnvelope

_log = logging.getLogger("streams")

# Cache of (stream, group) pairs for which _ensure_group has already succeeded.
# Uses a WeakKeyDictionary keyed on the Redis client so that different connections
# (e.g., in tests) don't share state and entries are cleaned up when the client is GC'd.
_ensured: weakref.WeakKeyDictionary[aioredis.Redis, set[tuple[str, str]]] = (
    weakref.WeakKeyDictionary()
)


async def publish(
    r: aioredis.Redis,
    stream: str,
    envelope: MessageEnvelope,
    maxlen: int | None = _DEFAULT_MAXLEN,
) -> str:
    """XADD envelope to stream with approximate trimming; return the Redis entry ID.

    When *maxlen* is ``None`` the stream is never trimmed (use for bursty
    streams where trimming would silently drop undelivered messages).
    """
    fields: dict[str, Any] = envelope.to_redis_fields()
    if maxlen is None:
        return await r.xadd(stream, fields)  # type: ignore[no-any-return,arg-type]
    return await r.xadd(stream, fields, maxlen=maxlen, approximate=True)  # type: ignore[no-any-return,arg-type]


async def _ensure_group(r: aioredis.Redis, stream: str, group: str) -> None:
    pairs = _ensured.get(r)
    key = (stream, group)
    if pairs is not None and key in pairs:
        return
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    if pairs is None:
        pairs = set()
        _ensured[r] = pairs
    pairs.add(key)


def _invalidate_ensured(r: aioredis.Redis, stream: str, group: str) -> None:
    """Remove (stream, group) from the _ensured cache after a NOGROUP error."""
    pairs = _ensured.get(r)
    if pairs is not None:
        pairs.discard((stream, group))


async def _archive_corrupt(
    r: aioredis.Redis,
    stream: str,
    msg_id: str,
    fields: dict[str, Any],
    error: str,
) -> None:
    """Write a corrupt message to stream:dlq:archive for audit, then log a warning.

    This preserves an audit trail for messages that cannot be deserialized.
    The raw fields are stored as-is so operators can inspect and diagnose.
    """
    archive_fields: dict[str, str] = {
        "failure_code": "corrupt_message",
        "failure_reason": error,
        "original_stream": stream,
        "original_message_id": msg_id,
        "raw_fields": json.dumps({str(k): str(v) for k, v in fields.items()}),
    }
    await r.xadd(
        STREAM_DLQ_ARCHIVE,
        archive_fields,  # type: ignore[arg-type]
        maxlen=50_000,
        approximate=True,
    )
    _log.warning(
        "corrupt message — archived and acking",
        extra={"msg_id": msg_id, "stream": stream, "error": error},
    )


async def _deserialize_entries_typed[T](
    r: aioredis.Redis,
    stream: str,
    group: str,
    raw_entries: list[Any],
    deserializer: Callable[[dict[str, Any]], T],
) -> list[tuple[str, T]]:
    """Deserialize raw xreadgroup entries with a custom deserializer, acking corrupt messages."""
    result: list[tuple[str, T]] = []
    for _, entries in raw_entries or []:
        for msg_id, fields in entries:
            try:
                env = deserializer(fields)
                result.append((msg_id, env))
            except (KeyError, json.JSONDecodeError, ValueError, TypeError) as exc:
                await _archive_corrupt(r, stream, msg_id, fields, str(exc))
                await r.xack(stream, group, msg_id)
    return result


async def consume_typed[T](  # noqa: PLR0913
    r: aioredis.Redis,
    stream: str,
    group: str,
    consumer: str,
    deserializer: Callable[[dict[str, Any]], T],
    count: int = 10,
    block: int = 5000,
    autoclaim_min_idle_ms: int | None = None,
) -> list[tuple[str, T]]:
    """Read up to *count* messages from *stream* via consumer group, deserializing
    each entry with the provided *deserializer* callable.

    On each call, first drains this consumer's own PEL (messages delivered but
    not yet acked -- e.g. stranded after a crash or system halt).  Then, if
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
    try:
        pending: list[Any] = await r.xreadgroup(group, consumer, {stream: "0"}, count=count)
    except ResponseError as exc:
        if "NOGROUP" in str(exc):
            # Redis restarted and the consumer group is gone — invalidate the cache
            # so _ensure_group recreates it on the next call instead of being skipped.
            _invalidate_ensured(r, stream, group)
        raise
    pel_messages = await _deserialize_entries_typed(r, stream, group, pending, deserializer)
    if pel_messages:
        return pel_messages

    # XAUTOCLAIM: reclaim idle messages from other consumers (dead workers).
    # Single call per consume(); the service loop handles further iterations.
    if autoclaim_min_idle_ms is not None:
        result: Any = await r.xautoclaim(
            stream, group, consumer, autoclaim_min_idle_ms, start_id="0-0", count=count
        )
        claimed_entries = result[1]  # xautoclaim returns [cursor, entries]
        claimed: list[tuple[str, T]] = []
        for msg_id, fields in claimed_entries:
            if not fields:  # skip deleted entries (nil bodies)
                continue
            try:
                env = deserializer(fields)
                claimed.append((msg_id, env))
            except (KeyError, json.JSONDecodeError, ValueError, TypeError) as exc:
                await _archive_corrupt(r, stream, msg_id, fields, str(exc))
                await r.xack(stream, group, msg_id)
        if claimed:
            return claimed

    # PEL is empty — block-wait for new messages.
    raw: list[Any] = await r.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block)
    return await _deserialize_entries_typed(r, stream, group, raw, deserializer)


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

    Convenience wrapper around :func:`consume_typed` that deserializes entries
    as :class:`MessageEnvelope`.  See :func:`consume_typed` for full semantics.
    """
    return await consume_typed(
        r,
        stream,
        group,
        consumer,
        deserializer=MessageEnvelope.from_redis_fields,
        count=count,
        block=block,
        autoclaim_min_idle_ms=autoclaim_min_idle_ms,
    )


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
        source_stream=STREAM_DLQ,
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
        dlq_attempts=envelope.dlq_attempts,
        priority=envelope.priority,
        correlation_id=envelope.correlation_id,
    )
    fields: dict[str, Any] = dlq.to_redis_fields()
    await r.xadd(STREAM_DLQ, fields, maxlen=50_000, approximate=True)  # type: ignore[arg-type]


def _maxlen_for_replay(stream: str) -> int:
    """Return the MAXLEN to use when replaying to *stream*."""
    return _REPLAY_MAXLEN_MAP.get(stream, _DEFAULT_MAXLEN)


def maxlen_for_stream(stream: str) -> int | None:
    """Return the configured MAXLEN for *stream*, or ``None`` if unknown."""
    result = _REPLAY_MAXLEN_MAP.get(stream)
    return result


async def replay_from_dlq(
    r: aioredis.Redis,
    dlq_entry_id: str,
    target_stream: str,
    envelope: MessageEnvelope,
) -> int:
    """Atomically XADD *envelope* to *target_stream* and XDEL *dlq_entry_id* from
    stream:dlq in a single Lua script call.

    Returns ``1`` when the replay succeeds, ``0`` when the DLQ entry no longer
    exists (already replayed — idempotent guard against crash-restart duplicates).
    """
    redis_fields = envelope.to_redis_fields()
    ml = _maxlen_for_replay(target_stream)
    flat_args: list[str] = [dlq_entry_id, str(ml)]
    for k, v in redis_fields.items():
        flat_args.append(str(k))
        flat_args.append(str(v))
    result: int = await r.eval(  # type: ignore[misc]
        _REPLAY_LUA,
        2,
        target_stream,
        STREAM_DLQ,
        *flat_args,
    )
    return result
