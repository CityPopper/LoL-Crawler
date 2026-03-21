"""Unit tests for lol_analyzer.main — Phase 04 ACs 04-14 through 04-25."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import consume, publish

from lol_analyzer.main import (
    _analyze_player,
    _derived,
    _process_matches,
    _refresh_lock,
    _update_champion_stats,
    main,
)

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
    async def test_lock_stolen_logs_warning(self, r, cfg, log, caplog):
        """AC-04-22: lock expires mid-processing → release returns 0; still ACKs."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        # Intercept _refresh_lock to simulate another worker stealing the lock
        # after processing completes but before release.
        original_refresh = _refresh_lock

        async def _fake_refresh(redis, lock_key, worker_id, ttl_ms):
            result = await original_refresh(redis, lock_key, worker_id, ttl_ms)
            # After a successful refresh, delete the lock to simulate expiry
            # so the final release in the `finally` block returns 0.
            await redis.delete(lock_key)
            return result

        with (
            patch("lol_analyzer.main._refresh_lock", side_effect=_fake_refresh),
            caplog.at_level(logging.WARNING, logger="test-analyzer"),
        ):
            await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        # Lock was gone at release time → warning logged
        assert any("lock expired before release" in rec.message for rec in caplog.records)

        # Message was still ACK'd (removed from PEL)
        pending = await r.xpending_range(_IN_STREAM, _GROUP, min="-", max="+", count=10)
        assert len(pending) == 0


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
        await r.set(f"player:priority:{puuid}", "1", ex=86400)

        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        # Stats should be computed
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"
        # Priority should be cleared
        assert await r.get(f"player:priority:{puuid}") is None


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
    async def test_source_has_cursor_in_pipeline_and_lua_lock_refresh(self, r, cfg, log):
        """Source inspection: cursor SET inside MULTI/EXEC; lock refresh via Lua."""
        import inspect

        source = inspect.getsource(_process_matches)
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
        # PEXPIRE must NOT be in the pipeline — lock refresh uses Lua ownership check
        assert "pipe.pexpire(" not in pipe_block, (
            "lock PEXPIRE must NOT be in the pipeline — use Lua ownership-check refresh"
        )
        # Lock refresh uses _refresh_lock (Lua script) after the pipeline
        assert "_refresh_lock(" in source, (
            "lock refresh must use _refresh_lock() (Lua ownership check) after pipeline"
        )


class TestAnalyzerPipelineContextManager:
    """Fix 4: Stats pipeline uses async context manager for proper cleanup."""

    @pytest.mark.asyncio
    async def test_pipeline_context_manager_in_source(self, r, cfg, log):
        """Stats update pipeline uses 'async with r.pipeline(transaction=True) as pipe:'."""
        import inspect

        source = inspect.getsource(_process_matches)
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


