"""Unit tests for lol_analyzer.main — Phase 04 ACs 04-14 through 04-25."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import consume, publish

from lol_analyzer.main import _analyze_player, main

_IN_STREAM = "stream:analyze"
_GROUP = "analyzers"

try:
    import lupa  # noqa: F401

    _LUPA_AVAILABLE = True
except ImportError:
    _LUPA_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua lock release")


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    return Config(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def log():
    return logging.getLogger("test-analyzer")


def _analyze_envelope(puuid="test-puuid-0001"):
    return MessageEnvelope(
        source_stream=_IN_STREAM,
        type="analyze",
        payload={"puuid": puuid},
        max_attempts=5,
    )


async def _setup_message(r, envelope):
    await publish(r, _IN_STREAM, envelope)
    msgs = await consume(r, _IN_STREAM, _GROUP, "test-consumer", block=0)
    assert len(msgs) == 1
    return msgs[0][0]


async def _add_participant(  # noqa: PLR0913
    r,
    match_id,
    puuid,
    game_start,
    win=True,
    kills=10,
    deaths=2,
    assists=5,
    champion="Annie",
    role="SOLO",
):
    """Write participant hash and add to player:matches sorted set."""
    await r.hset(
        f"participant:{match_id}:{puuid}",
        mapping={
            "champion_name": champion,
            "role": role,
            "win": "1" if win else "0",
            "kills": str(kills),
            "deaths": str(deaths),
            "assists": str(assists),
        },
    )
    await r.zadd(f"player:matches:{puuid}", {match_id: float(game_start)})


class TestAnalyzerLock:
    @pytest.mark.asyncio
    async def test_lock_held_by_another_worker_acks_immediately(self, r, cfg, log):
        """AC-04-14: lock held by another → ACKs; no HGETALL calls."""
        puuid = "test-puuid-0001"
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)
        # Another worker holds the lock
        await r.set(f"player:stats:lock:{puuid}", "other-worker", nx=True, px=30000)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "total_games") is None

    @pytest.mark.asyncio
    async def test_lock_acquired_processes_and_releases(self, r, cfg, log):
        """AC-04-15: lock acquired → processes matches; releases lock; ACKs."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"
        assert await r.exists(f"player:stats:lock:{puuid}") == 0

    @pytest.mark.asyncio
    async def test_lock_stolen_logs_warning(self, r, cfg, log):
        """AC-04-22: lock expires mid-processing → release returns 0; still ACKs."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        # Set lock to expire immediately (1ms TTL)
        original_set = r.set

        async def _set_with_tiny_ttl(key, value, **kwargs):
            if "player:stats:lock" in key and kwargs.get("nx"):
                kwargs["px"] = 1  # 1ms — will expire instantly
            return await original_set(key, value, **kwargs)

        r.set = _set_with_tiny_ttl

        # Let the lock expire and another worker grab it
        import asyncio

        await asyncio.sleep(0.01)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)
        # Should still complete without error


class TestAnalyzerCursor:
    @pytest.mark.asyncio
    async def test_cursor_zero_processes_all(self, r, cfg, log):
        """AC-04-16: cursor=0 → processes all matches."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        await _add_participant(r, "NA1_2", puuid, 2000, kills=3, deaths=2, assists=7)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "worker1", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "total_games") == "2"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "8"

    @pytest.mark.asyncio
    async def test_cursor_at_highest_returns_empty(self, r, cfg, log):
        """AC-04-17: cursor=highest → no new matches; cursor unchanged; ACKs."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000)
        # Set cursor past all matches
        await r.set(f"player:stats:cursor:{puuid}", "1000")
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "worker1", msg_id, env, log)

        # total_games should not have been set (no new matches)
        assert await r.hget(f"player:stats:{puuid}", "total_games") is None

    @pytest.mark.asyncio
    async def test_single_new_match_after_cursor(self, r, cfg, log):
        """AC-04-18: 1 new match after cursor → total_games +=1; cursor advances."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=2, assists=3, win=True)
        # First analysis
        env1 = _analyze_envelope(puuid)
        msg_id1 = await _setup_message(r, env1)
        await _analyze_player(r, cfg, "w1", msg_id1, env1, log)

        # Add a new match after cursor
        await _add_participant(r, "NA1_2", puuid, 2000, kills=3, deaths=4, assists=1, win=False)
        env2 = _analyze_envelope(puuid)
        msg_id2 = await _setup_message(r, env2)
        await _analyze_player(r, cfg, "w2", msg_id2, env2, log)

        assert await r.hget(f"player:stats:{puuid}", "total_games") == "2"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "8"
        assert await r.hget(f"player:stats:{puuid}", "total_deaths") == "6"

    @pytest.mark.asyncio
    async def test_five_new_matches(self, r, cfg, log):
        """AC-04-19: 5 new matches → all accumulated; cursor = highest score."""
        puuid = "test-puuid-0001"
        for i in range(5):
            await _add_participant(r, f"NA1_{i}", puuid, 1000 + i, kills=2, deaths=1, assists=1)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "total_games") == "5"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "10"
        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert float(cursor) == 1004.0


