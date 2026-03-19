"""Unit tests for lol_pipeline.streams."""

from __future__ import annotations

from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from redis.exceptions import RedisError

from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import _DEFAULT_MAXLEN, ack, consume, nack_to_dlq, publish


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


class TestEnsureGroupErrors:
    @pytest.mark.asyncio
    async def test_unexpected_error_propagates(self):
        """Non-ResponseError exceptions from xgroup_create propagate."""
        from lol_pipeline.streams import _ensure_group

        mock_r = AsyncMock()
        mock_r.xgroup_create.side_effect = ConnectionError("redis down")
        with pytest.raises(ConnectionError, match="redis down"):
            await _ensure_group(mock_r, "stream:test", "g")


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


class TestNackToDlqErrors:
    @pytest.mark.asyncio
    async def test_nack_to_dlq__xadd_raises__propagates(self):
        """If DLQ XADD fails, the error propagates."""
        mock_r = AsyncMock()
        mock_r.xadd.side_effect = RedisError("connection lost")
        env = _env()
        with pytest.raises(RedisError):
            await nack_to_dlq(mock_r, env, "http_429", "fetcher", "123-0")
