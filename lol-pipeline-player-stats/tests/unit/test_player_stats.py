"""Unit tests for lol_player_stats.main — STRUCT-2 Red step.

Tests the player-stats service that will be extracted from lol-pipeline-analyzer.
This service consumes stream:analyze, writes player:stats:{puuid},
player:champions:{puuid}, player:roles:{puuid}, and manages cursor + lock.
Consumer group: player-stats-workers.
"""

from __future__ import annotations

import logging

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import consume, publish

from lol_player_stats.main import handle_player_stats

_IN_STREAM = "stream:analyze"
_GROUP = "player-stats-workers"

try:
    import lupa  # noqa: F401

    _LUPA_AVAILABLE = True
except ImportError:
    _LUPA_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua scripts")


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
    return logging.getLogger("test-player-stats")


def _player_envelope(puuid="test-puuid-0001"):
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
            "team_position": role,
            "win": "1" if win else "0",
            "kills": str(kills),
            "deaths": str(deaths),
            "assists": str(assists),
        },
    )
    await r.zadd(f"player:matches:{puuid}", {match_id: float(game_start)})


# ---------------------------------------------------------------------------
# Player KDA Aggregation
# ---------------------------------------------------------------------------


class TestPlayerKdaAggregation:
    """Player stats handler aggregates kills, deaths, assists across matches."""

    @pytest.mark.asyncio
    async def test_player_stats__single_match__correct_totals(self, r, cfg, log):
        """Single match: total_kills, total_deaths, total_assists match participant data."""
        puuid = "test-puuid-kda-1"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=8, deaths=3, assists=5)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        stats = await r.hgetall(f"player:stats:{puuid}")
        assert stats["total_games"] == "1"
        assert stats["total_kills"] == "8"
        assert stats["total_deaths"] == "3"
        assert stats["total_assists"] == "5"

    @pytest.mark.asyncio
    async def test_player_stats__three_matches__averaged_kda(self, r, cfg, log):
        """Three matches: derived avg_kills, avg_deaths, avg_assists, kda are computed."""
        puuid = "test-puuid-kda-3"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=10, deaths=2, assists=6)
        await _add_participant(r, "NA1_2", puuid, 2000, kills=4, deaths=5, assists=8)
        await _add_participant(r, "NA1_3", puuid, 3000, kills=1, deaths=3, assists=1)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        stats = await r.hgetall(f"player:stats:{puuid}")
        assert stats["total_games"] == "3"
        assert stats["total_kills"] == "15"
        assert stats["total_deaths"] == "10"
        assert stats["total_assists"] == "15"
        # avg_kills = 15/3 = 5.0000
        assert stats["avg_kills"] == "5.0000"
        # avg_deaths = 10/3 = 3.3333
        assert stats["avg_deaths"] == "3.3333"
        # avg_assists = 15/3 = 5.0000
        assert stats["avg_assists"] == "5.0000"
        # kda = (15 + 15) / max(10, 1) = 3.0000
        assert stats["kda"] == "3.0000"

    @pytest.mark.asyncio
    async def test_player_stats__zero_deaths__kda_uses_max_1(self, r, cfg, log):
        """Zero deaths: KDA = (kills + assists) / max(0, 1) = kills + assists."""
        puuid = "test-puuid-kda-0d"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=12, deaths=0, assists=8)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "kda") == "20.0000"


# ---------------------------------------------------------------------------
# Win Rate Calculation
# ---------------------------------------------------------------------------


