"""Unit tests for compute_opgg_fast_stats — op.gg fast-path ETL."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.opgg_fast_stats import compute_opgg_fast_stats


def _make_game(
    game_id=1,
    puuid="test-puuid",
    team_key="BLUE",
    team_win=True,
    kills=5,
    deaths=2,
    assists=3,
    champion_id=1,
    position="MID",
    cs=120,
    game_length_second=1800,
):
    """Build a minimal raw op.gg game dict matching prefetch_player_games output."""
    return {
        "id": game_id,
        "created_at": "2026-03-20T12:00:00+00:00",
        "game_length_second": game_length_second,
        "queue_id": 420,
        "game_type": "Ranked",
        "participants": [
            {
                "team_key": team_key,
                "summoner": {"summoner_id": "sid-1", "puuid": puuid},
                "champion_id": champion_id,
                "position": position,
                "stats": {
                    "kill": kills,
                    "death": deaths,
                    "assist": assists,
                    "cs": cs,
                    "damage_dealt_to_champions": 15000,
                },
                "items": [3031, 0, 0, 0, 0, 0, 3363],
            },
            {
                "team_key": "RED" if team_key == "BLUE" else "BLUE",
                "summoner": {"summoner_id": "opp-1", "puuid": "opponent-puuid"},
                "champion_id": 92,
                "position": "TOP",
                "stats": {
                    "kill": 3,
                    "death": 4,
                    "assist": 1,
                    "cs": 100,
                    "damage_dealt_to_champions": 10000,
                },
                "items": [3071, 0, 0, 0, 0, 0, 3340],
            },
        ],
        "teams": [
            {
                "key": "BLUE",
                "game_stat": {"is_win": team_win, "kill": 10, "death": 5, "assist": 20},
            },
            {
                "key": "RED",
                "game_stat": {
                    "is_win": not team_win,
                    "kill": 5,
                    "death": 10,
                    "assist": 8,
                },
            },
        ],
    }


PUUID = "test-puuid"
CHAMP_MAP = {"1": "Annie", "92": "Riven", "157": "Yasuo"}


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestComputeFastStatsBasic:
    @pytest.mark.asyncio
    async def test_compute_fast_stats__basic__writes_correct_totals(self, r):
        """3 games: verify all totals and derived fields."""
        games = [
            _make_game(game_id=1, kills=5, deaths=2, assists=3, champion_id=1,
                       position="MID", cs=120, game_length_second=1800, team_win=True),
            _make_game(game_id=2, kills=3, deaths=4, assists=7, champion_id=92,
                       position="TOP", cs=150, game_length_second=2100, team_win=False),
            _make_game(game_id=3, kills=8, deaths=1, assists=2, champion_id=1,
                       position="MID", cs=200, game_length_second=2400, team_win=True),
        ]
        result = await compute_opgg_fast_stats(r, PUUID, games, CHAMP_MAP)

        assert result == 3
        stats = await r.hgetall(f"player:stats:{PUUID}")
        assert stats["total_games"] == "3"
        assert stats["total_wins"] == "2"
        assert stats["total_kills"] == "16"
        assert stats["total_deaths"] == "7"
        assert stats["total_assists"] == "12"
        # win_rate = 2/3 = 0.6667
        assert stats["win_rate"] == "0.6667"
        # avg_kills = 16/3 = 5.3333
        assert stats["avg_kills"] == "5.3333"
        # kda = (16+12)/max(7,1) = 4.0000
        assert stats["kda"] == "4.0000"

    @pytest.mark.asyncio
    async def test_compute_fast_stats__no_deaths__kda_clamps_denominator(self, r):
        """0 deaths: kda = (kills + assists) / 1."""
        games = [
            _make_game(game_id=1, kills=10, deaths=0, assists=5),
        ]
        result = await compute_opgg_fast_stats(r, PUUID, games, CHAMP_MAP)

        assert result == 1
        stats = await r.hgetall(f"player:stats:{PUUID}")
        # kda = (10+5)/max(0,1) = 15.0000
        assert stats["kda"] == "15.0000"

    @pytest.mark.asyncio
    async def test_compute_fast_stats__already_exists__returns_zero(self, r):
        """Pre-populated stats hash: return 0 and no overwrite."""
        await r.hset(f"player:stats:{PUUID}", mapping={"total_games": "99", "source": "pipeline"})
        games = [_make_game(game_id=1, kills=5, deaths=2, assists=3)]

        result = await compute_opgg_fast_stats(r, PUUID, games, CHAMP_MAP)

        assert result == 0
        stats = await r.hgetall(f"player:stats:{PUUID}")
        assert stats["total_games"] == "99"

    @pytest.mark.asyncio
    async def test_compute_fast_stats__unknown_champion_id__falls_back_to_id_string(self, r):
        """Champion ID not in map: uses str(champion_id) as name."""
        games = [
            _make_game(game_id=1, champion_id=9999),
        ]
        result = await compute_opgg_fast_stats(r, PUUID, games, CHAMP_MAP)

        assert result == 1
        score = await r.zscore(f"player:champions:{PUUID}", "9999")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_compute_fast_stats__writes_source_marker(self, r):
        """source=opgg_prefetch is written to the stats hash."""
        games = [_make_game(game_id=1)]
        await compute_opgg_fast_stats(r, PUUID, games, CHAMP_MAP)

        assert await r.hget(f"player:stats:{PUUID}", "source") == "opgg_prefetch"

    @pytest.mark.asyncio
    async def test_compute_fast_stats__sets_ttl(self, r):
        """All 3 keys get EXPIRE set."""
        games = [_make_game(game_id=1)]
        await compute_opgg_fast_stats(r, PUUID, games, CHAMP_MAP, ttl_seconds=3600)

        for key in (
            f"player:stats:{PUUID}",
            f"player:champions:{PUUID}",
            f"player:roles:{PUUID}",
        ):
            ttl = await r.ttl(key)
            assert 0 < ttl <= 3600, f"{key} TTL={ttl}"

    @pytest.mark.asyncio
    async def test_compute_fast_stats__avg_cs_per_min_computed(self, r):
        """cs/min formula: total_cs / total_duration_min."""
        games = [
            _make_game(game_id=1, cs=180, game_length_second=1800),  # 10 cs/min
            _make_game(game_id=2, cs=120, game_length_second=1200),  # 6 cs/min
        ]
        await compute_opgg_fast_stats(r, PUUID, games, CHAMP_MAP)

        stats = await r.hgetall(f"player:stats:{PUUID}")
        # total_cs = 300, total_duration = (1800+1200)/60 = 50 min
        # avg_cs_per_min = 300/50 = 6.0000
        assert stats["avg_cs_per_min"] == "6.0000"


class TestComputeFastStatsEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_raw_games(self, r):
        """Empty raw_games list: return 0 and no Redis writes."""
        result = await compute_opgg_fast_stats(r, PUUID, [], CHAMP_MAP)

        assert result == 0
        assert await r.exists(f"player:stats:{PUUID}") == 0
        assert await r.exists(f"player:champions:{PUUID}") == 0
        assert await r.exists(f"player:roles:{PUUID}") == 0

    @pytest.mark.asyncio
    async def test_participant_not_in_game(self, r):
        """Games where some have the puuid and some don't — count only matched."""
        games = [
            _make_game(game_id=1, puuid=PUUID, kills=5, deaths=2, assists=3),
            _make_game(game_id=2, puuid="other-puuid", kills=10, deaths=0, assists=10),
            _make_game(game_id=3, puuid=PUUID, kills=3, deaths=1, assists=4),
        ]
        result = await compute_opgg_fast_stats(r, PUUID, games, CHAMP_MAP)

        assert result == 2
        stats = await r.hgetall(f"player:stats:{PUUID}")
        assert stats["total_games"] == "2"
        assert stats["total_kills"] == "8"
        assert stats["total_deaths"] == "3"
        assert stats["total_assists"] == "7"

    @pytest.mark.asyncio
    async def test_zero_game_length(self, r):
        """All games with game_length_second=0: avg_cs_per_min should be '0.0000'."""
        games = [
            _make_game(game_id=1, cs=100, game_length_second=0),
            _make_game(game_id=2, cs=200, game_length_second=0),
        ]
        result = await compute_opgg_fast_stats(r, PUUID, games, CHAMP_MAP)

        assert result == 2
        stats = await r.hgetall(f"player:stats:{PUUID}")
        assert stats["avg_cs_per_min"] == "0.0000"
