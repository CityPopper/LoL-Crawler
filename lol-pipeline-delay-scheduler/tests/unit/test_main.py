"""Unit tests for lol_delay_scheduler.main — Phase 05 ACs 05-11 through 05-17."""

from __future__ import annotations

import json
import logging
import time
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from redis.exceptions import RedisError

from lol_delay_scheduler.main import _tick, main

_DELAYED_KEY = "delayed:messages"


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def log():
    return logging.getLogger("test-delay-scheduler")


def _delayed_envelope(stream="stream:match_id", match_id="NA1_123"):
    return MessageEnvelope(
        source_stream=stream,
        type=stream.removeprefix("stream:"),
        payload={"match_id": match_id, "region": "na1"},
        max_attempts=5,
    )


async def _add_delayed(r, envelope, score_ms):
    member = json.dumps(envelope.to_redis_fields())
    await r.zadd(_DELAYED_KEY, {member: score_ms})


class TestDelaySchedulerEmpty:
    @pytest.mark.asyncio
    async def test_empty_no_error(self, r, log):
        """AC-05-11: empty delayed:messages → no XADD; no error."""
        await _tick(r, log)
        # No streams should have been created
        # Just verify no exception was raised


class TestDelaySchedulerTiming:
    @pytest.mark.asyncio
    async def test_future_message_not_moved(self, r, log):
        """AC-05-12: score=now+5000 → not moved; still in delayed:messages."""
        env = _delayed_envelope()
        future_ms = int(time.time() * 1000) + 5000
        await _add_delayed(r, env, future_ms)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 1
        assert await r.xlen("stream:match_id") == 0

    @pytest.mark.asyncio
    async def test_past_message_moved(self, r, log):
        """AC-05-13: score=now-1 → moved to target stream; removed from delayed:messages."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen("stream:match_id") == 1

    @pytest.mark.asyncio
    async def test_boundary_score_moved(self, r, log):
        """AC-05-14: score=now exactly → moved (boundary is inclusive ≤)."""
        env = _delayed_envelope()
        now_ms = int(time.time() * 1000)
        await _add_delayed(r, env, now_ms)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen("stream:match_id") == 1


class TestDelaySchedulerBatch:
    @pytest.mark.asyncio
    async def test_all_past_due_moved(self, r, log):
        """AC-05-15a: 3 past-due messages → all moved; delayed:messages empty."""
        now_ms = int(time.time() * 1000)
        for i in range(3):
            env = _delayed_envelope(match_id=f"NA1_{i}")
            await _add_delayed(r, env, now_ms - 1000 - i)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen("stream:match_id") == 3

    @pytest.mark.asyncio
    async def test_mixed_past_and_future(self, r, log):
        """AC-05-15b: 2 past-due + 1 future → only 2 moved."""
        now_ms = int(time.time() * 1000)
        for i in range(2):
            env = _delayed_envelope(match_id=f"NA1_past_{i}")
            await _add_delayed(r, env, now_ms - 1000)
        env_future = _delayed_envelope(match_id="NA1_future")
        await _add_delayed(r, env_future, now_ms + 5000)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 1
        assert await r.xlen("stream:match_id") == 2


class TestDelaySchedulerDispatch:
    @pytest.mark.asyncio
    async def test_dispatched_envelope_correct(self, r, log):
        """AC-05-16: dispatched envelope appears in target stream with correct fields."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        await _tick(r, log)

        entries = await r.xrange("stream:match_id")
        assert len(entries) == 1
        fields = entries[0][1]
        restored = MessageEnvelope.from_redis_fields(fields)
        assert restored.payload["match_id"] == "NA1_123"
        assert restored.source_stream == "stream:match_id"

    @pytest.mark.asyncio
    async def test_system_halted_does_not_block(self, r, log):
        """AC-05-17: system:halted → messages still move (no halt check)."""
        await r.set("system:halted", "1")
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen("stream:match_id") == 1

    @pytest.mark.asyncio
    async def test_malformed_member_removed(self, r, log):
        """Invalid JSON in delayed:messages → logged; no crash; member removed."""
        past_ms = int(time.time() * 1000) - 1
        await r.zadd(_DELAYED_KEY, {"not-valid-json": past_ms})

        await _tick(r, log)

        # Corrupted member must be removed to prevent infinite retry spam
        assert await r.zcard(_DELAYED_KEY) == 0

    @pytest.mark.asyncio
    async def test_dispatches_to_correct_stream(self, r, log):
        """Messages go to the stream specified in source_stream."""
        env = _delayed_envelope(stream="stream:parse", match_id="NA1_456")
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        await _tick(r, log)

        assert await r.xlen("stream:parse") == 1
        assert await r.xlen("stream:match_id") == 0