class TestPlayerWinRate:
    """Win rate derived from total_wins / total_games."""

    @pytest.mark.asyncio
    async def test_player_stats__all_wins__win_rate_1(self, r, cfg, log):
        """All wins: win_rate = 1.0000."""
        puuid = "test-puuid-wr-all"
        await _add_participant(r, "NA1_1", puuid, 1000, win=True, kills=5, deaths=1, assists=3)
        await _add_participant(r, "NA1_2", puuid, 2000, win=True, kills=3, deaths=2, assists=7)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "win_rate") == "1.0000"

    @pytest.mark.asyncio
    async def test_player_stats__mixed_wins__correct_rate(self, r, cfg, log):
        """1 win out of 3: win_rate = 0.3333."""
        puuid = "test-puuid-wr-mix"
        await _add_participant(r, "NA1_1", puuid, 1000, win=True, kills=5, deaths=1, assists=3)
        await _add_participant(r, "NA1_2", puuid, 2000, win=False, kills=2, deaths=4, assists=1)
        await _add_participant(r, "NA1_3", puuid, 3000, win=False, kills=1, deaths=3, assists=2)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "win_rate") == "0.3333"

    @pytest.mark.asyncio
    async def test_player_stats__no_wins__win_rate_0(self, r, cfg, log):
        """All losses: win_rate = 0.0000."""
        puuid = "test-puuid-wr-none"
        await _add_participant(r, "NA1_1", puuid, 1000, win=False, kills=1, deaths=5, assists=0)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "win_rate") == "0.0000"


# ---------------------------------------------------------------------------
# Cursor-Based Processing
# ---------------------------------------------------------------------------


class TestPlayerStatsCursor:
    """Cursor tracks last-processed match; only new matches are processed."""

    @pytest.mark.asyncio
    async def test_player_stats__no_cursor__processes_all(self, r, cfg, log):
        """No cursor: all matches in player:matches are processed."""
        puuid = "test-puuid-cur-all"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        await _add_participant(r, "NA1_2", puuid, 2000, kills=3, deaths=2, assists=7)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "total_games") == "2"
        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert float(cursor) == 2000.0

    @pytest.mark.asyncio
    async def test_player_stats__cursor_set__only_new_matches(self, r, cfg, log):
        """Cursor at 1000: only matches with score > 1000 are processed."""
        puuid = "test-puuid-cur-new"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        await _add_participant(r, "NA1_2", puuid, 2000, kills=3, deaths=2, assists=7)

        # First run processes both
        env1 = _player_envelope(puuid)
        msg_id1 = await _setup_message(r, env1)
        await handle_player_stats(r, cfg, "w1", msg_id1, env1, log)
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "2"

        # Add a new match after cursor
        await _add_participant(r, "NA1_3", puuid, 3000, kills=7, deaths=0, assists=4)
        env2 = _player_envelope(puuid)
        msg_id2 = await _setup_message(r, env2)
        await handle_player_stats(r, cfg, "w2", msg_id2, env2, log)

        # Only the new match was processed, accumulating onto existing stats
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "3"
        assert await r.hget(f"player:stats:{puuid}", "total_kills") == "15"
        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert float(cursor) == 3000.0

    @pytest.mark.asyncio
    async def test_player_stats__cursor_at_max__no_new_matches(self, r, cfg, log):
        """Cursor at highest score: no new matches to process."""
        puuid = "test-puuid-cur-max"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        await r.set(f"player:stats:cursor:{puuid}", "1000")
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        # No new stats should be written
        assert await r.hget(f"player:stats:{puuid}", "total_games") is None

    @pytest.mark.asyncio
    async def test_player_stats__cursor_advances_to_highest_score(self, r, cfg, log):
        """Cursor ends at the highest score among processed matches."""
        puuid = "test-puuid-cur-hi"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=2, deaths=1, assists=1)
        await _add_participant(r, "NA1_2", puuid, 2000, kills=3, deaths=0, assists=2)
        await _add_participant(r, "NA1_3", puuid, 3000, kills=1, deaths=1, assists=4)
        await _add_participant(r, "NA1_4", puuid, 4000, kills=5, deaths=2, assists=3)
        await _add_participant(r, "NA1_5", puuid, 5000, kills=0, deaths=3, assists=0)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "w1", msg_id, env, log)

        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert float(cursor) == 5000.0
        assert await r.hget(f"player:stats:{puuid}", "total_games") == "5"


