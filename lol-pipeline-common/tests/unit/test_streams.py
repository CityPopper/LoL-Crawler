"""Unit tests for lol_pipeline.streams."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import ack, consume, nack_to_dlq, publish


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


def _env(stream: str = "stream:test", **kwargs) -> MessageEnvelope:
    return MessageEnvelope(
        source_stream=stream, type="test", payload={"k": "v"}, max_attempts=5, **kwargs,
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
            r, env,
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
            source_stream="stream:test", type="test",
            payload={"match_id": "NA1_999"}, max_attempts=5,
        )
        await nack_to_dlq(
            r, env,
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