class TestDelaySchedulerPagination:
    @pytest.mark.asyncio
    async def test_zrangebyscore_uses_limit(self, r, log):
        """ZRANGEBYSCORE is called with LIMIT to bound memory."""
        now_ms = int(time.time() * 1000)
        env = _delayed_envelope()
        await _add_delayed(r, env, now_ms - 1)

        calls = []
        original = r.zrangebyscore

        async def tracking(*args, **kwargs):
            calls.append(kwargs)
            return await original(*args, **kwargs)

        r.zrangebyscore = tracking

        await _tick(r, log)

        assert len(calls) >= 1
        assert "num" in calls[0], "Expected LIMIT (num=) on zrangebyscore"

    @pytest.mark.asyncio
    async def test_large_batch_all_processed(self, r, log):
        """150 past-due messages → all processed across multiple batches."""
        now_ms = int(time.time() * 1000)
        for i in range(150):
            env = _delayed_envelope(match_id=f"NA1_{i}")
            await _add_delayed(r, env, now_ms - 1000 - i)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen("stream:match_id") == 150


class TestDelaySchedulerEdgeCases:
    @pytest.mark.asyncio
    async def test_multiple_streams_dispatched_correctly(self, r, log):
        """Messages to different streams are dispatched to correct targets."""
        now_ms = int(time.time() * 1000)
        env1 = _delayed_envelope(stream="stream:match_id", match_id="NA1_1")
        env2 = _delayed_envelope(stream="stream:parse", match_id="NA1_2")
        await _add_delayed(r, env1, now_ms - 1000)
        await _add_delayed(r, env2, now_ms - 1000)

        await _tick(r, log)

        assert await r.xlen("stream:match_id") == 1
        assert await r.xlen("stream:parse") == 1
        assert await r.zcard(_DELAYED_KEY) == 0

    @pytest.mark.asyncio
    async def test_preserves_dlq_attempts_on_dispatch(self, r, log):
        """Dispatched envelope preserves dlq_attempts from delayed entry."""
        now_ms = int(time.time() * 1000)
        env = MessageEnvelope(
            source_stream="stream:match_id",
            type="match_id",
            payload={"match_id": "NA1_999", "region": "na1"},
            max_attempts=5,
            dlq_attempts=2,
        )
        await _add_delayed(r, env, now_ms - 5000)  # 5s in past to avoid timing edge

        await _tick(r, log)

        entries = await r.xrange("stream:match_id")
        assert len(entries) >= 1
        # Find our specific entry by match_id
        restored = None
        for _, fields in entries:
            e = MessageEnvelope.from_redis_fields(fields)
            if e.payload.get("match_id") == "NA1_999":
                restored = e
                break
        assert restored is not None, "NA1_999 not found in stream:match_id"
        assert restored.dlq_attempts == 2


