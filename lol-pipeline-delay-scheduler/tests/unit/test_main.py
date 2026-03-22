"""Unit tests for lol_delay_scheduler.main — Phase 05 ACs 05-11 through 05-17."""

from __future__ import annotations

import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from redis.exceptions import RedisError

from lol_delay_scheduler.main import (
    _circuit_open,
    _is_circuit_open,
    _member_failures,
    _record_failure,
    _record_success,
    _tick,
    main,
)

_DELAYED_KEY = "delayed:messages"


@pytest.fixture(autouse=True)
def _reset_circuit_state():
    """Clear module-level failure tracking between tests."""
    _member_failures.clear()
    _circuit_open.clear()
    yield
    _member_failures.clear()
    _circuit_open.clear()


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


class TestDuplicateDispatchGuard:
    """ZSCORE guard in _DISPATCH_LUA prevents duplicate XADD on crash-restart."""

    @pytest.mark.asyncio
    async def test_member_removed_before_eval__no_xadd(self, r, log):
        """If member was already removed from ZSET (prior successful dispatch),
        the Lua script returns 0 and does NOT XADD again."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        # Simulate crash-restart: manually remove the member from ZSET
        # (as if a prior run's ZREM succeeded) while keeping the member
        # string available for eval.
        members = await r.zrange(_DELAYED_KEY, 0, -1)
        member = members[0]
        await r.zrem(_DELAYED_KEY, member)

        # Re-add with score so _tick picks it up via zrangebyscore,
        # then remove before eval runs — simulate race by directly calling eval.
        from lol_delay_scheduler.main import _DISPATCH_LUA, _maxlen_for_stream

        redis_fields = env.to_redis_fields()
        ml = _maxlen_for_stream(env.source_stream)
        flat_args: list[str] = [member, str(ml if ml is not None else 0)]
        for k, v in redis_fields.items():
            flat_args.append(str(k))
            flat_args.append(str(v))

        result = await r.eval(  # type: ignore[misc]
            _DISPATCH_LUA,
            2,
            env.source_stream,
            "delayed:messages",
            *flat_args,
        )

        assert result == 0, "Lua script should return 0 when member is absent from ZSET"
        assert await r.xlen("stream:match_id") == 0, "No XADD should occur"

    @pytest.mark.asyncio
    async def test_member_present__normal_dispatch(self, r, log):
        """When member exists in ZSET, Lua script dispatches normally and returns 1."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen("stream:match_id") == 1

    @pytest.mark.asyncio
    async def test_double_tick__no_duplicate_xadd(self, r, log):
        """Running _tick twice on the same member produces exactly one XADD."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        await _tick(r, log)
        await _tick(r, log)

        assert await r.xlen("stream:match_id") == 1


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

        original_eval = r.eval

        async def failing_eval(*args, **kwargs):
            raise RedisError("connection lost")

        r.eval = failing_eval

        await _tick(r, log)

        r.eval = original_eval
        # Member should still be in sorted set since eval (XADD+ZREM) failed
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

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.side_effect = spy_add_signal_handler

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=fake_tick),
            patch("lol_delay_scheduler.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_delay_scheduler.main.asyncio.get_running_loop", return_value=mock_loop),
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

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.side_effect = spy_add_signal_handler

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_delay_scheduler.main.asyncio.get_running_loop", return_value=mock_loop),
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

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=fake_tick),
            patch("lol_delay_scheduler.main.asyncio.sleep", side_effect=tracking_sleep),
            patch("lol_delay_scheduler.main.asyncio.get_running_loop", return_value=mock_loop),
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

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=fake_tick),
            patch("lol_delay_scheduler.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_delay_scheduler.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()

        assert call_count == 2, f"Expected _tick called twice, got {call_count}"
        mock_r.aclose.assert_called_once()


class TestXaddOSErrorContinuesProcessing:
    """OSError during individual XADD in _tick does not crash the loop."""

    @pytest.mark.asyncio
    async def test_oserror_on_eval__member_preserved_in_zset(self, r, log):
        """When eval (atomic XADD+ZREM) raises OSError, member stays in ZSET for retry."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        original_eval = r.eval

        async def failing_eval(*args, **kwargs):
            raise OSError("broken pipe")

        r.eval = failing_eval

        # _tick should NOT raise — it catches (RedisError, OSError) per-member
        await _tick(r, log)

        r.eval = original_eval
        # Member should still be in sorted set since eval failed
        assert await r.zcard(_DELAYED_KEY) == 1
        # Nothing was dispatched to the target stream
        assert await r.xlen("stream:match_id") == 0

    @pytest.mark.asyncio
    async def test_oserror_on_all_members__all_preserved(self, r, log):
        """When eval fails for all members, all remain in ZSET for retry.

        Because the error is caught per-member and the 'dispatched' count reflects
        only successful dispatches, the loop exits when dispatched==0 for a batch
        of all-failing members. All members remain in the ZSET for retry.
        """
        now_ms = int(time.time() * 1000)
        env1 = _delayed_envelope(match_id="NA1_fail")
        env2 = _delayed_envelope(match_id="NA1_also_fail")
        await _add_delayed(r, env1, now_ms - 2000)
        await _add_delayed(r, env2, now_ms - 1000)

        original_eval = r.eval

        async def always_failing_eval(*args, **kwargs):
            raise OSError("broken pipe")

        r.eval = always_failing_eval

        # Should not raise
        await _tick(r, log)

        r.eval = original_eval
        # Both remain since all evals failed
        assert await r.zcard(_DELAYED_KEY) == 2

    @pytest.mark.asyncio
    async def test_oserror_on_first__second_still_dispatched(self, r, log):
        """When eval fails for the first member but succeeds for the second,
        the second member is dispatched and removed from the ZSET."""
        now_ms = int(time.time() * 1000)
        env_fail = _delayed_envelope(stream="stream:fail_target", match_id="NA1_fail")
        env_ok = _delayed_envelope(stream="stream:match_id", match_id="NA1_ok")
        await _add_delayed(r, env_fail, now_ms - 2000)
        await _add_delayed(r, env_ok, now_ms - 1000)

        original_eval = r.eval
        call_count = 0

        async def selective_failing_eval(script, numkeys, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            # args[0] is the target stream (KEYS[1])
            if args[0] == "stream:fail_target":
                raise OSError("broken pipe")
            return await original_eval(script, numkeys, *args, **kwargs)

        r.eval = selective_failing_eval

        await _tick(r, log)

        r.eval = original_eval
        # The failing one stays, the successful one is removed
        assert await r.zcard(_DELAYED_KEY) == 1
        assert await r.xlen("stream:match_id") == 1
        assert await r.xlen("stream:fail_target") == 0


class TestMaxlenForStream:
    """I2-H3/H4: _maxlen_for_stream returns the correct per-stream policy."""

    def test_match_id_returns_none(self):
        from lol_delay_scheduler.main import _maxlen_for_stream

        assert _maxlen_for_stream("stream:match_id") is None

    def test_analyze_returns_50k(self):
        from lol_delay_scheduler.main import _maxlen_for_stream

        assert _maxlen_for_stream("stream:analyze") == 50_000

    def test_puuid_returns_default(self):
        from lol_delay_scheduler.main import _maxlen_for_stream

        assert _maxlen_for_stream("stream:puuid") == 10_000

    def test_parse_returns_default(self):
        from lol_delay_scheduler.main import _maxlen_for_stream

        assert _maxlen_for_stream("stream:parse") == 10_000

    def test_unknown_stream_returns_default(self):
        from lol_delay_scheduler.main import _maxlen_for_stream

        assert _maxlen_for_stream("stream:unknown") == 10_000


class TestDelaySchedulerMaxlenDispatch:
    """I2-H3: delay scheduler dispatches to stream:match_id without MAXLEN trimming."""

    @pytest.mark.asyncio
    async def test_dispatch_to_match_id__no_trimming(self, r, log):
        """Messages dispatched to stream:match_id should not be trimmed."""
        env = _delayed_envelope(stream="stream:match_id")
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen("stream:match_id") == 1

    @pytest.mark.asyncio
    async def test_dispatch_to_analyze__uses_50k_maxlen(self, r, log):
        """Messages dispatched to stream:analyze use maxlen=50_000."""
        env = _delayed_envelope(stream="stream:analyze")
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen("stream:analyze") == 1

    @pytest.mark.asyncio
    async def test_dispatch_to_puuid__uses_default_maxlen(self, r, log):
        """Messages dispatched to stream:puuid use default maxlen (10_000)."""
        env = _delayed_envelope(stream="stream:puuid")
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        await _tick(r, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen("stream:puuid") == 1


class TestTickOSErrorDuringLuaScript:
    """_tick OSError during Lua script execution: logged, service continues."""

    @pytest.mark.asyncio
    async def test_tick__oserror_during_zrangebyscore__propagates(self, r, log):
        """OSError on zrangebyscore (top-level) propagates — caught by main() loop."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        original_zrangebyscore = r.zrangebyscore

        async def failing_zrangebyscore(*args, **kwargs):
            raise OSError("connection reset by peer")

        r.zrangebyscore = failing_zrangebyscore

        # _tick itself raises OSError — caught by main() loop
        with pytest.raises(OSError, match="connection reset by peer"):
            await _tick(r, log)

        r.zrangebyscore = original_zrangebyscore

    @pytest.mark.asyncio
    async def test_main__tick_raises_oserror__logged_and_continues(self, monkeypatch):
        """OSError from _tick in main() loop is logged and retried, not a crash."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        call_count = 0

        async def fake_tick(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("Lua script connection broken")
            raise KeyboardInterrupt

        sleep_args: list[float] = []

        async def tracking_sleep(seconds, *args, **kwargs):
            sleep_args.append(seconds)

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=fake_tick),
            patch("lol_delay_scheduler.main.asyncio.sleep", side_effect=tracking_sleep),
            patch("lol_delay_scheduler.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()

        # _tick was called twice (first errored, second raised KeyboardInterrupt)
        assert call_count == 2
        # The error path sleeps 1s before retrying
        assert 1 in sleep_args
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

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=fake_tick),
            patch("lol_delay_scheduler.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_delay_scheduler.main.asyncio.get_running_loop", return_value=mock_loop),
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

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_delay_scheduler.main.Config") as mock_cfg,
            patch("lol_delay_scheduler.main.get_redis", return_value=mock_r),
            patch("lol_delay_scheduler.main._tick", side_effect=KeyboardInterrupt),
            patch("lol_delay_scheduler.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()


class TestRecordFailureAndSuccess:
    """I2-M4: _record_failure increments counter; _record_success clears it."""

    def test_record_failure_increments_counter(self, log):
        _record_failure("member-a", log)
        assert _member_failures["member-a"] == 1
        _record_failure("member-a", log)
        assert _member_failures["member-a"] == 2

    def test_record_success_clears_counter(self, log):
        _record_failure("member-b", log)
        _record_failure("member-b", log)
        _record_success("member-b")
        assert "member-b" not in _member_failures

    def test_record_success_clears_circuit(self, log):
        for _ in range(10):
            _record_failure("member-c", log)
        assert "member-c" in _circuit_open
        _record_success("member-c")
        assert "member-c" not in _circuit_open
        assert "member-c" not in _member_failures

    def test_circuit_opens_after_max_failures(self, log):
        for _ in range(9):
            _record_failure("member-d", log)
        assert "member-d" not in _circuit_open
        _record_failure("member-d", log)  # 10th failure
        assert "member-d" in _circuit_open

    def test_record_success_on_unknown_member_is_noop(self):
        _record_success("never-seen")
        assert "never-seen" not in _member_failures


class TestIsCircuitOpen:
    """I2-M4: _is_circuit_open checks TTL on circuit-open members."""

    def test_not_open_when_absent(self):
        assert _is_circuit_open("unknown") is False

    def test_open_when_recently_added(self):
        _circuit_open["member-e"] = time.monotonic()
        assert _is_circuit_open("member-e") is True

    def test_reopens_after_ttl_expired(self):
        # Simulate circuit opened 301 seconds ago
        _circuit_open["member-f"] = time.monotonic() - 301
        assert _is_circuit_open("member-f") is False
        # Entry should be removed
        assert "member-f" not in _circuit_open

    def test_still_open_before_ttl(self):
        _circuit_open["member-g"] = time.monotonic() - 200
        assert _is_circuit_open("member-g") is True


class TestCircuitBreakerIntegration:
    """I2-M4: End-to-end circuit breaker behaviour in _tick."""

    @pytest.mark.asyncio
    async def test_circuit_open_skips_member(self, r, log):
        """A circuit-open member is skipped by _tick; stays in ZSET."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        # Get the member string from the ZSET
        members = await r.zrange(_DELAYED_KEY, 0, -1)
        member = members[0]

        # Open the circuit for this member
        _circuit_open[member] = time.monotonic()

        await _tick(r, log)

        # Member should still be in ZSET (skipped)
        assert await r.zcard(_DELAYED_KEY) == 1
        assert await r.xlen("stream:match_id") == 0

    @pytest.mark.asyncio
    async def test_failures_tracked_on_eval_error(self, r, log):
        """Dispatch failures increment the per-member failure counter."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        members = await r.zrange(_DELAYED_KEY, 0, -1)
        member = members[0]

        original_eval = r.eval

        async def failing_eval(*args, **kwargs):
            raise RedisError("connection lost")

        r.eval = failing_eval

        await _tick(r, log)

        r.eval = original_eval
        assert _member_failures[member] == 1
        assert await r.zcard(_DELAYED_KEY) == 1

    @pytest.mark.asyncio
    async def test_success_clears_failure_count(self, r, log):
        """Successful dispatch clears the failure counter."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        members = await r.zrange(_DELAYED_KEY, 0, -1)
        member = members[0]

        # Pre-seed some failures
        _member_failures[member] = 5

        await _tick(r, log)

        assert member not in _member_failures
        assert await r.zcard(_DELAYED_KEY) == 0

    @pytest.mark.asyncio
    async def test_circuit_opens_after_10_consecutive_failures(self, r, log):
        """After 10 consecutive eval failures, the circuit opens and member is skipped."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        members = await r.zrange(_DELAYED_KEY, 0, -1)
        member = members[0]

        original_eval = r.eval

        async def failing_eval(*args, **kwargs):
            raise RedisError("connection lost")

        r.eval = failing_eval

        # Run 10 ticks to accumulate failures
        for _ in range(10):
            await _tick(r, log)

        assert member in _circuit_open

        # 11th tick should skip the member entirely (circuit open)
        await _tick(r, log)

        r.eval = original_eval
        # Member still in ZSET since it was skipped
        assert await r.zcard(_DELAYED_KEY) == 1

    @pytest.mark.asyncio
    async def test_circuit_reopens_after_ttl(self, r, log):
        """After circuit TTL expires, member is retried."""
        env = _delayed_envelope()
        past_ms = int(time.time() * 1000) - 1
        await _add_delayed(r, env, past_ms)

        members = await r.zrange(_DELAYED_KEY, 0, -1)
        member = members[0]

        # Open circuit with expired TTL
        _circuit_open[member] = time.monotonic() - 301

        await _tick(r, log)

        # Circuit should have expired, member should have been dispatched
        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen("stream:match_id") == 1
        assert member not in _circuit_open


class TestCircuitResetClearsFailures:
    """P14-FV-3: After circuit expires, failure counter is reset."""

    def test_circuit_expire_resets_failure_counter(self):
        """When circuit TTL expires, _member_failures is also cleared for that member."""
        member = "test-member-reset"
        # Simulate 10 failures (circuit opens)
        _member_failures[member] = 10
        _circuit_open[member] = time.monotonic() - 301  # expired

        # This should reset the circuit and the failure counter
        result = _is_circuit_open(member)

        assert result is False
        assert member not in _circuit_open
        assert member not in _member_failures, (
            "Failure counter should be reset when circuit expires"
        )

    def test_circuit_expire_next_failure_does_not_retrip(self):
        """After circuit expires and counter resets, a single failure does not re-trip."""
        member = "test-member-no-retrip"
        import logging

        log = logging.getLogger("test")

        _member_failures[member] = 10
        _circuit_open[member] = time.monotonic() - 301

        # Expire circuit
        _is_circuit_open(member)

        # One new failure should NOT re-trip (count=1 < 10)
        _record_failure(member, log)
        assert member not in _circuit_open
        assert _member_failures[member] == 1
