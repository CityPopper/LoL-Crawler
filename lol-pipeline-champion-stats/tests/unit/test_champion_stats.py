"""Unit tests for lol_champion_stats.main — STRUCT-2 Red step.

Tests the champion-stats service that will be extracted from lol-pipeline-analyzer.
This service consumes stream:analyze, writes champion:stats:{champion}:{patch}:{role},
champion:builds, champion:runes, champion:spells, matchup aggregation, and
champion:index:{patch}.
Consumer group: champion-stats-workers.
"""

from __future__ import annotations

import json
import logging

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import consume, publish

from lol_champion_stats.main import handle_champion_stats

_IN_STREAM = "stream:analyze"
_GROUP = "champion-stats-workers"

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
    return logging.getLogger("test-champion-stats")


def _champion_envelope(puuid="test-puuid-0001"):
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
    items=None,
    perk_keystone="0",
    summoner1_id="0",
    summoner2_id="0",
):
    """Write participant + match metadata for a ranked match and add to sorted set."""
    mapping = {
        "champion_name": champion,
        "team_position": team_position,
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
        "perk_keystone": perk_keystone,
        "summoner1_id": summoner1_id,
        "summoner2_id": summoner2_id,
    }
    if items is not None:
        mapping["items"] = json.dumps(items)
    await r.hset(f"participant:{match_id}:{puuid}", mapping=mapping)
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


# ---------------------------------------------------------------------------
# Per-Patch Per-Role Champion Win Rate
# ---------------------------------------------------------------------------