# ---------------------------------------------------------------------------
# Distributed Lock
# ---------------------------------------------------------------------------


class TestPlayerStatsLock:
    """Distributed lock prevents concurrent processing of the same player."""

    @pytest.mark.asyncio
    async def test_player_stats__lock_held__discards_and_acks(self, r, cfg, log):
        """Lock held by another worker: no stats update, but message is ACKed."""
        puuid = "test-puuid-lock-held"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        # Another worker holds the lock
        await r.set(f"player:stats:lock:{puuid}", "other-worker", nx=True, px=30000)

        await handle_player_stats(r, cfg, "my-worker", msg_id, env, log)

        # No stats written
        assert await r.hget(f"player:stats:{puuid}", "total_games") is None
        # Message ACKed (removed from PEL)
        pending = await r.xpending_range(_IN_STREAM, _GROUP, min="-", max="+", count=10)
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_player_stats__lock_acquired__processes_and_releases(self, r, cfg, log):
        """Lock acquired: stats written and lock released after processing."""
        puuid = "test-puuid-lock-acq"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "my-worker", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "total_games") == "1"
        # Lock should be released
        assert await r.exists(f"player:stats:lock:{puuid}") == 0


# ---------------------------------------------------------------------------
# player:stats:{puuid} Hash Fields
# ---------------------------------------------------------------------------


class TestPlayerStatsHash:
    """player:stats:{puuid} hash contains correct accumulated and derived fields."""

    @pytest.mark.asyncio
    async def test_player_stats__hash_has_all_required_fields(self, r, cfg, log):
        """Hash contains total_games, total_wins, total_kills, total_deaths,
        total_assists, win_rate, avg_kills, avg_deaths, avg_assists, kda."""
        puuid = "test-puuid-hash"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=10, deaths=3, assists=5, win=True)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        stats = await r.hgetall(f"player:stats:{puuid}")
        required_fields = {
            "total_games",
            "total_wins",
            "total_kills",
            "total_deaths",
            "total_assists",
            "win_rate",
            "avg_kills",
            "avg_deaths",
            "avg_assists",
            "kda",
        }
        assert required_fields.issubset(set(stats.keys())), (
            f"Missing fields: {required_fields - set(stats.keys())}"
        )

    @pytest.mark.asyncio
    async def test_player_stats__derived_precision_4_decimal(self, r, cfg, log):
        """All derived fields use .4f precision."""
        puuid = "test-puuid-prec"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=10, deaths=3, assists=5, win=True)
        await _add_participant(r, "NA1_2", puuid, 2000, kills=0, deaths=4, assists=0, win=False)
        await _add_participant(r, "NA1_3", puuid, 3000, kills=0, deaths=0, assists=0, win=False)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        stats = await r.hgetall(f"player:stats:{puuid}")
        # 1/3 = 0.3333
        assert stats["win_rate"] == "0.3333"
        # 10/3 = 3.3333
        assert stats["avg_kills"] == "3.3333"
        for field in ("win_rate", "avg_kills", "avg_deaths", "avg_assists", "kda"):
            assert len(stats[field].split(".")[1]) == 4, (
                f"{field}={stats[field]} does not have 4 decimal places"
            )

    @pytest.mark.asyncio
    async def test_player_stats__ttl_set_on_stats_key(self, r, cfg, log):
        """player:stats:{puuid} has 30-day TTL after processing."""
        puuid = "test-puuid-ttl"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        ttl = await r.ttl(f"player:stats:{puuid}")
        _30_days = 30 * 24 * 3600
        assert 0 < ttl <= _30_days


# ---------------------------------------------------------------------------
# player:champions:{puuid} Sorted Set
# ---------------------------------------------------------------------------