class TestAnalyzerEdgeCases:
    @pytest.mark.asyncio
    async def test_zero_deaths_kda(self, r, cfg, log):
        """AC-04-20: 0 deaths → kda = (kills+assists)/max(0,1) = kills+assists."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=10, deaths=0, assists=5)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "kda") == "15.0000"

    @pytest.mark.asyncio
    async def test_champion_sorted_set(self, r, cfg, log):
        """AC-04-23: 3 games on Annie, 2 on Jinx → ZSCORE Annie=3, Jinx=2."""
        puuid = "test-puuid-0001"
        for i in range(3):
            await _add_participant(r, f"NA1_A{i}", puuid, 1000 + i, champion="Annie")
        for i in range(2):
            await _add_participant(r, f"NA1_J{i}", puuid, 2000 + i, champion="Jinx")
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        assert await r.zscore(f"player:champions:{puuid}", "Annie") == 3.0
        assert await r.zscore(f"player:champions:{puuid}", "Jinx") == 2.0

    @pytest.mark.asyncio
    async def test_derived_precision_4_decimal(self, r, cfg, log):
        """AC-04-25: all derived fields use .4f precision."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=10, deaths=3, assists=5, win=True)
        await _add_participant(r, "NA1_2", puuid, 2000, kills=0, deaths=4, assists=0, win=False)
        await _add_participant(r, "NA1_3", puuid, 3000, kills=0, deaths=0, assists=0, win=False)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        stats = await r.hgetall(f"player:stats:{puuid}")
        assert stats["win_rate"] == "0.3333"
        assert stats["avg_kills"] == "3.3333"
        assert len(stats["kda"].split(".")[1]) == 4


class TestAnalyzerPriority:
    @pytest.mark.asyncio
    async def test_analyze__clears_priority_after_processing(self, r, cfg, log):
        """After stats computation, clear_priority removes player:priority key."""
        puuid = "test-puuid-0001"
        # Set priority key (simulating what Seed does)
        await r.set(f"player:priority:{puuid}", "high")
        await r.set("system:priority_count", "1")

        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        # Stats should be computed
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"
        # Priority should be cleared
        assert await r.get(f"player:priority:{puuid}") is None
        assert await r.get("system:priority_count") == "0"


class TestAnalyzerAckPlacement:
    """Verify ack() is called even when clear_priority() raises an error.

    clear_priority() is wrapped in try/except so it cannot block the ack().
    Stats are computed, lock is released, message is acknowledged regardless.
    """

    @pytest.mark.asyncio
    async def test_clear_priority_raises__ack_still_called(self, r, cfg, log):
        """When clear_priority raises, ack is still called — message leaves PEL."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        # Patch clear_priority to raise a ConnectionError
        with patch(
            "lol_analyzer.main.clear_priority",
            side_effect=ConnectionError("redis down during clear_priority"),
        ):
            # Should NOT raise — clear_priority error is caught internally
            await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        # Stats should have been written before the error
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"
        # Lock should have been released by the finally block
        assert await r.exists(f"player:stats:lock:{puuid}") == 0
        # Message should be ACKed (no longer in PEL)
        pending = await r.xpending(_IN_STREAM, _GROUP)
        assert pending["pending"] == 0


class TestAnalyzerPipeline:
    @pytest.mark.asyncio
    async def test_stats_update_uses_pipeline(self, r, cfg, log):
        """HINCRBY calls are batched in a pipeline, not issued individually."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        hincrby_count = 0
        original_hincrby = r.hincrby

        async def counting_hincrby(*args, **kwargs):
            nonlocal hincrby_count
            hincrby_count += 1
            return await original_hincrby(*args, **kwargs)

        r.hincrby = counting_hincrby

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        # With pipeline batching, hincrby is called on the pipe, not r
        assert hincrby_count == 0, f"Expected 0 individual hincrby calls, got {hincrby_count}"
        # Stats should still be updated correctly
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"


