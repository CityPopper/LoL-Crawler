"""Unit tests for defer_message() — FP-1."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest

from lol_pipeline.constants import DELAYED_MESSAGES_KEY, STREAM_DLQ
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import _DEFER_MAX_COUNT, consume, publish


@pytest.fixture(autouse=True)
def _clear_ensured_cache():
    """Clear the _ensure_group cache before each test."""
    from lol_pipeline.streams import _ensured

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


_STREAM = "stream:test"
_GROUP = "test-group"


async def _setup_message(r, envelope):
    """Publish and consume so the message is in the PEL."""
    await publish(r, _STREAM, envelope)
    msgs = await consume(r, _STREAM, _GROUP, "test-consumer", block=0)
    assert len(msgs) == 1
    return msgs[0][0]


class TestDeferMessageNormal:
    """Normal case: ZSCORE exists, envelope hash exists, message ACK'd."""

    @pytest.mark.asyncio
    async def test_defer_message__zscore_exists_at_expected_time(self, r):
        from lol_pipeline.streams import defer_message

        env = _env()
        msg_id = await _setup_message(r, env)
        delay_ms = 30_000
        before = int(time.time() * 1000)

        await defer_message(r, msg_id, env, _STREAM, _GROUP, delay_ms=delay_ms)

        after = int(time.time() * 1000)
        score = await r.zscore(DELAYED_MESSAGES_KEY, env.id)
        assert score is not None
        assert before + delay_ms <= score <= after + delay_ms

    @pytest.mark.asyncio
    async def test_defer_message__envelope_hash_has_data_field(self, r):
        from lol_pipeline.streams import defer_message

        env = _env()
        msg_id = await _setup_message(r, env)

        await defer_message(r, msg_id, env, _STREAM, _GROUP)

        data = await r.hget(f"delayed:envelope:{env.id}", "data")
        assert data is not None
        parsed = json.loads(data)
        assert parsed["id"] == env.id

    @pytest.mark.asyncio
    async def test_defer_message__message_acked(self, r):
        from lol_pipeline.streams import defer_message

        env = _env()
        msg_id = await _setup_message(r, env)

        await defer_message(r, msg_id, env, _STREAM, _GROUP)

        pending = await r.xpending(_STREAM, _GROUP)
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_defer_message__envelope_hash_has_ttl(self, r):
        from lol_pipeline.streams import defer_message

        env = _env()
        msg_id = await _setup_message(r, env)

        await defer_message(r, msg_id, env, _STREAM, _GROUP, envelope_ttl=3600)

        ttl = await r.ttl(f"delayed:envelope:{env.id}")
        assert 0 < ttl <= 3600


class TestDeferMessageAttemptsUnchanged:
    """envelope.attempts must not be modified by defer_message."""

    @pytest.mark.asyncio
    async def test_defer_message__attempts_unchanged(self, r):
        from lol_pipeline.streams import defer_message

        env = _env(attempts=2)
        msg_id = await _setup_message(r, env)

        await defer_message(r, msg_id, env, _STREAM, _GROUP)

        data = await r.hget(f"delayed:envelope:{env.id}", "data")
        parsed = json.loads(data)
        assert parsed["attempts"] == "2"
        # Original envelope object unchanged
        assert env.attempts == 2


class TestDeferMessageCrashSafety:
    """If pipeline.execute() fails, message stays in PEL."""

    @pytest.mark.asyncio
    async def test_defer_message__exec_fails__message_in_pel(self, r):
        from lol_pipeline.streams import defer_message

        env = _env()
        msg_id = await _setup_message(r, env)

        # Mock pipeline to raise on execute
        with patch.object(r, "pipeline") as mock_pipeline:
            mock_pipe = AsyncMock()
            mock_pipe.__aenter__ = AsyncMock(return_value=mock_pipe)
            mock_pipe.__aexit__ = AsyncMock(return_value=False)
            mock_pipe.execute = AsyncMock(side_effect=Exception("EXEC failed"))
            mock_pipeline.return_value = mock_pipe

            with pytest.raises(Exception, match="EXEC failed"):
                await defer_message(r, msg_id, env, _STREAM, _GROUP)

        # Message must still be in PEL (not ACK'd)
        pending = await r.xpending(_STREAM, _GROUP)
        assert pending["pending"] == 1


class TestDeferMessageCap:
    """REV-2: defer_count cap routes to DLQ instead of re-deferring."""

    @pytest.mark.asyncio
    async def test_defer_message__at_cap__routes_to_dlq(self, r):
        """Message with defer_count=_DEFER_MAX_COUNT routes to DLQ."""
        from lol_pipeline.streams import defer_message

        env = _env(defer_count=_DEFER_MAX_COUNT)
        msg_id = await _setup_message(r, env)

        await defer_message(r, msg_id, env, _STREAM, _GROUP)

        # Must NOT be in delayed:messages
        score = await r.zscore(DELAYED_MESSAGES_KEY, env.id)
        assert score is None

        # Must be in stream:dlq with failure_code="deferred_too_long"
        dlq_entries = await r.xrange(STREAM_DLQ)
        assert len(dlq_entries) == 1
        dlq_fields = dlq_entries[0][1]
        assert dlq_fields["failure_code"] == "deferred_too_long"
        assert dlq_fields["original_stream"] == _STREAM

        # Original message must be ACK'd
        pending = await r.xpending(_STREAM, _GROUP)
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_defer_message__below_cap__still_defers(self, r):
        """Message with defer_count=_DEFER_MAX_COUNT-1 still defers normally."""
        from lol_pipeline.streams import defer_message

        env = _env(defer_count=_DEFER_MAX_COUNT - 1)
        msg_id = await _setup_message(r, env)

        await defer_message(r, msg_id, env, _STREAM, _GROUP)

        # Must be in delayed:messages
        score = await r.zscore(DELAYED_MESSAGES_KEY, env.id)
        assert score is not None

        # stream:dlq must be empty
        dlq_len = await r.xlen(STREAM_DLQ)
        assert dlq_len == 0

        # defer_count on the envelope must be incremented
        assert env.defer_count == _DEFER_MAX_COUNT

        # Stored envelope data must reflect the incremented count
        data = await r.hget(f"delayed:envelope:{env.id}", "data")
        parsed = json.loads(data)
        assert parsed["defer_count"] == str(_DEFER_MAX_COUNT)