class TestLockOwnershipRefresh:
    """I2-H5: Lock refresh must verify ownership via Lua before extending TTL."""

    @pytest.mark.asyncio
    async def test_refresh_lock__owned__returns_true(self, r):
        """_refresh_lock returns True when we still own the lock."""
        lock_key = "player:stats:lock:test-puuid"
        worker_id = "worker-1"
        await r.set(lock_key, worker_id, px=30000)

        result = await _refresh_lock(r, lock_key, worker_id, 30000)

        assert result is True
        # Lock should still exist with refreshed TTL
        assert await r.get(lock_key) == worker_id

    @pytest.mark.asyncio
    async def test_refresh_lock__stolen__returns_false(self, r):
        """_refresh_lock returns False when another worker owns the lock."""
        lock_key = "player:stats:lock:test-puuid"
        # Another worker holds the lock
        await r.set(lock_key, "other-worker", px=30000)

        result = await _refresh_lock(r, lock_key, "my-worker", 30000)

        assert result is False
        # Other worker's lock should be untouched
        assert await r.get(lock_key) == "other-worker"

    @pytest.mark.asyncio
    async def test_refresh_lock__expired__returns_false(self, r):
        """_refresh_lock returns False when the lock key no longer exists."""
        lock_key = "player:stats:lock:test-puuid"
        # No lock exists

        result = await _refresh_lock(r, lock_key, "my-worker", 30000)

        assert result is False

    @pytest.mark.asyncio
    async def test_ownership_lost__aborts_processing__skips_derived_stats(self, r, cfg, log):
        """When lock is stolen mid-processing, remaining matches are skipped
        and derived stats are NOT written."""
        puuid = "test-puuid-0001"
        # Set up 3 matches
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        await _add_participant(r, "NA1_2", puuid, 2000, kills=3, deaths=2, assists=7)
        await _add_participant(r, "NA1_3", puuid, 3000, kills=1, deaths=0, assists=1)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        # Patch _refresh_lock to simulate lock loss on the very first refresh
        # (after match 1 stats are committed but before match 2 is processed)
        async def _always_fail(*_args, **_kwargs):
            return False

        with patch("lol_analyzer.main._refresh_lock", side_effect=_always_fail):
            await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        # Only the first match should have been processed (stats committed
        # before the refresh check returned False)
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "5"
        # Cursor should be at first match only
        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert float(cursor) == 1000.0
        # Derived stats should NOT be computed (aborted before reaching that code)
        assert await r.hget(f"player:stats:{puuid}", "win_rate") is None
        assert await r.hget(f"player:stats:{puuid}", "kda") is None
        # Message should still be ACKed
        pending = await r.xpending(_IN_STREAM, _GROUP)
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_ownership_lost__does_not_extend_other_workers_lock(self, r, cfg, log):
        """When lock is stolen, we must NOT extend the other worker's lock TTL."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        # Simulate lock being stolen after stats pipeline but before refresh check
        original_refresh = _refresh_lock

        async def _steal_and_check(r, lock_key, worker_id, ttl_ms):
            # Replace lock with another worker's value before the Lua check
            await r.set(lock_key, "other-worker", px=5000)
            result = await original_refresh(r, lock_key, worker_id, ttl_ms)
            # Verify the Lua script did NOT extend the TTL
            assert result is False
            # The other worker's lock should still have its original short TTL
            remaining = await r.pttl(lock_key)
            assert remaining <= 5000, (
                f"Other worker's lock TTL was extended to {remaining}ms — "
                "Lua ownership check failed to protect it"
            )
            return result

        with patch("lol_analyzer.main._refresh_lock", side_effect=_steal_and_check):
            await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        # Other worker's lock should still be intact
        assert await r.get(f"player:stats:lock:{puuid}") == "other-worker"

    @pytest.mark.asyncio
    async def test_two_workers_no_double_count(self, r, cfg, log):
        """Two workers processing the same player should not double-count stats."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=10, deaths=2, assists=5, win=True)
        await _add_participant(r, "NA1_2", puuid, 2000, kills=3, deaths=4, assists=1, win=False)

        # Worker 1 acquires lock and processes
        env1 = _analyze_envelope(puuid)
        msg_id1 = await _setup_message(r, env1)
        await _analyze_player(r, cfg, "worker-1", msg_id1, env1, log)

        # Verify worker 1 processed both matches
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "2"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "13"

        # Worker 2 tries to process the same player (same cursor, no new matches)
        env2 = _analyze_envelope(puuid)
        msg_id2 = await _setup_message(r, env2)
        await _analyze_player(r, cfg, "worker-2", msg_id2, env2, log)

        # Stats should be unchanged — no double-counting
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "2"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "13"