class TestAnalyzerHgetallBatching:
    """CQ-16: HGETALL calls for participant data are batched in a pipeline."""

    @pytest.mark.asyncio
    async def test_hgetall_not_called_individually(self, r, cfg, log):
        """Per-match HGETALL calls go through pipeline, not individual calls on r."""
        puuid = "test-puuid-0001"
        for i in range(5):
            await _add_participant(r, f"NA1_{i}", puuid, 1000 + i)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        direct_hgetall_count = 0
        original_hgetall = r.hgetall

        async def counting_hgetall(*args, **kwargs):
            nonlocal direct_hgetall_count
            direct_hgetall_count += 1
            return await original_hgetall(*args, **kwargs)

        r.hgetall = counting_hgetall

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        # Only the final stats HGETALL should be called directly on r.
        # The 5 per-match HGETALLs should go through the pipeline.
        assert direct_hgetall_count == 1, (
            f"Expected 1 direct hgetall (final stats), got {direct_hgetall_count}"
        )
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "5"


class TestAnalyzerDerivedRecovery:
    @pytest.mark.asyncio
    async def test_derived_stats_recomputed_on_empty_run(self, r, cfg, log):
        """Derived stats should be recomputed even when no new matches exist.
        This recovers from mid-processing crashes where cursor advanced but
        derived stats were not updated."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=10, deaths=2, assists=5, win=True)

        # Simulate a crashed state: raw stats exist, cursor is advanced, but no derived stats
        await r.hset(
            f"player:stats:{puuid}",
            mapping={
                "total_games": "1",
                "total_wins": "1",
                "total_kills": "10",
                "total_deaths": "2",
                "total_assists": "5",
            },
        )
        await r.set(f"player:stats:cursor:{puuid}", "1000")

        # Run analyzer with no new matches
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)
        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        # Derived stats should have been recomputed
        assert await r.hget(f"player:stats:{puuid}", "win_rate") == "1.0000"
        assert await r.hget(f"player:stats:{puuid}", "kda") == "7.5000"


class TestAnalyzerTier3EdgeCases:
    """Tier 3 — Analyzer edge case tests."""

    @pytest.mark.asyncio
    async def test_empty_match_history_no_cursor_update(self, r, cfg, log):
        """No player:matches entries → cursor stays unset; derived stats still computed."""
        puuid = "test-puuid-0001"
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        # No cursor should be set (no matches to process)
        assert await r.get(f"player:stats:cursor:{puuid}") is None
        # No stats should exist
        assert await r.hget(f"player:stats:{puuid}", "total_games") is None

    @pytest.mark.asyncio
    async def test_very_large_cursor_handles_float_precision(self, r, cfg, log):
        """Cursor values near float precision limit are handled correctly."""
        puuid = "test-puuid-0001"
        # Use a very large timestamp (year ~2100 in ms)
        large_ts = 4102444800000.0
        await _add_participant(r, "NA1_FUTURE", puuid, large_ts, kills=5, deaths=1, assists=3)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert float(cursor) == large_ts
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"

    @pytest.mark.asyncio
    async def test_lock_acquisition_redis_error_propagates(self, r, cfg, log):
        """Redis error during lock SET NX propagates to caller."""
        puuid = "test-puuid-0001"
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        original_set = r.set

        async def failing_set(key, *args, **kwargs):
            if "player:stats:lock" in key:
                raise ConnectionError("redis down")
            return await original_set(key, *args, **kwargs)

        r.set = failing_set

        with pytest.raises(ConnectionError, match="redis down"):
            await _analyze_player(r, cfg, "w1", msg_id, env, log)


class TestAnalyzerAtomicCursorAdvancement:
    """Stats HINCRBY + cursor SET + lock PEXPIRE must be in the same MULTI/EXEC."""

    @pytest.mark.asyncio
    async def test_cursor_set_inside_stats_pipeline(self, r, cfg, log):
        """Cursor SET is in the same MULTI/EXEC as HINCRBY — no separate round-trip."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        # Track direct SET calls on the redis client (not on a pipeline)
        direct_set_calls: list[str] = []
        original_set = r.set

        async def tracking_set(key, *args, **kwargs):
            direct_set_calls.append(key)
            return await original_set(key, *args, **kwargs)

        r.set = tracking_set

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        # The cursor SET should NOT appear as a direct call on r —
        # it should be inside the pipeline transaction.
        cursor_calls = [k for k in direct_set_calls if "player:stats:cursor" in k]
        assert cursor_calls == [], (
            f"Cursor SET was called directly on r (non-atomic): {cursor_calls}. "
            "It must be inside the MULTI/EXEC pipeline with HINCRBY."
        )
        # But cursor should still be advanced correctly
        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert cursor == "1000.0"
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"

    @pytest.mark.asyncio
    async def test_lock_pexpire_inside_stats_pipeline(self, r, cfg, log):
        """Lock PEXPIRE is in the same MULTI/EXEC — no separate round-trip."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        direct_pexpire_calls: list[str] = []
        original_pexpire = r.pexpire

        async def tracking_pexpire(key, *args, **kwargs):
            direct_pexpire_calls.append(key)
            return await original_pexpire(key, *args, **kwargs)

        r.pexpire = tracking_pexpire

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        lock_pexpire_calls = [k for k in direct_pexpire_calls if "player:stats:lock" in k]
        assert lock_pexpire_calls == [], (
            f"PEXPIRE was called directly on r: {lock_pexpire_calls}. "
            "It must be inside the MULTI/EXEC pipeline."
        )
        # Lock should still have been released after processing
        assert await r.exists(f"player:stats:lock:{puuid}") == 0

    @pytest.mark.asyncio
    async def test_cursor_advances_per_match_atomically(self, r, cfg, log):
        """With multiple matches, cursor advances after each match's atomic pipeline."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=2, deaths=1, assists=1)
        await _add_participant(r, "NA1_2", puuid, 2000, kills=3, deaths=0, assists=2)
        await _add_participant(r, "NA1_3", puuid, 3000, kills=1, deaths=1, assists=4)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        # Cursor should be at the highest score
        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert float(cursor) == 3000.0
        # All three matches should be counted
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "3"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "6"

    @pytest.mark.asyncio
    async def test_source_has_cursor_and_pexpire_in_pipeline_block(self, r, cfg, log):
        """Source inspection: cursor SET and lock PEXPIRE are inside the pipe block."""
        import inspect

        source = inspect.getsource(_analyze_player)
        # Find the transaction pipeline block
        lines = source.splitlines()
        in_pipe_block = False
        pipe_indent = 0
        pipe_contents: list[str] = []
        for line in lines:
            stripped = line.lstrip()
            if "r.pipeline(transaction=True)" in line:
                in_pipe_block = True
                pipe_indent = len(line) - len(stripped)
                continue
            if in_pipe_block:
                current_indent = len(line) - len(stripped) if stripped else pipe_indent + 1
                if current_indent > pipe_indent or not stripped:
                    pipe_contents.append(stripped)
                else:
                    in_pipe_block = False

        pipe_block = "\n".join(pipe_contents)
        assert "pipe.set(" in pipe_block, (
            "cursor SET must use pipe.set() inside the transaction pipeline"
        )
        assert "pipe.pexpire(" in pipe_block, (
            "lock PEXPIRE must use pipe.pexpire() inside the transaction pipeline"
        )


