"""Stream operations: publish, consume, ack, nack_to_dlq, defer_message, replay_from_dlq."""

from __future__ import annotations

import json
import logging
import time
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
    DEFAULT_STREAM_MAXLEN as DEFAULT_STREAM_MAXLEN,
)
from lol_pipeline._streams_data import (
    MATCH_ID_STREAM_MAXLEN as MATCH_ID_STREAM_MAXLEN,
)
from lol_pipeline.constants import (
    DELAYED_MESSAGES_KEY,
    STREAM_DLQ,
    STREAM_DLQ_ARCHIVE,
    VALID_REPLAY_STREAMS,
)
from lol_pipeline.models import DLQEnvelope, MessageEnvelope

_log = logging.getLogger("streams")

# Maximum number of times a message may be deferred before routing to the DLQ.
_DEFER_MAX_COUNT: int = 100

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


async def _drain_own_pel[T](
    r: aioredis.Redis,
    stream: str,
    group: str,
    consumer: str,
    deserializer: Callable[[dict[str, Any]], T],
    count: int,
) -> list[tuple[str, T]]:
    """Drain this consumer's own PEL (messages delivered but not yet ACKed).

    Redis 7 returns ``[["stream", []]]`` (truthy!) when the PEL is empty, so
    the caller must check the returned list length, not its truthiness.
    """
    try:
        pending: list[Any] = await r.xreadgroup(group, consumer, {stream: "0"}, count=count)
    except ResponseError as exc:
        if "NOGROUP" in str(exc):
            _invalidate_ensured(r, stream, group)
        raise
    return await _deserialize_entries_typed(r, stream, group, pending, deserializer)


async def _autoclaim[T](
    r: aioredis.Redis,
    stream: str,
    group: str,
    consumer: str,
    deserializer: Callable[[dict[str, Any]], T],
    count: int,
    min_idle_ms: int,
) -> list[tuple[str, T]]:
    """Reclaim idle messages from dead workers via XAUTOCLAIM."""
    result: Any = await r.xautoclaim(
        stream, group, consumer, min_idle_ms, start_id="0-0", count=count
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
    return claimed


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

    pel_messages = await _drain_own_pel(r, stream, group, consumer, deserializer, count)
    if pel_messages:
        return pel_messages

    if autoclaim_min_idle_ms is not None:
        claimed = await _autoclaim(
            r, stream, group, consumer, deserializer, count, autoclaim_min_idle_ms
        )
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


async def defer_message(  # noqa: PLR0913
    r: aioredis.Redis,
    msg_id: str,
    envelope: MessageEnvelope,
    stream: str,
    group: str,
    delay_ms: int = 30_000,
    envelope_ttl: int | None = None,
) -> None:
    """Defer a message to delayed:messages without DLQ involvement.

    Atomically: ZADD delayed:messages, HSET envelope data, EXPIRE, XACK.
    Does NOT modify envelope.attempts or envelope.dlq_attempts.

    When ``envelope.defer_count`` reaches ``_DEFER_MAX_COUNT`` the message is
    routed to the DLQ with ``failure_code="deferred_too_long"`` instead of
    being re-deferred, preventing infinite deferral loops.
    """
    if envelope.defer_count >= _DEFER_MAX_COUNT:
        await nack_to_dlq(
            r,
            envelope,
            failure_code="deferred_too_long",
            failed_by="defer_message",
            original_message_id=msg_id,
            failure_reason=(
                f"message deferred {envelope.defer_count} times, exceeding cap of"
                f" {_DEFER_MAX_COUNT}"
            ),
        )
        await r.xack(stream, group, msg_id)
        _log.warning(
            "defer cap reached — routed to DLQ",
            extra={
                "msg_id": msg_id,
                "envelope_id": envelope.id,
                "defer_count": envelope.defer_count,
            },
        )
        return

    envelope.defer_count += 1

    if envelope_ttl is None:
        try:
            from lol_pipeline.config import Config

            envelope_ttl = Config().delay_envelope_ttl_seconds
        except Exception:
            envelope_ttl = 86400

    ready_ms = int(time.time() * 1000) + delay_ms
    envelope_data = json.dumps(envelope.to_redis_fields())
    envelope_key = f"delayed:envelope:{envelope.id}"
    async with r.pipeline(transaction=True) as pipe:
        await pipe.zadd(DELAYED_MESSAGES_KEY, {envelope.id: ready_ms})
        await pipe.hset(envelope_key, "data", envelope_data)  # type: ignore[misc]
        await pipe.expire(envelope_key, envelope_ttl)
        await pipe.xack(stream, group, msg_id)
        await pipe.execute()
    _log.debug(
        "deferred message",
        extra={
            "msg_id": msg_id,
            "delay_ms": delay_ms,
            "envelope_id": envelope.id,
            "defer_count": envelope.defer_count,
        },
    )


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
    if not target_stream or target_stream not in VALID_REPLAY_STREAMS:
        raise ValueError(f"invalid replay target stream: {target_stream!r}")
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