class TestDelaySchedulerRedisErrors:
    @pytest.mark.asyncio
    async def test_xadd_fails__does_not_remove_from_sorted_set(self, r, log):
        """XADD failure preserves member in delayed:messages for retry."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        original_xadd = r.xadd

        async def failing_xadd(*args, **kwargs):
            raise RedisError("connection lost")

        r.xadd = failing_xadd

        await _tick(r, log)

        r.xadd = original_xadd
        # Member should still be in sorted set since XADD failed
        assert await r.zcard(_DELAYED_KEY) == 1


class TestGracefulShutdown:
    """CQ-13: Delay scheduler uses asyncio.Event for shutdown."""

    @pytest.mark.asyncio
    async def test_sigterm_stops_main_loop(self, monkeypatch):
        """Triggering the shutdown event causes main() to exit cleanly."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()

        captured_callbacks: list[object] = []

        async def fake_tick(*args):
            if captured_callbacks:
                cb = captured_callbacks[0]
                if callable(cb):
                    cb()  # sets the shutdown_event

        def spy_add_signal_handler(sig, callback, *args):
            captured_callbacks.append(callback)

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.side_effect = spy_add_signal_handler

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=fake_tick),
            patch("lol_delay_scheduler.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_delay_scheduler.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            await main()  # should exit cleanly, not loop forever
        mock_r.aclose.assert_called_once()


class TestShutdownEventPattern:
    """Architecture: delay-scheduler uses asyncio.Event instead of module-level global."""

    @pytest.mark.asyncio
    async def test_no_module_level_shutdown_global(self):
        """The _shutdown global should no longer exist in the module."""
        import lol_delay_scheduler.main as mod

        assert not hasattr(mod, "_shutdown"), "Module-level _shutdown global should be removed"

    @pytest.mark.asyncio
    async def test_signal_handler_registered_via_loop(self, monkeypatch):
        """main() registers SIGTERM via loop.add_signal_handler, not signal.signal."""
        import signal as sig_mod

        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()

        registered_signals: list[int] = []

        def spy_add_signal_handler(signum, callback, *args):
            registered_signals.append(signum)
            callback()  # immediately set to stop the loop

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.side_effect = spy_add_signal_handler

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_delay_scheduler.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            await main()

        assert sig_mod.SIGTERM in registered_signals


class TestMainLoopRedisError:
    """C5: main() loop catches RedisError/OSError from _tick and continues."""

    @pytest.mark.asyncio
    async def test_tick_redis_error__loop_continues(self, monkeypatch):
        """RedisError in _tick is caught; loop retries after 1s sleep, then exits cleanly."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        call_count = 0

        async def fake_tick(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RedisError("connection reset")
            # Second call succeeds; then we stop the loop
            if call_count >= 2:
                raise KeyboardInterrupt

        sleep_args: list[float] = []

        async def tracking_sleep(seconds, *args, **kwargs):
            sleep_args.append(seconds)

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=fake_tick),
            patch("lol_delay_scheduler.main.asyncio.sleep", side_effect=tracking_sleep),
            patch("lol_delay_scheduler.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()

        # _tick was called at least twice (first errored, second raised KeyboardInterrupt)
        assert call_count >= 2, f"Expected _tick called >=2 times, got {call_count}"
        # The error path sleeps 1s before retrying
        assert 1 in sleep_args, f"Expected 1s retry sleep, got {sleep_args}"
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_os_error__loop_continues(self, monkeypatch):
        """OSError in _tick is also caught; loop retries."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        call_count = 0

        async def fake_tick(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("broken pipe")
            raise KeyboardInterrupt

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=fake_tick),
            patch("lol_delay_scheduler.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_delay_scheduler.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()

        assert call_count == 2, f"Expected _tick called twice, got {call_count}"
        mock_r.aclose.assert_called_once()


class TestMainEntryPoint:
    """Tests for main() bootstrap and teardown."""

    @pytest.mark.asyncio
    async def test_main__creates_redis_and_starts_loop(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        call_count = 0

        async def fake_tick(*args):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise KeyboardInterrupt

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=fake_tick),
            patch("lol_delay_scheduler.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_delay_scheduler.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__keyboard_interrupt__closes_redis(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=KeyboardInterrupt),
            patch("lol_delay_scheduler.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()
