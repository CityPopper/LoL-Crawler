"""Unit tests for lol_pipeline.streams."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from redis.exceptions import RedisError, ResponseError

from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import (
    _DEFAULT_MAXLEN,
    ANALYZE_STREAM_MAXLEN,
    MATCH_ID_STREAM_MAXLEN,
    _maxlen_for_replay,
    ack,
    consume,
    consume_typed,
    nack_to_dlq,
    publish,
    replay_from_dlq,
)


@pytest.fixture(autouse=True)
def _clear_ensured_cache():
    """Clear the _ensure_group cache before each test to avoid cross-test pollution."""
    from lol_pipeline.streams import _ensured

    # WeakKeyDictionary: clear all entries
    for key in list(_ensured):
        _ensured.pop(key, None)
    yield
    for key in list(_ensured):
        _ensured.pop(key, None)


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


def _env(stream: str = "stream:test", **kwargs) -> MessageEnvelope:
    return MessageEnvelope(
        source_stream=stream,
        type="test",
        payload={"k": "v"},
        max_attempts=5,
        **kwargs,
    )


class TestPublish:
    @pytest.mark.asyncio
    async def test_publish_adds_to_stream(self, r):
        env = _env()
        msg_id = await publish(r, "stream:test", env)
        assert msg_id is not None
        length = await r.xlen("stream:test")
        assert length == 1

    @pytest.mark.asyncio
    async def test_published_fields_deserialize(self, r):
        env = _env()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "g", "c", block=0)
        assert len(msgs) == 1
        _, restored = msgs[0]
        assert restored.payload == {"k": "v"}
        assert restored.id == env.id


class TestPublishMaxlen:
    @pytest.mark.asyncio
    async def test_publish_default_maxlen_constant(self):
        """Default maxlen is 10_000."""
        assert _DEFAULT_MAXLEN == 10_000

    @pytest.mark.asyncio
    async def test_publish_passes_maxlen_to_xadd(self):
        """publish() passes maxlen and approximate=True to r.xadd."""
        mock_r = AsyncMock()
        mock_r.xadd.return_value = "1-0"
        env = _env()
        await publish(mock_r, "stream:test", env)
        call_kwargs = mock_r.xadd.call_args
        assert call_kwargs[1]["maxlen"] == _DEFAULT_MAXLEN
        assert call_kwargs[1]["approximate"] is True

    @pytest.mark.asyncio
    async def test_publish_custom_maxlen(self):
        """publish() accepts custom maxlen parameter."""
        mock_r = AsyncMock()
        mock_r.xadd.return_value = "1-0"
        env = _env()
        await publish(mock_r, "stream:test", env, maxlen=500)
        call_kwargs = mock_r.xadd.call_args
        assert call_kwargs[1]["maxlen"] == 500

    @pytest.mark.asyncio
    async def test_publish_with_maxlen_still_works(self, r):
        """publish() with maxlen still adds to stream and is consumable."""
        env = _env()
        msg_id = await publish(r, "stream:test", env, maxlen=100)
        assert msg_id is not None
        length = await r.xlen("stream:test")
        assert length == 1


class TestPublishMaxlenNone:
    """I2-H3: publish() with maxlen=None omits MAXLEN from XADD (no trimming)."""

    @pytest.mark.asyncio
    async def test_publish_maxlen_none__no_maxlen_kwarg(self):
        """When maxlen=None, xadd is called without maxlen or approximate."""
        mock_r = AsyncMock()
        mock_r.xadd.return_value = "1-0"
        env = _env()
        await publish(mock_r, "stream:test", env, maxlen=None)
        call_kwargs = mock_r.xadd.call_args
        assert "maxlen" not in call_kwargs[1]
        assert "approximate" not in call_kwargs[1]

    @pytest.mark.asyncio
    async def test_publish_maxlen_none__still_adds_to_stream(self, r):
        """publish() with maxlen=None adds to stream and is consumable."""
        env = _env()
        msg_id = await publish(r, "stream:test", env, maxlen=None)
        assert msg_id is not None
        length = await r.xlen("stream:test")
        assert length == 1

    @pytest.mark.asyncio
    async def test_publish_maxlen_none__no_trimming(self, r):
        """Stream grows unbounded when maxlen=None (no approximate trim)."""
        for _i in range(50):
            env = _env()
            await publish(r, "stream:unbounded", env, maxlen=None)
        assert await r.xlen("stream:unbounded") == 50


class TestStreamMaxlenConstants:
    """I2-H3/H4: exported constants for per-stream maxlen policy."""

    def test_match_id_stream_maxlen_is_bounded(self):
        """stream:match_id should have a large but bounded maxlen."""
        assert MATCH_ID_STREAM_MAXLEN == 500_000

    def test_analyze_stream_maxlen_is_50k(self):
        """stream:analyze should have 50_000 maxlen."""
        assert ANALYZE_STREAM_MAXLEN == 50_000

    def test_default_maxlen_unchanged(self):
        """Default maxlen remains 10_000."""
        assert _DEFAULT_MAXLEN == 10_000


class TestConsume:
    @pytest.mark.asyncio
    async def test_consume_returns_empty_on_no_messages(self, r):
        msgs = await consume(r, "stream:empty", "g", "c", block=0)
        assert msgs == []

    @pytest.mark.asyncio
    async def test_consume_multiple_messages(self, r):
        """consume returns multiple messages when available."""
        for _ in range(3):
            await publish(r, "stream:test", _env())
        msgs = await consume(r, "stream:test", "g", "c", block=0, count=10)
        assert len(msgs) == 3

    @pytest.mark.asyncio
    async def test_consume_after_ack_returns_empty(self, r):
        env = _env()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "g", "c", block=0)
        await ack(r, "stream:test", "g", msgs[0][0])
        msgs2 = await consume(r, "stream:test", "g", "c", block=0)
        assert msgs2 == []


class TestAck:
    @pytest.mark.asyncio
    async def test_ack_removes_from_pel(self, r):
        env = _env()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "g", "c", block=0)
        msg_id = msgs[0][0]
        await ack(r, "stream:test", "g", msg_id)
        # After ACK, PEL should be empty
        pending = await r.xpending(r"stream:test", "g")
        assert pending["pending"] == 0


class TestNackToDlq:
    @pytest.mark.asyncio
    async def test_nack_writes_to_dlq_stream(self, r):
        env = _env()
        await nack_to_dlq(
            r,
            env,
            failure_code="http_429",
            failed_by="fetcher",
            original_message_id="123-0",
            failure_reason="rate limited",
        )
        length = await r.xlen("stream:dlq")
        assert length == 1

    @pytest.mark.asyncio
    async def test_nack_preserves_envelope_fields(self, r):
        env = MessageEnvelope(
            source_stream="stream:test",
            type="test",
            payload={"match_id": "NA1_999"},
            max_attempts=5,
        )
        await nack_to_dlq(
            r,
            env,
            failure_code="http_5xx",
            failed_by="fetcher",
            original_message_id="456-0",
            retry_after_ms=5000,
        )
        entries = await r.xrange("stream:dlq")
        assert len(entries) == 1
        fields = entries[0][1]
        assert fields["failure_code"] == "http_5xx"
        assert fields["failed_by"] == "fetcher"
        assert fields["original_message_id"] == "456-0"
        assert fields["retry_after_ms"] == "5000"
        # T16-3: original_stream reflects the envelope's source_stream;
        # source_stream on the DLQ entry itself is "stream:dlq".
        assert fields["original_stream"] == "stream:test"
        assert fields["source_stream"] == "stream:dlq"


class TestEnsureGroup:
    @pytest.mark.asyncio
    async def test_creates_group_and_stream(self, r):
        """_ensure_group creates both stream and group if neither exist."""
        from lol_pipeline.streams import _ensure_group

        await _ensure_group(r, "stream:new", "new-group")
        # Should be able to consume from it now
        msgs = await consume(r, "stream:new", "new-group", "c", block=0)
        assert msgs == []

    @pytest.mark.asyncio
    async def test_idempotent_group_creation(self, r):
        """Calling _ensure_group twice does not raise."""
        from lol_pipeline.streams import _ensure_group

        await _ensure_group(r, "stream:test", "g")
        await _ensure_group(r, "stream:test", "g")  # no error


class TestEnsureGroupCaching:
    """P2: _ensure_group is only called once per (stream, group) pair."""

    @pytest.mark.asyncio
    async def test_ensure_group_called_once_per_pair(self):
        """After the first consume(), subsequent calls skip _ensure_group."""
        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [["stream:test", []]]

        # First consume: _ensure_group should be called
        await consume(mock_r, "stream:cached", "g", "c", block=0)
        first_call_count = mock_r.xgroup_create.call_count

        # Second consume: _ensure_group should be skipped (cached)
        await consume(mock_r, "stream:cached", "g", "c", block=0)
        second_call_count = mock_r.xgroup_create.call_count

        # xgroup_create should only have been called during the first consume
        assert first_call_count > 0
        assert second_call_count == first_call_count, (
            f"xgroup_create called {second_call_count - first_call_count} extra times. "
            "Expected _ensure_group to be cached after first call."
        )


class TestEnsureGroupErrors:
    @pytest.mark.asyncio
    async def test_unexpected_error_propagates(self):
        """Non-ResponseError exceptions from xgroup_create propagate."""
        from lol_pipeline.streams import _ensure_group

        mock_r = AsyncMock()
        mock_r.xgroup_create.side_effect = ConnectionError("redis down")
        with pytest.raises(ConnectionError, match="redis down"):
            await _ensure_group(mock_r, "stream:test", "g")

    @pytest.mark.asyncio
    async def test_busygroup_error__suppressed(self):
        """BUSYGROUP ResponseError is suppressed (group already exists)."""
        from lol_pipeline.streams import _ensure_group

        mock_r = AsyncMock()
        mock_r.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        await _ensure_group(mock_r, "stream:test", "g")  # should not raise

    @pytest.mark.asyncio
    async def test_non_busygroup_response_error__re_raised(self):
        """Non-BUSYGROUP ResponseError (e.g. WRONGTYPE, OOM) is re-raised."""
        from lol_pipeline.streams import _ensure_group

        mock_r = AsyncMock()
        mock_r.xgroup_create.side_effect = ResponseError(
            "WRONGTYPE Operation against a key holding the wrong kind of value"
        )
        with pytest.raises(ResponseError, match="WRONGTYPE"):
            await _ensure_group(mock_r, "stream:test", "g")


class TestNogroupCacheInvalidation:
    """P13-DBG-3: NOGROUP ResponseError from xreadgroup clears the _ensured cache."""

    @pytest.mark.asyncio
    async def test_nogroup_error_clears_cache(self):
        """After a NOGROUP xreadgroup error the cache entry is discarded."""
        from lol_pipeline.streams import _ensured

        mock_r = AsyncMock()
        # First call: xgroup_create succeeds (BUSYGROUP suppressed)
        mock_r.xgroup_create.side_effect = ResponseError("BUSYGROUP already exists")
        # xreadgroup raises NOGROUP (Redis restarted, group gone)
        mock_r.xreadgroup.side_effect = ResponseError(
            "NOGROUP No such consumer group 'g' for key name 'stream:test'"
        )

        with pytest.raises(ResponseError, match="NOGROUP"):
            await consume(mock_r, "stream:test", "g", "c", block=0)

        # Cache must be cleared so next consume() re-creates the group
        pairs = _ensured.get(mock_r)
        assert pairs is None or ("stream:test", "g") not in pairs

    @pytest.mark.asyncio
    async def test_nogroup_error_reraises(self):
        """NOGROUP ResponseError propagates to caller even after clearing cache."""
        mock_r = AsyncMock()
        mock_r.xgroup_create.side_effect = ResponseError("BUSYGROUP already exists")
        mock_r.xreadgroup.side_effect = ResponseError("NOGROUP No such consumer group")

        with pytest.raises(ResponseError, match="NOGROUP"):
            await consume(mock_r, "stream:test", "g", "c", block=0)

    @pytest.mark.asyncio
    async def test_non_nogroup_response_error_does_not_clear_cache(self):
        """Non-NOGROUP ResponseError from xreadgroup leaves the cache intact."""
        from lol_pipeline.streams import _ensured

        mock_r = AsyncMock()
        # Successful group creation
        mock_r.xgroup_create.return_value = "OK"
        # xreadgroup raises a different ResponseError
        mock_r.xreadgroup.side_effect = ResponseError("WRONGTYPE wrong kind of value")

        with pytest.raises(ResponseError, match="WRONGTYPE"):
            await consume(mock_r, "stream:test", "g", "c", block=0)

        # Cache should still contain the entry (not cleared for unrelated errors)
        pairs = _ensured.get(mock_r)
        assert pairs is not None and ("stream:test", "g") in pairs

    @pytest.mark.asyncio
    async def test_invalidate_ensured_removes_entry(self):
        """_invalidate_ensured removes the (stream, group) from cache."""
        from lol_pipeline.streams import _ensured, _invalidate_ensured

        mock_r = AsyncMock()
        _ensured[mock_r] = {("stream:a", "g"), ("stream:b", "h")}

        _invalidate_ensured(mock_r, "stream:a", "g")

        pairs = _ensured.get(mock_r)
        assert pairs is not None
        assert ("stream:a", "g") not in pairs
        assert ("stream:b", "h") in pairs  # other entry untouched

    @pytest.mark.asyncio
    async def test_invalidate_ensured_no_error_when_not_cached(self):
        """_invalidate_ensured is a no-op when the client has no cache entry."""
        from lol_pipeline.streams import _ensured, _invalidate_ensured

        mock_r = AsyncMock()
        _invalidate_ensured(mock_r, "stream:x", "g")  # should not raise

        # Client should have no entry in _ensured (was never cached)
        assert mock_r not in _ensured


class TestPublishErrors:
    @pytest.mark.asyncio
    async def test_publish__xadd_raises__propagates(self):
        """XADD failure (e.g. connection lost) propagates to caller."""
        mock_r = AsyncMock()
        mock_r.xadd.side_effect = RedisError("connection lost")
        env = _env()
        with pytest.raises(RedisError):
            await publish(mock_r, "stream:test", env)


class TestConsumeErrors:
    @pytest.mark.asyncio
    async def test_consume__xreadgroup_raises__propagates(self):
        """XREADGROUP failure propagates to caller."""
        mock_r = AsyncMock()
        mock_r.xreadgroup.side_effect = RedisError("connection lost")
        with pytest.raises(RedisError):
            await consume(mock_r, "stream:test", "g", "c", block=0)

    @pytest.mark.asyncio
    async def test_consume__xautoclaim_raises__propagates(self):
        """XAUTOCLAIM failure propagates to caller."""
        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [["stream:test", []]]
        mock_r.xautoclaim.side_effect = RedisError("connection lost")
        with pytest.raises(RedisError):
            await consume(mock_r, "stream:test", "g", "c", block=0, autoclaim_min_idle_ms=5000)


class TestAckEdgeCases:
    @pytest.mark.asyncio
    async def test_ack__nonexistent_message__no_error(self, r):
        """ACKing a non-existent message ID does not raise."""
        from lol_pipeline.streams import _ensure_group

        await _ensure_group(r, "stream:test", "g")
        await ack(r, "stream:test", "g", "999999-0")


class TestCorruptMessageHandling:
    """CQ-14: corrupt messages in consume() are acked and skipped."""

    @pytest.mark.asyncio
    async def test_consume__corrupt_entry_acked_and_skipped(self, r):
        """A message with missing required fields is acked and not returned."""
        # Publish a valid message
        env = _env()
        await publish(r, "stream:test", env)
        # Inject a corrupt entry directly (missing 'payload' and other fields)
        await r.xadd("stream:test", {"garbage": "data"})

        # First consume: gets both messages. Corrupt one is acked + skipped.
        msgs = await consume(r, "stream:test", "g", "c", block=0, count=10)
        # Only the valid message should be returned
        assert len(msgs) == 1
        assert msgs[0][1].id == env.id

        # Ack the valid one
        await ack(r, "stream:test", "g", msgs[0][0])

        # Second consume: should get nothing (corrupt was already acked)
        msgs2 = await consume(r, "stream:test", "g", "c", block=0)
        assert msgs2 == []


class TestNackToDlqPriorityAndEnqueuedAt:
    """Sprint 5: nack_to_dlq preserves priority and enqueued_at from source envelope."""

    @pytest.mark.asyncio
    async def test_nack_to_dlq__preserves_priority_field(self, r):
        """DLQ envelope inherits priority from the source MessageEnvelope."""
        env = MessageEnvelope(
            source_stream="stream:match_id",
            type="match_id",
            payload={"match_id": "NA1_999"},
            max_attempts=5,
            priority="high",
        )
        await nack_to_dlq(
            r,
            env,
            failure_code="http_429",
            failed_by="fetcher",
            original_message_id="123-0",
        )
        entries = await r.xrange("stream:dlq")
        assert len(entries) == 1
        fields = entries[0][1]
        assert fields["priority"] == "high"

    @pytest.mark.asyncio
    async def test_nack_to_dlq__preserves_enqueued_at(self, r):
        """DLQ envelope inherits enqueued_at from the source MessageEnvelope (CQ-23)."""
        original_ts = "2024-06-15T12:00:00+00:00"
        env = MessageEnvelope(
            source_stream="stream:parse",
            type="parse",
            payload={"match_id": "NA1_111"},
            max_attempts=5,
            enqueued_at=original_ts,
        )
        await nack_to_dlq(
            r,
            env,
            failure_code="http_5xx",
            failed_by="parser",
            original_message_id="456-0",
        )
        entries = await r.xrange("stream:dlq")
        assert len(entries) == 1
        fields = entries[0][1]
        assert fields["enqueued_at"] == original_ts

    @pytest.mark.asyncio
    async def test_nack_to_dlq__default_priority_is_normal(self, r):
        """When source envelope has default priority, DLQ envelope also gets 'normal'."""
        env = _env()
        await nack_to_dlq(
            r,
            env,
            failure_code="http_429",
            failed_by="fetcher",
            original_message_id="789-0",
        )
        entries = await r.xrange("stream:dlq")
        fields = entries[0][1]
        assert fields["priority"] == "normal"


class TestNackToDlqDlqAttempts:
    """I2-C1: nack_to_dlq must propagate dlq_attempts from the source envelope."""

    @pytest.mark.asyncio
    async def test_nack_to_dlq__preserves_nonzero_dlq_attempts(self, r):
        """DLQ envelope inherits dlq_attempts from the source MessageEnvelope."""
        env = MessageEnvelope(
            source_stream="stream:match_id",
            type="match_id",
            payload={"match_id": "NA1_555"},
            max_attempts=5,
            dlq_attempts=3,
        )
        await nack_to_dlq(
            r,
            env,
            failure_code="http_5xx",
            failed_by="fetcher",
            original_message_id="100-0",
        )
        entries = await r.xrange("stream:dlq")
        assert len(entries) == 1
        fields = entries[0][1]
        assert fields["dlq_attempts"] == "3"

    @pytest.mark.asyncio
    async def test_nack_to_dlq__default_dlq_attempts_is_zero(self, r):
        """When source envelope has default dlq_attempts=0, DLQ envelope also gets 0."""
        env = _env()
        await nack_to_dlq(
            r,
            env,
            failure_code="http_429",
            failed_by="fetcher",
            original_message_id="200-0",
        )
        entries = await r.xrange("stream:dlq")
        fields = entries[0][1]
        assert fields["dlq_attempts"] == "0"


class TestNackToDlqErrors:
    @pytest.mark.asyncio
    async def test_nack_to_dlq__xadd_raises__propagates(self):
        """If DLQ XADD fails, the error propagates."""
        mock_r = AsyncMock()
        mock_r.xadd.side_effect = RedisError("connection lost")
        env = _env()
        with pytest.raises(RedisError):
            await nack_to_dlq(mock_r, env, "http_429", "fetcher", "123-0")


class TestXautoclaimCorruptMessages:
    """XAUTOCLAIM path handles corrupt and nil entries without crashing."""

    @pytest.mark.asyncio
    async def test_xautoclaim__corrupt_entry_acked_and_skipped(self):
        """Corrupt message in XAUTOCLAIM is ACKed and skipped (not re-raised)."""
        mock_r = AsyncMock()
        # PEL drain returns empty
        mock_r.xreadgroup.return_value = [["stream:test", []]]
        # XAUTOCLAIM returns one corrupt entry (missing required fields)
        mock_r.xautoclaim.return_value = [
            "0-0",  # cursor
            [("corrupt-1", {"garbage": "data"})],  # entries
        ]

        msgs = await consume(mock_r, "stream:test", "g", "c", block=0, autoclaim_min_idle_ms=5000)

        # No valid messages returned
        assert msgs == []
        # Corrupt message was ACKed
        mock_r.xack.assert_called_once_with("stream:test", "g", "corrupt-1")

    @pytest.mark.asyncio
    async def test_xautoclaim__json_decode_error_acked_and_skipped(self):
        """Entry with invalid JSON payload in XAUTOCLAIM is ACKed and skipped."""
        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [["stream:test", []]]
        # Entry has all required keys but payload is not valid JSON
        mock_r.xautoclaim.return_value = [
            "0-0",
            [
                (
                    "bad-json-1",
                    {
                        "id": "abc",
                        "source_stream": "stream:test",
                        "type": "test",
                        "payload": "NOT-JSON{{{",
                        "attempts": "0",
                        "max_attempts": "5",
                        "enqueued_at": "2024-01-01T00:00:00+00:00",
                    },
                )
            ],
        ]

        msgs = await consume(mock_r, "stream:test", "g", "c", block=0, autoclaim_min_idle_ms=5000)

        assert msgs == []
        mock_r.xack.assert_called_once_with("stream:test", "g", "bad-json-1")

    @pytest.mark.asyncio
    async def test_xautoclaim__nil_fields_skipped_without_ack(self):
        """Entries with nil/empty fields (deleted entries) are skipped without ACK."""
        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [["stream:test", []]]
        # XAUTOCLAIM returns a deleted entry (nil body = empty dict/None)
        mock_r.xautoclaim.return_value = [
            "0-0",
            [("deleted-1", {}), ("deleted-2", None)],
        ]

        msgs = await consume(mock_r, "stream:test", "g", "c", block=0, autoclaim_min_idle_ms=5000)

        # No messages returned and no ACK for nil entries
        assert msgs == []
        mock_r.xack.assert_not_called()

    @pytest.mark.asyncio
    async def test_xautoclaim__mix_valid_corrupt_nil(self):
        """XAUTOCLAIM with a mix of valid, corrupt, and nil entries returns only valid."""
        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [["stream:test", []]]

        valid_env = _env()
        valid_fields = valid_env.to_redis_fields()

        mock_r.xautoclaim.return_value = [
            "0-0",
            [
                ("nil-1", {}),  # nil - skip
                ("valid-1", valid_fields),  # valid - return
                ("corrupt-1", {"garbage": "data"}),  # corrupt - ack + skip
                ("nil-2", None),  # nil - skip
            ],
        ]

        msgs = await consume(mock_r, "stream:test", "g", "c", block=0, autoclaim_min_idle_ms=5000)

        # Only the valid message is returned
        assert len(msgs) == 1
        assert msgs[0][0] == "valid-1"
        assert msgs[0][1].id == valid_env.id
        # Only the corrupt one was ACKed (not nil entries)
        mock_r.xack.assert_called_once_with("stream:test", "g", "corrupt-1")


class TestXautoclaimCorruptHandlerNotCalled:
    """Consume with XAUTOCLAIM: corrupt messages are ACKed, handler never invoked."""

    @pytest.mark.asyncio
    async def test_xautoclaim__all_corrupt__falls_through_to_new_messages(self):
        """When all XAUTOCLAIM entries are corrupt, consume falls through to new messages."""
        mock_r = AsyncMock()
        # PEL drain returns empty
        mock_r.xreadgroup.side_effect = [
            [["stream:test", []]],  # PEL drain: empty
            [["stream:test", []]],  # new messages: also empty
        ]
        # XAUTOCLAIM returns two corrupt entries
        mock_r.xautoclaim.return_value = [
            "0-0",
            [
                ("corrupt-a", {"bad": "fields"}),
                ("corrupt-b", {"also": "bad"}),
            ],
        ]

        msgs = await consume(mock_r, "stream:test", "g", "c", block=0, autoclaim_min_idle_ms=5000)

        # No valid messages returned
        assert msgs == []
        # Both corrupt messages were ACKed
        assert mock_r.xack.call_count == 2
        mock_r.xack.assert_any_call("stream:test", "g", "corrupt-a")
        mock_r.xack.assert_any_call("stream:test", "g", "corrupt-b")
        # Falls through to new messages xreadgroup
        assert mock_r.xreadgroup.call_count == 2

    @pytest.mark.asyncio
    async def test_xautoclaim__valid_and_corrupt_mixed__only_valid_returned(self):
        """XAUTOCLAIM returns mix of valid and corrupt; only valid ones reach caller."""
        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [["stream:test", []]]

        valid_env_1 = _env()
        valid_env_2 = _env()

        mock_r.xautoclaim.return_value = [
            "0-0",
            [
                ("valid-1", valid_env_1.to_redis_fields()),
                ("corrupt-1", {"garbage": "data"}),
                ("valid-2", valid_env_2.to_redis_fields()),
                ("corrupt-2", {"more": "garbage"}),
            ],
        ]

        msgs = await consume(mock_r, "stream:test", "g", "c", block=0, autoclaim_min_idle_ms=5000)

        # Only valid messages returned
        assert len(msgs) == 2
        assert msgs[0][0] == "valid-1"
        assert msgs[0][1].id == valid_env_1.id
        assert msgs[1][0] == "valid-2"
        assert msgs[1][1].id == valid_env_2.id
        # Corrupt messages were ACKed
        assert mock_r.xack.call_count == 2
        mock_r.xack.assert_any_call("stream:test", "g", "corrupt-1")
        mock_r.xack.assert_any_call("stream:test", "g", "corrupt-2")


class TestEnsureGroupWeakKeyIsolation:
    """_ensure_group WeakKeyDictionary keeps separate caches per Redis client."""

    @pytest.mark.asyncio
    async def test_separate_clients_get_separate_caches(self):
        """A call on client A does not affect client B's cache."""
        mock_a = AsyncMock()
        mock_a.xreadgroup.return_value = [["stream:test", []]]

        mock_b = AsyncMock()
        mock_b.xreadgroup.return_value = [["stream:test", []]]

        # Consume on client A
        await consume(mock_a, "stream:test", "g", "c", block=0)
        assert mock_a.xgroup_create.call_count == 1

        # Consume on client B — should still call xgroup_create (not cached from A)
        await consume(mock_b, "stream:test", "g", "c", block=0)
        assert mock_b.xgroup_create.call_count == 1

    @pytest.mark.asyncio
    async def test_xgroup_create_called_once_per_client_stream_group(self):
        """xgroup_create is called only once per (client, stream, group) even across
        multiple consume() calls."""
        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [["stream:test", []]]

        # Three consume() calls on same (client, stream, group)
        await consume(mock_r, "stream:test", "g", "c", block=0)
        await consume(mock_r, "stream:test", "g", "c", block=0)
        await consume(mock_r, "stream:test", "g", "c", block=0)

        assert mock_r.xgroup_create.call_count == 1

    @pytest.mark.asyncio
    async def test_different_stream_group_pairs_each_create(self):
        """Different (stream, group) pairs on the same client each trigger xgroup_create."""
        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [["stream:a", []]]

        await consume(mock_r, "stream:a", "g1", "c", block=0)
        await consume(mock_r, "stream:b", "g2", "c", block=0)
        await consume(mock_r, "stream:a", "g1", "c", block=0)  # cached
        await consume(mock_r, "stream:b", "g2", "c", block=0)  # cached

        assert mock_r.xgroup_create.call_count == 2