class TestPlayerStatsTTL:
    """P10-DB-1: player stats/champions/roles/cursor keys get 30-day TTL."""

    _30_DAYS = 30 * 24 * 3600  # 2592000

    @pytest.mark.asyncio
    async def test_stats_key_has_ttl_after_analysis(self, r, cfg, log):
        """player:stats:{puuid} gets 30-day TTL after analysis."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        ttl = await r.ttl(f"player:stats:{puuid}")
        assert 0 < ttl <= self._30_DAYS

    @pytest.mark.asyncio
    async def test_champions_key_has_ttl_after_analysis(self, r, cfg, log):
        """player:champions:{puuid} gets 30-day TTL after analysis."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, champion="Annie")
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        ttl = await r.ttl(f"player:champions:{puuid}")
        assert 0 < ttl <= self._30_DAYS

    @pytest.mark.asyncio
    async def test_roles_key_has_ttl_after_analysis(self, r, cfg, log):
        """player:roles:{puuid} gets 30-day TTL after analysis."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, role="SOLO")
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        ttl = await r.ttl(f"player:roles:{puuid}")
        assert 0 < ttl <= self._30_DAYS

    @pytest.mark.asyncio
    async def test_cursor_key_has_ttl_after_analysis(self, r, cfg, log):
        """player:stats:cursor:{puuid} gets 30-day TTL after analysis."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "my-worker", msg_id, env, log)

        ttl = await r.ttl(f"player:stats:cursor:{puuid}")
        assert 0 < ttl <= self._30_DAYS

    @pytest.mark.asyncio
    async def test_ttl_refreshed_on_subsequent_analysis(self, r, cfg, log):
        """TTL is refreshed each time the player is analyzed (active players stay fresh)."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env1 = _analyze_envelope(puuid)
        msg_id1 = await _setup_message(r, env1)
        await _analyze_player(r, cfg, "w1", msg_id1, env1, log)

        # Manually reduce TTL to simulate time passing
        await r.expire(f"player:stats:{puuid}", 100)
        ttl_before = await r.ttl(f"player:stats:{puuid}")
        assert ttl_before <= 100

        # Add a new match and re-analyze
        await _add_participant(r, "NA1_2", puuid, 2000, kills=3, deaths=2, assists=7)
        env2 = _analyze_envelope(puuid)
        msg_id2 = await _setup_message(r, env2)
        await _analyze_player(r, cfg, "w2", msg_id2, env2, log)

        ttl_after = await r.ttl(f"player:stats:{puuid}")
        assert ttl_after > 100  # TTL was refreshed back to ~30 days


class TestAnalyzerExpirePipeline:
    """P14-OPT-1: Analyzer batches 4 EXPIRE calls into a single pipeline."""

    @pytest.mark.asyncio
    async def test_expire_not_called_directly_on_r(self, r, cfg, log):
        """The 4 EXPIRE calls for player stat keys use a pipeline, not individual calls."""
        puuid = "test-puuid-0001"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        direct_expire_calls: list[str] = []
        original_expire = r.expire

        async def tracking_expire(key, *args, **kwargs):
            direct_expire_calls.append(key)
            return await original_expire(key, *args, **kwargs)

        r.expire = tracking_expire

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        # Filter to only stat-key expire calls
        stat_expire_calls = [
            k
            for k in direct_expire_calls
            if "player:stats" in k or "player:champions" in k or "player:roles" in k
        ]
        assert stat_expire_calls == [], (
            f"Expected 0 direct expire calls for stat keys, got: {stat_expire_calls}"
        )
        # Verify TTLs were still set (via pipeline)
        assert await r.ttl(f"player:stats:{puuid}") > 0
        assert await r.ttl(f"player:champions:{puuid}") > 0
        assert await r.ttl(f"player:roles:{puuid}") > 0


class TestProcessMatchesSkipsEmptyParticipant:
    """T16-2: _process_matches skips entries where participant data is empty."""

    @pytest.mark.asyncio
    async def test_process_matches__skips_empty_participant_data(self, r, cfg, log):
        """Empty participant dicts are skipped — stats only count non-empty entries."""
        puuid = "test-puuid-skip"
        worker_id = "skip-worker"
        lock_key = f"player:stats:lock:{puuid}"
        lock_ttl_ms = cfg.analyzer_lock_ttl_seconds * 1000

        # Acquire lock
        await r.set(lock_key, worker_id, nx=True, px=lock_ttl_ms)

        # 3 matches, but middle one has empty participant data
        new_matches = [
            ("NA1_A", 1000.0),
            ("NA1_B", 2000.0),
            ("NA1_C", 3000.0),
        ]
        participant_data = [
            {
                "win": "1", "kills": "5", "deaths": "1", "assists": "3",
                "champion_name": "Annie", "role": "SOLO",
            },
            {},  # empty — should be skipped
            {
                "win": "0", "kills": "2", "deaths": "4", "assists": "1",
                "champion_name": "Jinx", "role": "DUO",
            },
        ]

        result = await _process_matches(
            r, puuid, new_matches, participant_data,
            lock_key, worker_id, lock_ttl_ms, log,
        )

        assert result is True
        # Only 2 matches counted (empty dict skipped)
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "2"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "7"
        assert await r.hget(f"player:stats:{puuid}", "total_deaths") == "5"
        assert await r.hget(f"player:stats:{puuid}", "total_assists") == "4"
        # Cursor should advance to the last match (3000), not the skipped one (2000)
        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert float(cursor) == 3000.0
        # Champions: Annie=1, Jinx=1 (skipped match not counted)
        assert await r.zscore(f"player:champions:{puuid}", "Annie") == 1.0
        assert await r.zscore(f"player:champions:{puuid}", "Jinx") == 1.0

    @pytest.mark.asyncio
    async def test_process_matches__all_empty__no_stats_written(self, r, cfg, log):
        """When all participant entries are empty, no stats are written."""
        puuid = "test-puuid-all-empty"
        worker_id = "empty-worker"
        lock_key = f"player:stats:lock:{puuid}"
        lock_ttl_ms = cfg.analyzer_lock_ttl_seconds * 1000

        await r.set(lock_key, worker_id, nx=True, px=lock_ttl_ms)

        new_matches = [("NA1_X", 1000.0), ("NA1_Y", 2000.0)]
        participant_data = [{}, {}]  # both empty

        result = await _process_matches(
            r, puuid, new_matches, participant_data,
            lock_key, worker_id, lock_ttl_ms, log,
        )

        assert result is True
        # No stats should have been written
        assert await r.hget(f"player:stats:{puuid}", "total_games") is None

    @pytest.mark.asyncio
    async def test_process_matches__first_empty_rest_valid(self, r, cfg, log):
        """First entry empty, second valid — only second counted."""
        puuid = "test-puuid-first-empty"
        worker_id = "first-empty-worker"
        lock_key = f"player:stats:lock:{puuid}"
        lock_ttl_ms = cfg.analyzer_lock_ttl_seconds * 1000

        await r.set(lock_key, worker_id, nx=True, px=lock_ttl_ms)

        new_matches = [("NA1_E", 500.0), ("NA1_F", 1500.0)]
        participant_data = [
            {},  # empty — skipped
            {
                "win": "1", "kills": "10", "deaths": "0", "assists": "5",
                "champion_name": "Lux", "role": "SUPPORT",
            },
        ]

        result = await _process_matches(
            r, puuid, new_matches, participant_data,
            lock_key, worker_id, lock_ttl_ms, log,
        )

        assert result is True
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "10"
        # Cursor at second match
        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert float(cursor) == 1500.0


class TestDerivedEdgeCases:
    """Edge cases for the _derived() helper function."""

    def test_derived__deaths_zero__kda_uses_max_1(self):
        """When deaths=0, KDA uses max(deaths, 1) to avoid ZeroDivisionError."""
        stats = {
            "total_games": "1",
            "total_wins": "1",
            "total_kills": "10",
            "total_deaths": "0",
            "total_assists": "5",
        }
        result = _derived(stats)
        # KDA = (10 + 5) / max(0, 1) = 15.0
        assert result["kda"] == "15.0000"

    def test_derived__total_games_zero__returns_empty(self):
        """When total_games=0, _derived returns empty dict."""
        stats = {
            "total_games": "0",
            "total_wins": "0",
            "total_kills": "0",
            "total_deaths": "0",
            "total_assists": "0",
        }
        result = _derived(stats)
        assert result == {}

    def test_derived__no_total_games_key__returns_empty(self):
        """When total_games is missing, defaults to 0 and returns empty dict."""
        result = _derived({})
        assert result == {}

    def test_derived__very_large_values__no_exception(self):
        """Very large stat values do not cause overflow or exceptions."""
        stats = {
            "total_games": "999999999",
            "total_wins": "500000000",
            "total_kills": "999999999",
            "total_deaths": "999999999",
            "total_assists": "999999999",
        }
        result = _derived(stats)
        assert "win_rate" in result
        assert "kda" in result
        assert "avg_kills" in result
        # win_rate should be ~0.5000
        assert result["win_rate"] == "0.5000"
        # kda = (999999999 + 999999999) / max(999999999, 1) = 2.0
        assert result["kda"] == "2.0000"

    def test_derived__all_zeros_except_games__returns_zero_values(self):
        """When all stats are zero except total_games, all derived values are 0."""
        stats = {
            "total_games": "5",
            "total_wins": "0",
            "total_kills": "0",
            "total_deaths": "0",
            "total_assists": "0",
        }
        result = _derived(stats)
        assert result["win_rate"] == "0.0000"
        assert result["avg_kills"] == "0.0000"
        assert result["avg_deaths"] == "0.0000"
        assert result["avg_assists"] == "0.0000"
        # kda = (0 + 0) / max(0, 1) = 0.0
        assert result["kda"] == "0.0000"

    def test_derived__single_game__correct_averages(self):
        """Single game produces correct per-game averages."""
        stats = {
            "total_games": "1",
            "total_wins": "1",
            "total_kills": "7",
            "total_deaths": "3",
            "total_assists": "11",
        }
        result = _derived(stats)
        assert result["win_rate"] == "1.0000"
        assert result["avg_kills"] == "7.0000"
        assert result["avg_deaths"] == "3.0000"
        assert result["avg_assists"] == "11.0000"
        # kda = (7 + 11) / max(3, 1) = 6.0
        assert result["kda"] == "6.0000"


async def _add_ranked_participant(  # noqa: PLR0913
    r,
    match_id,
    puuid,
    game_start,
    *,
    win=True,
    kills=10,
    deaths=2,
    assists=5,
    champion="Annie",
    team_position="TOP",
    patch="14.5",
    queue_id="420",
    gold_earned=12000,
    total_minions_killed=180,
    total_damage_dealt_to_champions=25000,
    vision_score=30,
    double_kills=1,
    triple_kills=0,
    quadra_kills=0,
    penta_kills=0,
):
    """Write participant + match metadata for a ranked match and add to sorted set."""
    await r.hset(
        f"participant:{match_id}:{puuid}",
        mapping={
            "champion_name": champion,
            "team_position": team_position,
            "role": "SOLO",
            "win": "1" if win else "0",
            "kills": str(kills),
            "deaths": str(deaths),
            "assists": str(assists),
            "gold_earned": str(gold_earned),
            "total_minions_killed": str(total_minions_killed),
            "total_damage_dealt_to_champions": str(total_damage_dealt_to_champions),
            "vision_score": str(vision_score),
            "double_kills": str(double_kills),
            "triple_kills": str(triple_kills),
            "quadra_kills": str(quadra_kills),
            "penta_kills": str(penta_kills),
        },
    )
    await r.hset(
        f"match:{match_id}",
        mapping={
            "queue_id": str(queue_id),
            "patch": patch,
            "game_mode": "CLASSIC",
            "duration": "1800",
        },
    )
    await r.zadd(f"player:matches:{puuid}", {match_id: float(game_start)})


class TestChampionStatsAggregation:
    """Champion aggregate stats updated for ranked matches during analysis."""

    _90_DAYS = 90 * 24 * 3600

    @pytest.mark.asyncio
    async def test_champion_stats_ranked_match_updates(self, r, cfg, log):
        """Ranked match (queue_id=420) with valid patch/position increments champion stats."""
        puuid = "test-puuid-champ"
        await _add_ranked_participant(
            r, "NA1_R1", puuid, 1000,
            champion="Annie", team_position="MID", patch="14.5",
            kills=8, deaths=3, assists=5, win=True,
            gold_earned=14000, total_minions_killed=200,
            total_damage_dealt_to_champions=30000, vision_score=25,
            double_kills=2, triple_kills=1, quadra_kills=0, penta_kills=0,
        )
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        stats = await r.hgetall("champion:stats:Annie:14.5:MID")
        assert stats["games"] == "1"
        assert stats["wins"] == "1"
        assert stats["kills"] == "8"
        assert stats["deaths"] == "3"
        assert stats["assists"] == "5"
        assert stats["gold"] == "14000"
        assert stats["cs"] == "200"
        assert stats["damage"] == "30000"
        assert stats["vision"] == "25"
        assert stats["double_kills"] == "2"
        assert stats["triple_kills"] == "1"
        assert stats["quadra_kills"] == "0"
        assert stats["penta_kills"] == "0"

    @pytest.mark.asyncio
    async def test_champion_stats_skips_non_ranked(self, r, cfg, log):
        """queue_id != 420 does not update champion stats."""
        puuid = "test-puuid-aram"
        await _add_ranked_participant(
            r, "NA1_ARAM", puuid, 1000,
            champion="Annie", team_position="MID", patch="14.5",
            queue_id="450",  # ARAM
        )
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        assert not await r.exists("champion:stats:Annie:14.5:MID")

    @pytest.mark.asyncio
    async def test_champion_stats_skips_missing_patch(self, r, cfg, log):
        """Empty patch string skips champion stats update."""
        puuid = "test-puuid-nopatch"
        await _add_ranked_participant(
            r, "NA1_NP", puuid, 1000,
            champion="Annie", team_position="MID", patch="",
        )
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        # No champion stats keys should exist for empty patch
        assert not await r.exists("champion:stats:Annie::MID")

    @pytest.mark.asyncio
    async def test_champion_stats_skips_missing_position(self, r, cfg, log):
        """Empty team_position skips champion stats update."""
        puuid = "test-puuid-nopos"
        await _add_ranked_participant(
            r, "NA1_NP2", puuid, 1000,
            champion="Annie", team_position="", patch="14.5",
        )
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        # Index should not have any entries
        assert await r.zcard("champion:index:14.5") == 0

    @pytest.mark.asyncio
    async def test_champion_stats_skips_empty_participant(self, r, cfg, log):
        """Empty participant data skips champion stats update."""
        puuid = "test-puuid-empty-p"
        # Set up match metadata without participant data
        await r.hset(
            "match:NA1_EP",
            mapping={"queue_id": "420", "patch": "14.5", "game_mode": "CLASSIC"},
        )
        await r.zadd(f"player:matches:{puuid}", {"NA1_EP": 1000.0})
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        assert await r.zcard("champion:index:14.5") == 0

    @pytest.mark.asyncio
    async def test_champion_index_incremented(self, r, cfg, log):
        """champion:index:{patch} ZINCRBY tracks champion:position combinations."""
        puuid = "test-puuid-idx"
        await _add_ranked_participant(
            r, "NA1_I1", puuid, 1000,
            champion="Annie", team_position="MID", patch="14.5",
        )
        await _add_ranked_participant(
            r, "NA1_I2", puuid, 2000,
            champion="Annie", team_position="MID", patch="14.5",
        )
        await _add_ranked_participant(
            r, "NA1_I3", puuid, 3000,
            champion="Jinx", team_position="BOTTOM", patch="14.5",
        )
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        assert await r.zscore("champion:index:14.5", "Annie:MID") == 2.0
        assert await r.zscore("champion:index:14.5", "Jinx:BOTTOM") == 1.0

    @pytest.mark.asyncio
    async def test_patch_list_recorded(self, r, cfg, log):
        """patch:list ZADD NX records patch with game_start as score."""
        puuid = "test-puuid-patch"
        await _add_ranked_participant(
            r, "NA1_P1", puuid, 5000,
            champion="Annie", team_position="MID", patch="14.5",
        )
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        # patch:list should contain "14.5" with score 5000
        score = await r.zscore("patch:list", "14.5")
        assert score == 5000.0

    @pytest.mark.asyncio
    async def test_patch_list_nx_does_not_overwrite(self, r, cfg, log):
        """patch:list ZADD NX keeps the first score, does not overwrite."""
        puuid = "test-puuid-pnx"
        # Pre-set patch:list with an earlier score
        await r.zadd("patch:list", {"14.5": 1000.0})

        await _add_ranked_participant(
            r, "NA1_PNX", puuid, 9000,
            champion="Annie", team_position="MID", patch="14.5",
        )
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        # Score should still be 1000 (NX = don't overwrite)
        score = await r.zscore("patch:list", "14.5")
        assert score == 1000.0

    @pytest.mark.asyncio
    async def test_champion_stats_ttl_set(self, r, cfg, log):
        """Champion stats and index keys get 90-day TTL."""
        puuid = "test-puuid-ttl"
        await _add_ranked_participant(
            r, "NA1_T1", puuid, 1000,
            champion="Annie", team_position="MID", patch="14.5",
        )
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        stats_ttl = await r.ttl("champion:stats:Annie:14.5:MID")
        assert 0 < stats_ttl <= self._90_DAYS

        index_ttl = await r.ttl("champion:index:14.5")
        assert 0 < index_ttl <= self._90_DAYS

    @pytest.mark.asyncio
    async def test_champion_stats_accumulates_across_matches(self, r, cfg, log):
        """Multiple ranked matches on same champion/patch/role accumulate stats."""
        puuid = "test-puuid-accum"
        await _add_ranked_participant(
            r, "NA1_AC1", puuid, 1000,
            champion="Annie", team_position="MID", patch="14.5",
            kills=5, deaths=2, assists=3, win=True,
            gold_earned=10000, total_minions_killed=150,
            total_damage_dealt_to_champions=20000, vision_score=20,
            double_kills=1, triple_kills=0, quadra_kills=0, penta_kills=0,
        )
        await _add_ranked_participant(
            r, "NA1_AC2", puuid, 2000,
            champion="Annie", team_position="MID", patch="14.5",
            kills=3, deaths=4, assists=7, win=False,
            gold_earned=8000, total_minions_killed=120,
            total_damage_dealt_to_champions=15000, vision_score=18,
            double_kills=0, triple_kills=1, quadra_kills=0, penta_kills=0,
        )
        env = _analyze_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await _analyze_player(r, cfg, "w1", msg_id, env, log)

        stats = await r.hgetall("champion:stats:Annie:14.5:MID")
        assert stats["games"] == "2"
        assert stats["wins"] == "1"
        assert stats["kills"] == "8"
        assert stats["deaths"] == "6"
        assert stats["assists"] == "10"
        assert stats["gold"] == "18000"
        assert stats["cs"] == "270"
        assert stats["damage"] == "35000"
        assert stats["vision"] == "38"
        assert stats["double_kills"] == "1"
        assert stats["triple_kills"] == "1"


class TestUpdateChampionStatsDirect:
    """Direct unit tests for _update_champion_stats function."""

    @pytest.mark.asyncio
    async def test_update_champion_stats_skips_empty_meta(self, r):
        """Empty match metadata causes skip — no champion stats written."""
        new_matches = [("NA1_X", 1000.0)]
        participant_data = [{"champion_name": "Annie", "team_position": "MID", "win": "1"}]
        match_metadata = [{}]

        await _update_champion_stats(r, new_matches, participant_data, match_metadata)

        assert await r.zcard("champion:index:14.5") == 0

    @pytest.mark.asyncio
    async def test_update_champion_stats_skips_missing_champion_name(self, r):
        """Missing champion_name in participant data causes skip."""
        new_matches = [("NA1_X", 1000.0)]
        participant_data = [{"team_position": "MID", "win": "1"}]
        match_metadata = [{"queue_id": "420", "patch": "14.5"}]

        await _update_champion_stats(r, new_matches, participant_data, match_metadata)

        assert await r.zcard("champion:index:14.5") == 0