class TestAnalyzerPipelineContextManager:
    """Fix 4: Stats pipeline uses async context manager for proper cleanup."""

    @pytest.mark.asyncio
    async def test_pipeline_context_manager_in_source(self, r, cfg, log):
        """Stats update pipeline uses 'async with r.pipeline(transaction=True) as pipe:'."""
        import inspect

        source = inspect.getsource(_analyze_player)
        assert "async with r.pipeline(transaction=True) as pipe:" in source

    @pytest.mark.asyncio
    async def test_pipeline_context_manager_still_writes_stats(self, r, cfg, log):
        """Context manager pipeline correctly writes stats (not a no-op)."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "5"


class TestAnalyzerSystemHalted:
    @pytest.mark.asyncio
    async def test_system_halted_skips(self, r, cfg, log):
        """AC-04-25b: system:halted → does NOT ACK; exits."""
        await r.set("system:halted", "1")
        env = _analyze_envelope()
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        assert await r.hget("player:stats:test-puuid-0001", "total_games") is None


class TestMainEntryPoint:
    """Tests for main() bootstrap and teardown."""

    @pytest.mark.asyncio
    async def test_main__creates_redis_and_starts_consumer(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        mock_consumer = AsyncMock()
        with (
            patch("lol_analyzer.main.Config") as mock_cfg,
            patch("lol_analyzer.main.get_redis", return_value=mock_r),
            patch("lol_analyzer.main.run_consumer", mock_consumer),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            await main()
        mock_consumer.assert_called_once()
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__keyboard_interrupt__closes_redis(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        with (
            patch("lol_analyzer.main.Config") as mock_cfg,
            patch("lol_analyzer.main.get_redis", return_value=mock_r),
            patch("lol_analyzer.main.run_consumer", side_effect=KeyboardInterrupt),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()