@dataclass
class _SimpleMsg:
    """Minimal test type for consume_typed() custom deserializer."""

    name: str
    value: int

    def to_redis_fields(self) -> dict[str, str]:
        return {"name": self.name, "value": str(self.value)}

    @classmethod
    def from_redis_fields(cls, fields: dict[str, Any]) -> _SimpleMsg:
        return cls(name=fields["name"], value=int(fields["value"]))


class TestConsumeTyped:
    """consume_typed() accepts a custom deserializer and returns typed results."""

    @pytest.mark.asyncio
    async def test_consume_typed__custom_deserializer(self, r):
        """consume_typed() uses the provided deserializer instead of MessageEnvelope."""
        # Publish raw fields matching _SimpleMsg
        await r.xadd("stream:typed", {"name": "alice", "value": "42"})

        results = await consume_typed(
            r,
            "stream:typed",
            "g",
            "c",
            deserializer=_SimpleMsg.from_redis_fields,
            count=10,
            block=0,
        )

        assert len(results) == 1
        _msg_id, msg = results[0]
        assert isinstance(msg, _SimpleMsg)
        assert msg.name == "alice"
        assert msg.value == 42

    @pytest.mark.asyncio
    async def test_consume_typed__corrupt_entry_acked_and_skipped(self, r):
        """Corrupt entries are acked and skipped when using a custom deserializer."""
        # Add a valid entry
        await r.xadd("stream:typed", {"name": "bob", "value": "7"})
        # Add a corrupt entry (missing "value" field)
        await r.xadd("stream:typed", {"garbage": "data"})

        results = await consume_typed(
            r,
            "stream:typed",
            "g",
            "c",
            deserializer=_SimpleMsg.from_redis_fields,
            count=10,
            block=0,
        )

        # Only valid message returned
        assert len(results) == 1
        assert results[0][1].name == "bob"

    @pytest.mark.asyncio
    async def test_consume_typed__returns_empty_on_no_messages(self, r):
        """consume_typed() returns empty list when no messages are available."""
        results = await consume_typed(
            r,
            "stream:empty",
            "g",
            "c",
            deserializer=_SimpleMsg.from_redis_fields,
            count=10,
            block=0,
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_consume_typed__pel_drain(self):
        """consume_typed() drains own PEL before reading new messages."""
        mock_r = AsyncMock()

        valid_fields = {"name": "charlie", "value": "99"}
        # PEL drain returns the unacked message
        mock_r.xreadgroup.return_value = [
            ["stream:typed", [("msg-1", valid_fields)]],
        ]

        results = await consume_typed(
            mock_r,
            "stream:typed",
            "g",
            "c",
            deserializer=_SimpleMsg.from_redis_fields,
            count=10,
            block=0,
        )

        assert len(results) == 1
        assert results[0][1].name == "charlie"
        assert results[0][1].value == 99

    @pytest.mark.asyncio
    async def test_consume_typed__autoclaim_support(self):
        """consume_typed() supports autoclaim_min_idle_ms parameter."""
        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [["stream:test", []]]

        valid_fields = {"name": "dana", "value": "5"}
        mock_r.xautoclaim.return_value = [
            "0-0",
            [("claimed-1", valid_fields)],
        ]

        results = await consume_typed(
            mock_r,
            "stream:test",
            "g",
            "c",
            deserializer=_SimpleMsg.from_redis_fields,
            count=10,
            block=0,
            autoclaim_min_idle_ms=5000,
        )

        assert len(results) == 1
        assert results[0][1].name == "dana"
        assert results[0][1].value == 5


class TestConsumeUsesConsumeTyped:
    """consume() delegates to consume_typed() with MessageEnvelope.from_redis_fields."""

    @pytest.mark.asyncio
    async def test_consume__still_returns_message_envelopes(self, r):
        """consume() continues to return MessageEnvelope tuples (backward compat)."""
        env = _env()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "g", "c", block=0)
        assert len(msgs) == 1
        _, restored = msgs[0]
        assert isinstance(restored, MessageEnvelope)
        assert restored.id == env.id


