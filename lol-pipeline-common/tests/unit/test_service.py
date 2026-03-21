"""Unit tests for lol_pipeline.service — handler error resilience."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest

from lol_pipeline.models import MessageEnvelope
from lol_pipeline.service import (
    _RETRY_KEY_TTL,
    _clear_retry,
    _handle_with_retry,
    _incr_retry,
    _retry_key,
    run_consumer,
)
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
        """Successful handler call: no DLQ entry, retry key cleared."""
        msg_id, envelope = await _setup(r)

        async def handler(mid, env):
            pass  # success

        await _handle_with_retry(r, _STREAM, _GROUP, msg_id, envelope, handler, log, 3)

        assert await r.xlen("stream:dlq") == 0
        assert await r.exists(_retry_key(_STREAM, msg_id)) == 0

    @pytest.mark.asyncio
    async def test_persistent_crash_nacks_to_dlq(self, r, log):
        """After max_handler_retries consecutive failures, message is nacked to DLQ and ACKed."""
        msg_id, envelope = await _setup(r)

        async def bad_handler(mid, env):
            raise RuntimeError("boom")

        for _ in range(3):
            await _handle_with_retry(
                r,
                _STREAM,
                _GROUP,
                msg_id,
                envelope,
                bad_handler,
                log,
                3,
            )

        assert await r.xlen("stream:dlq") == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["failure_code"] == "handler_crash"

    @pytest.mark.asyncio
    async def test_intermittent_failure_resets_counter(self, r, log):
        """A success after failures resets the failure counter."""
        msg_id, envelope = await _setup(r)
        call_count = 0

        async def flaky_handler(mid, env):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("flaky")

        # Two failures
        for _ in range(2):
            await _handle_with_retry(
                r,
                _STREAM,
                _GROUP,
                msg_id,
                envelope,
                flaky_handler,
                log,
                3,
            )

        # Third call succeeds
        await _handle_with_retry(
            r,
            _STREAM,
            _GROUP,
            msg_id,
            envelope,
            flaky_handler,
            log,
            3,
        )

        assert await r.xlen("stream:dlq") == 0
        assert await r.exists(_retry_key(_STREAM, msg_id)) == 0


class TestRetryKeyTTL:
    @pytest.mark.asyncio
    async def test_retry_key_has_ttl(self, r, log):
        """Redis retry counter key gets a TTL so it does not persist forever."""
        msg_id, envelope = await _setup(r)

        async def bad_handler(mid, env):
            raise RuntimeError("boom")

        await _handle_with_retry(
            r,
            _STREAM,
            _GROUP,
            msg_id,
            envelope,
            bad_handler,
            log,
            5,  # max_retries high enough so we don't DLQ on first failure
        )

        key = _retry_key(_STREAM, msg_id)
        ttl = await r.ttl(key)
        assert 0 < ttl <= _RETRY_KEY_TTL


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
            source_stream=_STREAM,
            type="test",
            payload={"key": "val"},
            max_attempts=5,
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
        from unittest.mock import AsyncMock, patch

        call_count = 0

        async def handler(mid, env):
            pass

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

    @pytest.mark.asyncio
    async def test_redis_error_in_dispatch_loop_does_not_crash(self, r, log):
        """C3: RedisError during nack_to_dlq in dispatch loop does not crash run_consumer."""
        from redis.exceptions import RedisError

        env = MessageEnvelope(
            source_stream=_STREAM,
            type="test",
            payload={"key": "val"},
            max_attempts=5,
        )
        await publish(r, _STREAM, env)

        # _handle_with_retry raises RedisError (from nack_to_dlq or ack internally)
        dispatch_calls = 0

        async def failing_handle(*args, **kwargs):
            nonlocal dispatch_calls
            dispatch_calls += 1
            raise RedisError("connection lost during nack")

        consume_count = 0
        original_consume = consume

        async def counting_consume(*args, **kwargs):
            nonlocal consume_count
            consume_count += 1
            if consume_count > 1:
                await r.set("system:halted", "1")
                return []
            return await original_consume(*args, **kwargs)

        with (
            patch("lol_pipeline.service._handle_with_retry", side_effect=failing_handle),
            patch("lol_pipeline.service.consume", side_effect=counting_consume),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await run_consumer(r, _STREAM, _GROUP, "c", handler=AsyncMock(), log=log)

        # Key assertion: run_consumer completed without raising
        assert dispatch_calls == 1

    @pytest.mark.asyncio
    async def test_oserror_in_dispatch_loop_does_not_crash(self, r, log):
        """C3: OSError during ack in dispatch loop does not crash run_consumer."""
        env = MessageEnvelope(
            source_stream=_STREAM,
            type="test",
            payload={"key": "val"},
            max_attempts=5,
        )
        await publish(r, _STREAM, env)

        # _handle_with_retry raises OSError (simulates connection reset)
        dispatch_calls = 0

        async def failing_handle(*args, **kwargs):
            nonlocal dispatch_calls
            dispatch_calls += 1
            raise OSError("connection reset")

        consume_count = 0
        original_consume = consume

        async def counting_consume(*args, **kwargs):
            nonlocal consume_count
            consume_count += 1
            if consume_count > 1:
                await r.set("system:halted", "1")
                return []
            return await original_consume(*args, **kwargs)

        with (
            patch("lol_pipeline.service._handle_with_retry", side_effect=failing_handle),
            patch("lol_pipeline.service.consume", side_effect=counting_consume),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await run_consumer(r, _STREAM, _GROUP, "c", handler=AsyncMock(), log=log)

        # Key assertion: run_consumer completed without raising
        assert dispatch_calls == 1

    @pytest.mark.asyncio
    async def test_sigterm_sets_shutdown_flag(self, r, log):
        """CQ-7: SIGTERM handler sets shutdown flag, loop exits cleanly."""
        import signal

        captured_handlers: dict[int, object] = {}
        call_count = 0

        def spy_add(sig: int, callback: object, *args: object) -> None:
            captured_handlers[sig] = callback

        async def handler(mid: str, env: object) -> None:
            pass

        async def consume_then_fire(*args: object, **kwargs: object) -> list[object]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Invoke the SIGTERM handler directly (simulates OS signal)
                cb = captured_handlers.get(signal.SIGTERM)
                if cb and callable(cb):
                    cb()
            return []

        with (
            patch("lol_pipeline.service.asyncio.get_running_loop") as mock_loop_fn,
            patch("lol_pipeline.service.consume", side_effect=consume_then_fire),
        ):
            mock_loop = mock_loop_fn.return_value
            mock_loop.add_signal_handler.side_effect = spy_add
            await run_consumer(r, _STREAM, _GROUP, "c", handler, log)

        # Loop should have exited after SIGTERM callback, not because of system:halted
        assert await r.get("system:halted") is None
        assert signal.SIGTERM in captured_handlers
        assert call_count >= 1


class TestRedisBackedRetryCounter:
    """B13: Redis-backed retry counter survives service restarts."""

    @pytest.mark.asyncio
    async def test_counter_survives_simulated_restart(self, r, log):
        """Retry counter persists in Redis across independent _handle_with_retry calls,
        simulating a service restart (new in-memory state, same Redis)."""
        msg_id, envelope = await _setup(r)

        async def bad_handler(mid, env):
            raise RuntimeError("poison")

        # Simulate first "process lifetime": 2 failures
        for _ in range(2):
            await _handle_with_retry(r, _STREAM, _GROUP, msg_id, envelope, bad_handler, log, 3)

        # Counter should be 2 in Redis (not yet DLQ'd)
        assert await r.xlen("stream:dlq") == 0
        key = _retry_key(_STREAM, msg_id)
        assert await r.get(key) == "2"

        # Simulate restart: the 3rd failure (new call, same Redis) triggers DLQ
        await _handle_with_retry(r, _STREAM, _GROUP, msg_id, envelope, bad_handler, log, 3)

        assert await r.xlen("stream:dlq") == 1
        # Retry key cleaned up after DLQ
        assert await r.exists(key) == 0

    @pytest.mark.asyncio
    async def test_counter_deleted_on_success(self, r, log):
        """Successful handler call deletes the Redis retry counter."""
        msg_id, envelope = await _setup(r)
        call_count = 0

        async def eventually_ok(mid, env):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise RuntimeError("transient")

        # First call: failure (counter = 1)
        await _handle_with_retry(r, _STREAM, _GROUP, msg_id, envelope, eventually_ok, log, 3)
        key = _retry_key(_STREAM, msg_id)
        assert await r.get(key) == "1"

        # Second call: success — counter should be deleted
        await _handle_with_retry(r, _STREAM, _GROUP, msg_id, envelope, eventually_ok, log, 3)
        assert await r.exists(key) == 0
        assert await r.xlen("stream:dlq") == 0

    @pytest.mark.asyncio
    async def test_poison_message_nacked_after_max_retries(self, r, log):
        """A poison message is nacked to DLQ exactly once after max retries."""
        msg_id, envelope = await _setup(r)

        async def poison(mid, env):
            raise RuntimeError("always fails")

        # 5 calls with max_retries=3 — DLQ should happen on call 3,
        # then calls 4-5 should not produce additional DLQ entries
        # because the key is cleared after DLQ.
        for _i in range(5):
            await _handle_with_retry(r, _STREAM, _GROUP, msg_id, envelope, poison, log, 3)

        # DLQ entry created after retry 3, then counter resets,
        # so retries 4 and 5 start a new counter (not yet at 3 again).
        dlq_len = await r.xlen("stream:dlq")
        assert dlq_len == 1

    @pytest.mark.asyncio
    async def test_incr_retry_sets_ttl_atomically(self, r):
        """P16-DB-4: _incr_retry sets TTL via pipeline on every call (atomic)."""
        count1 = await _incr_retry(r, _STREAM, "msg-ttl-test")
        assert count1 == 1
        ttl1 = await r.ttl(_retry_key(_STREAM, "msg-ttl-test"))
        assert 0 < ttl1 <= _RETRY_KEY_TTL

        count2 = await _incr_retry(r, _STREAM, "msg-ttl-test")
        assert count2 == 2
        ttl2 = await r.ttl(_retry_key(_STREAM, "msg-ttl-test"))
        # TTL refreshed on every call — should be close to _RETRY_KEY_TTL
        assert 0 < ttl2 <= _RETRY_KEY_TTL

    @pytest.mark.asyncio
    async def test_clear_retry_removes_key(self, r):
        """_clear_retry deletes the retry counter key from Redis."""
        await _incr_retry(r, _STREAM, "msg-clear-test")
        key = _retry_key(_STREAM, "msg-clear-test")
        assert await r.exists(key) == 1

        await _clear_retry(r, _STREAM, "msg-clear-test")
        assert await r.exists(key) == 0


class TestPriorityReordering:
    """R6: Consumer sorts messages within a batch by priority before dispatching."""

    @pytest.mark.asyncio
    async def test_consumer_sorts_batch_by_priority(self, r, log):
        """Messages in a batch are dispatched highest-priority first."""
        # Publish 3 messages with different priorities
        envs = [
            MessageEnvelope(
                source_stream=_STREAM,
                type="test",
                payload={"order": "low"},
                max_attempts=5,
                priority="auto_new",
            ),
            MessageEnvelope(
                source_stream=_STREAM,
                type="test",
                payload={"order": "high"},
                max_attempts=5,
                priority="manual_20",
            ),
            MessageEnvelope(
                source_stream=_STREAM,
                type="test",
                payload={"order": "mid"},
                max_attempts=5,
                priority="auto_20",
            ),
        ]
        for e in envs:
            await publish(r, _STREAM, e)

        processed_priorities = []

        async def handler(mid, envelope):
            processed_priorities.append(envelope.priority)
            await ack(r, _STREAM, _GROUP, mid)

        # Set halted after processing to exit the loop
        call_count = 0
        original_consume = consume

        async def consume_then_halt(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                msgs = await original_consume(*args, **kwargs)
                return msgs
            # After first batch, halt to exit
            await r.set("system:halted", "1")
            return []

        with patch("lol_pipeline.service.consume", side_effect=consume_then_halt):
            await run_consumer(r, _STREAM, _GROUP, "c", handler, log)

        # Should be sorted: manual_20 (4) > auto_20 (2) > auto_new (1)
        assert processed_priorities == ["manual_20", "auto_20", "auto_new"]