class TestPlayerChampionsSortedSet:
    """player:champions:{puuid} sorted set tracks champion play counts."""

    @pytest.mark.asyncio
    async def test_player_stats__champion_counts_accumulated(self, r, cfg, log):
        """3 games on Annie, 2 on Jinx: Annie=3, Jinx=2 in sorted set."""
        puuid = "test-puuid-champs"
        for i in range(3):
            await _add_participant(r, f"NA1_A{i}", puuid, 1000 + i, champion="Annie")
        for i in range(2):
            await _add_participant(r, f"NA1_J{i}", puuid, 2000 + i, champion="Jinx")
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        assert await r.zscore(f"player:champions:{puuid}", "Annie") == 3.0
        assert await r.zscore(f"player:champions:{puuid}", "Jinx") == 2.0

    @pytest.mark.asyncio
    async def test_player_stats__champion_key_has_ttl(self, r, cfg, log):
        """player:champions:{puuid} has 30-day TTL."""
        puuid = "test-puuid-champ-ttl"
        await _add_participant(r, "NA1_1", puuid, 1000, champion="Annie")
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        ttl = await r.ttl(f"player:champions:{puuid}")
        _30_days = 30 * 24 * 3600
        assert 0 < ttl <= _30_days


# ---------------------------------------------------------------------------
# player:roles:{puuid} Sorted Set
# ---------------------------------------------------------------------------


class TestPlayerRolesSortedSet:
    """player:roles:{puuid} sorted set tracks role play counts."""

    @pytest.mark.asyncio
    async def test_player_stats__role_counts_accumulated(self, r, cfg, log):
        """3 games MID, 2 games BOTTOM: role sorted set reflects counts."""
        puuid = "test-puuid-roles"
        for i in range(3):
            await _add_participant(r, f"NA1_M{i}", puuid, 1000 + i, role="MID")
        for i in range(2):
            await _add_participant(r, f"NA1_B{i}", puuid, 2000 + i, role="BOTTOM")
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        assert await r.zscore(f"player:roles:{puuid}", "MID") == 3.0
        assert await r.zscore(f"player:roles:{puuid}", "BOTTOM") == 2.0

    @pytest.mark.asyncio
    async def test_player_stats__roles_key_has_ttl(self, r, cfg, log):
        """player:roles:{puuid} has 30-day TTL."""
        puuid = "test-puuid-role-ttl"
        await _add_participant(r, "NA1_1", puuid, 1000, role="SOLO")
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        ttl = await r.ttl(f"player:roles:{puuid}")
        _30_days = 30 * 24 * 3600
        assert 0 < ttl <= _30_days


# ---------------------------------------------------------------------------
# system:halted Check
# ---------------------------------------------------------------------------


class TestPlayerStatsSystemHalted:
    """system:halted flag stops processing; message stays in PEL."""

    @pytest.mark.asyncio
    async def test_player_stats__system_halted__skips_processing(self, r, cfg, log):
        """system:halted set: no stats written."""
        puuid = "test-puuid-halt"
        await r.set("system:halted", "1")
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        assert await r.hget(f"player:stats:{puuid}", "total_games") is None

    @pytest.mark.asyncio
    async def test_player_stats__system_halted__preserves_pel(self, r, cfg, log):
        """system:halted: message NOT ACKed — stays in PEL for redelivery."""
        await r.set("system:halted", "1")
        env = _player_envelope()
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        pending = await r.xpending(_IN_STREAM, _GROUP)
        assert pending["pending"] == 1


# ---------------------------------------------------------------------------
# Proper Ack After Processing
# ---------------------------------------------------------------------------