class TestMaxlenForReplay:
    """_maxlen_for_replay() returns per-stream MAXLEN policy."""

    def test_returns_maxlen_for_match_id_stream(self):
        """stream:match_id uses its bounded MAXLEN for replay."""
        assert MATCH_ID_STREAM_MAXLEN == 500_000
        assert _maxlen_for_replay("stream:match_id") == 500_000

    def test_returns_analyze_maxlen_for_analyze_stream(self):
        assert _maxlen_for_replay("stream:analyze") == ANALYZE_STREAM_MAXLEN

    def test_returns_default_for_puuid_stream(self):
        assert _maxlen_for_replay("stream:puuid") == _DEFAULT_MAXLEN

    def test_returns_default_for_parse_stream(self):
        assert _maxlen_for_replay("stream:parse") == _DEFAULT_MAXLEN

    def test_returns_default_for_unknown_stream(self):
        assert _maxlen_for_replay("stream:unknown") == _DEFAULT_MAXLEN


class TestReplayFromDlq:
    """replay_from_dlq() atomically moves a DLQ entry to its target stream."""

    @pytest.mark.asyncio
    async def test_dispatches_to_target_and_removes_from_dlq(self, r):
        """Message appears in target stream and is removed from stream:dlq."""
        from lol_pipeline.constants import STREAM_DLQ

        env = _env(stream="stream:puuid")
        dlq_id = await r.xadd(STREAM_DLQ, env.to_redis_fields())

        await replay_from_dlq(r, dlq_id, "stream:puuid", env)

        assert await r.xlen(STREAM_DLQ) == 0
        assert await r.xlen("stream:puuid") == 1

    @pytest.mark.asyncio
    async def test_replayed_message_is_consumable(self, r):
        """Message replayed into target stream round-trips through consume()."""
        from lol_pipeline.constants import STREAM_DLQ

        env = _env(stream="stream:puuid")
        dlq_id = await r.xadd(STREAM_DLQ, env.to_redis_fields())

        await replay_from_dlq(r, dlq_id, "stream:puuid", env)

        msgs = await consume(r, "stream:puuid", "g", "c", block=0)
        assert len(msgs) == 1
        _, restored = msgs[0]
        assert restored.id == env.id
        assert restored.payload == env.payload

    @pytest.mark.asyncio
    async def test_only_target_dlq_entry_removed(self, r):
        """Only the specified DLQ entry is deleted; others remain."""
        from lol_pipeline.constants import STREAM_DLQ

        env1 = _env(stream="stream:puuid")
        env2 = _env(stream="stream:puuid")
        dlq_id1 = await r.xadd(STREAM_DLQ, env1.to_redis_fields())
        await r.xadd(STREAM_DLQ, env2.to_redis_fields())

        await replay_from_dlq(r, dlq_id1, "stream:puuid", env1)

        assert await r.xlen(STREAM_DLQ) == 1
        assert await r.xlen("stream:puuid") == 1