class TestChampionWinRate:
    """champion:stats:{champion}:{patch}:{role} tracks per-patch win rate."""

    @pytest.mark.asyncio
    async def test_champion_stats__single_win__games_1_wins_1(self, r, cfg, log):
        """Single ranked win: games=1, wins=1 in champion:stats hash."""
        puuid = "test-puuid-cwr-1"
        await _add_ranked_participant(
            r,
            "NA1_1",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            kills=8,
            deaths=3,
            assists=5,
            win=True,
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        stats = await r.hgetall("champion:stats:Annie:14.5:MID")
        assert stats["games"] == "1"
        assert stats["wins"] == "1"

    @pytest.mark.asyncio
    async def test_champion_stats__two_matches__accumulates(self, r, cfg, log):
        """Two ranked matches on same champion/patch/role: stats accumulate."""
        puuid = "test-puuid-cwr-2"
        await _add_ranked_participant(
            r,
            "NA1_1",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            kills=5,
            deaths=2,
            assists=3,
            win=True,
            gold_earned=10000,
            total_minions_killed=150,
            total_damage_dealt_to_champions=20000,
            vision_score=20,
            double_kills=1,
            triple_kills=0,
            quadra_kills=0,
            penta_kills=0,
        )
        await _add_ranked_participant(
            r,
            "NA1_2",
            puuid,
            2000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            kills=3,
            deaths=4,
            assists=7,
            win=False,
            gold_earned=8000,
            total_minions_killed=120,
            total_damage_dealt_to_champions=15000,
            vision_score=18,
            double_kills=0,
            triple_kills=1,
            quadra_kills=0,
            penta_kills=0,
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

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

    @pytest.mark.asyncio
    async def test_champion_stats__non_ranked_skipped(self, r, cfg, log):
        """queue_id != 420 (ARAM etc.): no champion:stats key created."""
        puuid = "test-puuid-cwr-aram"
        await _add_ranked_participant(
            r,
            "NA1_ARAM",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            queue_id="450",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        assert not await r.exists("champion:stats:Annie:14.5:MID")

    @pytest.mark.asyncio
    async def test_champion_stats__missing_patch_skipped(self, r, cfg, log):
        """Empty patch: no champion:stats key created."""
        puuid = "test-puuid-cwr-nop"
        await _add_ranked_participant(
            r,
            "NA1_NP",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        assert not await r.exists("champion:stats:Annie::MID")

    @pytest.mark.asyncio
    async def test_champion_stats__missing_position_skipped(self, r, cfg, log):
        """Empty team_position: no champion:stats key created."""
        puuid = "test-puuid-cwr-nopos"
        await _add_ranked_participant(
            r,
            "NA1_NOPOS",
            puuid,
            1000,
            champion="Annie",
            team_position="",
            patch="14.5",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        assert await r.zcard("champion:index:14.5") == 0


# ---------------------------------------------------------------------------
# Build Aggregation
# ---------------------------------------------------------------------------


class TestChampionBuilds:
    """champion:builds:{champion}:{patch}:{role} aggregates item build fingerprints."""

    @pytest.mark.asyncio
    async def test_champion_stats__build_fingerprint_written(self, r, cfg, log):
        """Item build fingerprint (sorted non-zero IDs) ZINCRBY'd in builds key."""
        puuid = "test-puuid-build-1"
        await _add_ranked_participant(
            r,
            "NA1_B1",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            items=[3157, 3089, 0, 3020, 0, 3116, 0],
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        builds_key = "champion:builds:Annie:14.5:MID"
        # Sorted non-zero: 3020,3089,3116,3157
        score = await r.zscore(builds_key, "3020,3089,3116,3157")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_champion_stats__build_accumulates_across_matches(self, r, cfg, log):
        """Same build across two matches: score incremented to 2."""
        puuid = "test-puuid-build-2"
        items = [3157, 3089, 3020]
        await _add_ranked_participant(
            r,
            "NA1_B1",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            items=items,
        )
        await _add_ranked_participant(
            r,
            "NA1_B2",
            puuid,
            2000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            items=items,
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        builds_key = "champion:builds:Annie:14.5:MID"
        score = await r.zscore(builds_key, "3020,3089,3157")
        assert score == 2.0

    @pytest.mark.asyncio
    async def test_champion_stats__all_zero_items_no_build(self, r, cfg, log):
        """All-zero items: no build fingerprint written."""
        puuid = "test-puuid-build-0"
        await _add_ranked_participant(
            r,
            "NA1_B0",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            items=[0, 0, 0, 0, 0, 0, 0],
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        builds_key = "champion:builds:Annie:14.5:MID"
        assert await r.zcard(builds_key) == 0

    @pytest.mark.asyncio
    async def test_champion_stats__builds_key_has_ttl(self, r, cfg, log):
        """champion:builds key has CHAMPION_STATS_TTL_SECONDS TTL (90 days)."""
        puuid = "test-puuid-build-ttl"
        await _add_ranked_participant(
            r,
            "NA1_BT",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            items=[3157, 3089],
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        _90_days = 90 * 24 * 3600
        ttl = await r.ttl("champion:builds:Annie:14.5:MID")
        assert 0 < ttl <= _90_days


# ---------------------------------------------------------------------------
# Rune Aggregation
# ---------------------------------------------------------------------------


class TestChampionRunes:
    """champion:runes:{champion}:{patch}:{role} aggregates keystone rune usage."""

    @pytest.mark.asyncio
    async def test_champion_stats__keystone_rune_written(self, r, cfg, log):
        """Keystone rune ID ZINCRBY'd in runes key."""
        puuid = "test-puuid-rune-1"
        await _add_ranked_participant(
            r,
            "NA1_R1",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            perk_keystone="8229",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        runes_key = "champion:runes:Annie:14.5:MID"
        score = await r.zscore(runes_key, "8229")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_champion_stats__rune_zero_skipped(self, r, cfg, log):
        """perk_keystone=0: no entry in runes sorted set."""
        puuid = "test-puuid-rune-0"
        await _add_ranked_participant(
            r,
            "NA1_R0",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            perk_keystone="0",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        runes_key = "champion:runes:Annie:14.5:MID"
        assert await r.zcard(runes_key) == 0

    @pytest.mark.asyncio
    async def test_champion_stats__rune_accumulates(self, r, cfg, log):
        """Same keystone across two matches: score = 2."""
        puuid = "test-puuid-rune-2"
        await _add_ranked_participant(
            r,
            "NA1_R1",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            perk_keystone="8229",
        )
        await _add_ranked_participant(
            r,
            "NA1_R2",
            puuid,
            2000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            perk_keystone="8229",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        runes_key = "champion:runes:Annie:14.5:MID"
        score = await r.zscore(runes_key, "8229")
        assert score == 2.0

    @pytest.mark.asyncio
    async def test_champion_stats__runes_key_has_ttl(self, r, cfg, log):
        """champion:runes key has 90-day TTL."""
        puuid = "test-puuid-rune-ttl"
        await _add_ranked_participant(
            r,
            "NA1_RT",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            perk_keystone="8229",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        _90_days = 90 * 24 * 3600
        ttl = await r.ttl("champion:runes:Annie:14.5:MID")
        assert 0 < ttl <= _90_days


# ---------------------------------------------------------------------------
# Matchup Aggregation
# ---------------------------------------------------------------------------


class TestChampionMatchups:
    """matchup:{champion_a}:{champion_b}:{role}:{patch} tracks head-to-head stats."""

    @pytest.mark.asyncio
    async def test_champion_stats__matchup_written(self, r, cfg, log):
        """When opponent data is available, matchup hash is updated."""
        puuid = "test-puuid-mu-1"
        # Set up a match with Annie MID vs Zed MID
        await _add_ranked_participant(
            r,
            "NA1_MU1",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            kills=8,
            deaths=3,
            assists=5,
            win=True,
        )
        # Store opponent data for the match
        await r.hset(
            f"opponent:{puuid}:NA1_MU1",
            mapping={"champion_name": "Zed", "team_position": "MID"},
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        # Matchup key uses alphabetically sorted champion pair
        matchup_key = "matchup:Annie:Zed:MID:14.5"
        matchup = await r.hgetall(matchup_key)
        assert matchup.get("games") == "1"
        # Annie won, so Annie side gets a win
        assert matchup.get("Annie_wins") == "1"

    @pytest.mark.asyncio
    async def test_champion_stats__matchup_accumulates(self, r, cfg, log):
        """Two matches in same matchup: games and wins accumulate."""
        puuid = "test-puuid-mu-2"
        for i, (win, score) in enumerate([(True, 1000), (False, 2000)]):
            await _add_ranked_participant(
                r,
                f"NA1_MU{i}",
                puuid,
                score,
                champion="Annie",
                team_position="MID",
                patch="14.5",
                kills=5,
                deaths=2,
                assists=3,
                win=win,
            )
            await r.hset(
                f"opponent:{puuid}:NA1_MU{i}",
                mapping={"champion_name": "Zed", "team_position": "MID"},
            )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        matchup_key = "matchup:Annie:Zed:MID:14.5"
        matchup = await r.hgetall(matchup_key)
        assert matchup.get("games") == "2"
        assert matchup.get("Annie_wins") == "1"

    @pytest.mark.asyncio
    async def test_champion_stats__matchup_no_opponent_data__skips(self, r, cfg, log):
        """No opponent data for match: matchup not written."""
        puuid = "test-puuid-mu-skip"
        await _add_ranked_participant(
            r,
            "NA1_SKIP",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
        )
        # No opponent:{puuid}:match_id key
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        # No matchup keys should exist
        matchup_keys = await r.keys("matchup:*")
        assert len(matchup_keys) == 0


# ---------------------------------------------------------------------------
# champion:stats:{champion}:{patch}:{role} Hash Fields
# ---------------------------------------------------------------------------


class TestChampionStatsHash:
    """champion:stats hash contains all required aggregate fields."""

    @pytest.mark.asyncio
    async def test_champion_stats__hash_has_all_required_fields(self, r, cfg, log):
        """Hash contains games, wins, kills, deaths, assists, gold, cs,
        damage, vision, double/triple/quadra/penta_kills."""
        puuid = "test-puuid-ch-hash"
        await _add_ranked_participant(
            r,
            "NA1_H1",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            kills=8,
            deaths=3,
            assists=5,
            win=True,
            gold_earned=14000,
            total_minions_killed=200,
            total_damage_dealt_to_champions=30000,
            vision_score=25,
            double_kills=2,
            triple_kills=1,
            quadra_kills=0,
            penta_kills=0,
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        stats = await r.hgetall("champion:stats:Annie:14.5:MID")
        required_fields = {
            "games",
            "wins",
            "kills",
            "deaths",
            "assists",
            "gold",
            "cs",
            "damage",
            "vision",
            "double_kills",
            "triple_kills",
            "quadra_kills",
            "penta_kills",
        }
        assert required_fields.issubset(set(stats.keys())), (
            f"Missing fields: {required_fields - set(stats.keys())}"
        )
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
    async def test_champion_stats__ttl_set_on_stats_key(self, r, cfg, log):
        """champion:stats key has 90-day TTL."""
        puuid = "test-puuid-ch-ttl"
        await _add_ranked_participant(
            r,
            "NA1_T1",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        _90_days = 90 * 24 * 3600
        stats_ttl = await r.ttl("champion:stats:Annie:14.5:MID")
        assert 0 < stats_ttl <= _90_days

    @pytest.mark.asyncio
    async def test_champion_stats__index_incremented(self, r, cfg, log):
        """champion:index:{patch} sorted set tracks champion:position combinations."""
        puuid = "test-puuid-ch-idx"
        await _add_ranked_participant(
            r,
            "NA1_I1",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
        )
        await _add_ranked_participant(
            r,
            "NA1_I2",
            puuid,
            2000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
        )
        await _add_ranked_participant(
            r,
            "NA1_I3",
            puuid,
            3000,
            champion="Jinx",
            team_position="BOTTOM",
            patch="14.5",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        assert await r.zscore("champion:index:14.5", "Annie:MID") == 2.0
        assert await r.zscore("champion:index:14.5", "Jinx:BOTTOM") == 1.0

    @pytest.mark.asyncio
    async def test_champion_stats__patch_list_recorded(self, r, cfg, log):
        """patch:list ZADD NX records patch with game_start as score."""
        puuid = "test-puuid-ch-pl"
        await _add_ranked_participant(
            r,
            "NA1_PL",
            puuid,
            5000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        score = await r.zscore("patch:list", "14.5")
        assert score == 5000.0

    @pytest.mark.asyncio
    async def test_champion_stats__patch_list_nx_no_overwrite(self, r, cfg, log):
        """patch:list ZADD NX keeps first score, does not overwrite."""
        puuid = "test-puuid-ch-pnx"
        await r.zadd("patch:list", {"14.5": 1000.0})
        await _add_ranked_participant(
            r,
            "NA1_PNX",
            puuid,
            9000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        score = await r.zscore("patch:list", "14.5")
        assert score == 1000.0


# ---------------------------------------------------------------------------
# system:halted Check
# ---------------------------------------------------------------------------


class TestChampionStatsSystemHalted:
    """system:halted flag stops processing; message stays in PEL."""

    @pytest.mark.asyncio
    async def test_champion_stats__system_halted__skips_processing(self, r, cfg, log):
        """system:halted set: no champion:stats written."""
        puuid = "test-puuid-ch-halt"
        await r.set("system:halted", "1")
        await _add_ranked_participant(
            r,
            "NA1_H",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        assert not await r.exists("champion:stats:Annie:14.5:MID")

    @pytest.mark.asyncio
    async def test_champion_stats__system_halted__preserves_pel(self, r, cfg, log):
        """system:halted: message NOT ACKed — stays in PEL."""
        await r.set("system:halted", "1")
        env = _champion_envelope()
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        pending = await r.xpending(_IN_STREAM, _GROUP)
        assert pending["pending"] == 1


# ---------------------------------------------------------------------------
# Proper Ack After Processing
# ---------------------------------------------------------------------------


class TestChampionStatsAck:
    """Message is ACKed after successful champion stats processing."""

    @pytest.mark.asyncio
    async def test_champion_stats__successful_processing__acks(self, r, cfg, log):
        """After processing, message is removed from PEL."""
        puuid = "test-puuid-ch-ack"
        await _add_ranked_participant(
            r,
            "NA1_ACK",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        pending = await r.xpending(_IN_STREAM, _GROUP)
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_champion_stats__no_ranked_matches__still_acks(self, r, cfg, log):
        """Non-ranked matches only (nothing to aggregate): still ACKed."""
        puuid = "test-puuid-ch-ack-nr"
        await _add_ranked_participant(
            r,
            "NA1_NR",
            puuid,
            1000,
            champion="Annie",
            team_position="MID",
            patch="14.5",
            queue_id="450",  # ARAM
        )
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        pending = await r.xpending(_IN_STREAM, _GROUP)
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_champion_stats__empty_match_history__acks(self, r, cfg, log):
        """No matches at all: message still ACKed."""
        puuid = "test-puuid-ch-ack-empty"
        env = _champion_envelope(puuid)
        msg_id = await _setup_message(r, env)

        await handle_champion_stats(r, cfg, "worker-1", msg_id, env, log)

        pending = await r.xpending(_IN_STREAM, _GROUP)
        assert pending["pending"] == 0
