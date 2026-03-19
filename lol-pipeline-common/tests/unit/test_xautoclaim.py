"""Test that consume() uses XAUTOCLAIM to reclaim stranded messages from other consumers."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import ack, consume, publish


@pytest.fixture
async def r() -> fakeredis.aioredis.FakeRedis:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestXautoclaim:
    @pytest.mark.asyncio
    async def test_reclaims_idle_message_from_dead_consumer(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        """A message pending under a dead consumer should be reclaimed by a new consumer."""
        stream = "stream:test"
        group = "test-group"

        env = MessageEnvelope(
            source_stream=stream,
            type="test",
            payload={"key": "value"},
            max_attempts=5,
        )
        await publish(r, stream, env)

        # Old consumer reads the message but doesn't ACK
        old_msgs = await consume(r, stream, group, "old-worker", block=0)
        assert len(old_msgs) == 1

        # New consumer calls consume — should reclaim via XAUTOCLAIM
        # min_idle_ms=0 means claim everything regardless of idle time
        new_msgs = await consume(r, stream, group, "new-worker", block=0, autoclaim_min_idle_ms=0)
        assert len(new_msgs) == 1
        assert new_msgs[0][1].payload == {"key": "value"}

        # ACK the message via new consumer
        await ack(r, stream, group, new_msgs[0][0])

    @pytest.mark.asyncio
    async def test_no_autoclaim_when_disabled(self, r: fakeredis.aioredis.FakeRedis) -> None:
        """When autoclaim_min_idle_ms is None (default), no reclaim happens."""
        stream = "stream:test2"
        group = "test-group2"

        env = MessageEnvelope(
            source_stream=stream,
            type="test",
            payload={"key": "value"},
            max_attempts=5,
        )
        await publish(r, stream, env)

        # Old consumer reads but doesn't ACK
        old_msgs = await consume(r, stream, group, "old-worker", block=0)
        assert len(old_msgs) == 1

        # New consumer calls consume without autoclaim — should NOT get the message
        new_msgs = await consume(r, stream, group, "new-worker", block=0)
        assert len(new_msgs) == 0
