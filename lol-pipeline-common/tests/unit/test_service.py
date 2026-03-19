"""Unit tests for lol_pipeline.service — handler error resilience."""

from __future__ import annotations

import logging

import fakeredis.aioredis
import pytest

from lol_pipeline.models import MessageEnvelope
from lol_pipeline.service import _handle_with_retry, run_consumer
from lol_pipeline.streams import ack, consume, publish


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def log():
    return logging.getLogger("test-service")


_STREAM = "stream:test-svc"
_GROUP = "test-group"


async def _setup(r, payload=None):
    env = MessageEnvelope(
        source_stream=_STREAM,
        type="test",
        payload=payload or {"key": "val"},
        max_attempts=5,
    )
    await publish(r, _STREAM, env)
    msgs = await consume(r, _STREAM, _GROUP, "test-consumer", block=0)
    assert len(msgs) == 1
    return msgs[0]


class TestHandleWithRetry:
    @pytest.mark.asyncio
    async def test_successful_handler_no_dlq(self, r, log):
        """Successful handler call: no DLQ entry, failure counter cleared."""
        msg_id, envelope = await _setup(r)
        failures: dict[str, int] = {}

        async def handler(mid, env):
            pass  # success

        await _handle_with_retry(r, _STREAM, _GROUP, msg_id, envelope, handler, log, failures, 3)

        assert await r.xlen("stream:dlq") == 0
        assert msg_id not in failures

    @pytest.mark.asyncio
    async def test_persistent_crash_nacks_to_dlq(self, r, log):
        """After max_handler_retries consecutive failures, message is nacked to DLQ and ACKed."""
        msg_id, envelope = await _setup(r)
        failures: dict[str, int] = {}

        async def bad_handler(mid, env):
            raise RuntimeError("boom")

        for _ in range(3):
            await _handle_with_retry(
                r, _STREAM, _GROUP, msg_id, envelope, bad_handler, log, failures, 3,
            )

        assert await r.xlen("stream:dlq") == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["failure_code"] == "handler_crash"

    @pytest.mark.asyncio
    async def test_intermittent_failure_resets_counter(self, r, log):
        """A success after failures resets the failure counter."""
        msg_id, envelope = await _setup(r)
        failures: dict[str, int] = {}
        call_count = 0

        async def flaky_handler(mid, env):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("flaky")

        # Two failures
        for _ in range(2):
            await _handle_with_retry(
                r, _STREAM, _GROUP, msg_id, envelope, flaky_handler, log, failures, 3,
            )

        # Third call succeeds
        await _handle_with_retry(
            r, _STREAM, _GROUP, msg_id, envelope, flaky_handler, log, failures, 3,
        )

        assert await r.xlen("stream:dlq") == 0
        assert msg_id not in failures


class TestRunConsumer:
    @pytest.mark.asyncio
    async def test_halted_exits_immediately(self, r, log):
        """run_consumer exits when system:halted is set."""
        await r.set("system:halted", "1")
        call_count = 0
        async def handler(mid, env):
            nonlocal call_count
            call_count += 1
        await run_consumer(r, _STREAM, _GROUP, "c", handler, log)
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_processes_messages_then_halts(self, r, log):
        """Processes available messages, then exits on halt."""
        env = MessageEnvelope(
            source_stream=_STREAM, type="test",
            payload={"key": "val"}, max_attempts=5,
        )
        await publish(r, _STREAM, env)
        processed = []
        async def handler(mid, envelope):
            processed.append(mid)
            await ack(r, _STREAM, _GROUP, mid)
            await r.set("system:halted", "1")
        await run_consumer(r, _STREAM, _GROUP, "c", handler, log)
        assert len(processed) == 1

    @pytest.mark.asyncio
    async def test_consume_error_retries(self, r, log):
        """On consume error, retries after 1s (we mock sleep)."""
        import asyncio
        from unittest.mock import AsyncMock, patch

        call_count = 0
        async def handler(mid, env):
            pass

        original_consume = consume
        async def failing_consume(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Redis gone")
            # On second call, set halted to exit loop
            await r.set("system:halted", "1")
            return []

        with patch("lol_pipeline.service.consume", side_effect=failing_consume):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await run_consumer(r, _STREAM, _GROUP, "c", handler, log)
                mock_sleep.assert_called_with(1)
