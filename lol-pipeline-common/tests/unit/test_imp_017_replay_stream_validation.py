"""IMP-017: replay_from_dlq rejects invalid target streams."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import replay_from_dlq


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


def _envelope() -> MessageEnvelope:
    return MessageEnvelope(
        source_stream="stream:match_id",
        type="match_id",
        payload={"match_id": "NA1_123", "region": "na1"},
        max_attempts=5,
    )


class TestReplayStreamValidation:
    async def test_invalid_stream_raises_value_error(self, r):
        """replay_from_dlq raises ValueError for an unknown target stream."""
        env = _envelope()
        with pytest.raises(ValueError, match="invalid replay target stream"):
            await replay_from_dlq(r, "0-0", "stream:evil", env)

    async def test_empty_stream_raises_value_error(self, r):
        """Empty string target stream is rejected."""
        env = _envelope()
        with pytest.raises(ValueError, match="invalid replay target stream"):
            await replay_from_dlq(r, "0-0", "", env)

    async def test_dlq_stream_itself_rejected(self, r):
        """Cannot replay to stream:dlq itself."""
        env = _envelope()
        with pytest.raises(ValueError, match="invalid replay target stream"):
            await replay_from_dlq(r, "0-0", "stream:dlq", env)

    async def test_valid_streams_accepted(self, r):
        """Valid pipeline streams are accepted (no ValueError)."""
        from lol_pipeline.constants import VALID_REPLAY_STREAMS

        env = _envelope()
        for stream in VALID_REPLAY_STREAMS:
            # These will return 0 (DLQ entry doesn't exist) but should NOT raise
            result = await replay_from_dlq(r, "0-0", stream, env)
            assert result == 0