class TestPlayerStatsAck:
    """Message is ACKed after successful processing or lock-discard."""

    @pytest.mark.asyncio
    async def test_player_stats__successful_processing__acks(self, r, cfg, log):
        """After processing, message is removed from PEL."""
        puuid = "test-puuid-ack-ok"
        await _add_participant(r, "NA1_1", puuid, 1000, kills=5, deaths=1, assists=3)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        pending = await r.xpending(_IN_STREAM, _GROUP)
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_player_stats__no_new_matches__still_acks(self, r, cfg, log):
        """Even with no new matches, message is ACKed."""
        puuid = "test-puuid-ack-noop"
        # Empty match history but message exists
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        pending = await r.xpending(_IN_STREAM, _GROUP)
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_player_stats__derived_recomputed_on_empty_run(self, r, cfg, log):
        """Derived stats recomputed even when no new matches (crash recovery)."""
        puuid = "test-puuid-ack-recover"
        # Simulate crashed state: raw stats exist, cursor is advanced
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
        await _add_participant(r, "NA1_1", puuid, 1000, kills=10, deaths=2, assists=5, win=True)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        # Derived stats should have been recomputed
        assert await r.hget(f"player:stats:{puuid}", "win_rate") == "1.0000"
        assert await r.hget(f"player:stats:{puuid}", "kda") == "7.5000"


# ---------------------------------------------------------------------------
# Serial EVAL Constraint (IMP-077)
# ---------------------------------------------------------------------------


class TestPlayerStatsSerialEval:
    """EVAL calls are serial — lock loss mid-batch aborts remaining matches."""

    @pytest.mark.asyncio
    async def test_player_stats__lock_lost_mid_batch__aborts_remaining(self, r, cfg, log):
        """If the lock is stolen between matches, processing stops immediately.

        This proves the serial EVAL design is necessary: each EVAL checks lock
        ownership and returns 0 on mismatch, so subsequent matches are skipped.
        Pipelining would not allow this early-abort behavior.
        """
        puuid = "test-puuid-serial"
        # Set up 3 matches
        await _add_participant(r, "NA1_S1", puuid, 1000, kills=5, deaths=1, assists=3)
        await _add_participant(r, "NA1_S2", puuid, 2000, kills=3, deaths=2, assists=7)
        await _add_participant(r, "NA1_S3", puuid, 3000, kills=1, deaths=1, assists=1)
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        # Acquire lock as "worker-1"
        lock_key = f"player:stats:lock:{puuid}"
        await r.set(lock_key, "worker-1", nx=True, px=30000)

        # Start processing — first EVAL will succeed
        # After first match, steal the lock by overwriting it
        original_eval = r.eval

        call_count = 0

        async def eval_with_lock_steal(*args, **kwargs):
            nonlocal call_count
            result = await original_eval(*args, **kwargs)
            call_count += 1
            if call_count == 1:
                # After first successful EVAL, steal the lock
                await r.set(lock_key, "thief-worker", px=30000)
            return result

        r.eval = eval_with_lock_steal

        # _process_matches is called inside handle_player_stats;
        # but the lock is acquired in handle_player_stats, so we need
        # to release it first and let handle_player_stats re-acquire
        await r.delete(lock_key)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        # Only 1 match should have been processed (lock stolen after first EVAL)
        stats = await r.hgetall(f"player:stats:{puuid}")
        assert stats.get("total_games") == "1", (
            f"Expected 1 match processed before lock theft, got {stats.get('total_games')}"
        )

    @pytest.mark.asyncio
    async def test_player_stats__multiple_matches__all_processed_in_order(self, r, cfg, log):
        """Multiple matches are processed sequentially; cursor advances to max score."""
        puuid = "test-puuid-serial-all"
        for i in range(5):
            await _add_participant(
                r, f"NA1_M{i}", puuid, (i + 1) * 1000,
                kills=2, deaths=1, assists=1, champion="Annie", role="MID",
            )
        env = _player_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_player_stats(r, cfg, "worker-1", msg_id, env, log)

        stats = await r.hgetall(f"player:stats:{puuid}")
        assert stats["total_games"] == "5"
        assert stats["total_kills"] == "10"  # 2 * 5
        cursor = await r.get(f"player:stats:cursor:{puuid}")
        assert float(cursor) == 5000.0
        assert await r.zscore(f"player:champions:{puuid}", "Annie") == 5.0
        assert await r.zscore(f"player:roles:{puuid}", "MID") == 5.0
