"""Unit tests for lol_parser._helpers and _constants (PRIN-PAR-01/02/03)."""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config

from lol_parser._constants import (
    _PARTICIPANT_FIELD_MAP,
    _TEAM_ID_BLUE,
    _TEAM_ID_MAP,
    _TEAM_ID_RED,
)
from lol_parser._helpers import (
    _key_match_participants,
    _key_player,
    _key_player_matches,
    _validate,
)
from lol_parser.main import _build_pid_mappings, _queue_player_matches_trim

# -------------------------------------------------------------------------
# _constants.py
# -------------------------------------------------------------------------


class TestParserConstants:
    """PRIN-PAR-02: Team IDs and field map are in _constants.py."""

    def test_team_id_blue__value(self):
        assert _TEAM_ID_BLUE == 100

    def test_team_id_red__value(self):
        assert _TEAM_ID_RED == 200

    def test_team_id_map__blue_label(self):
        assert _TEAM_ID_MAP[100] == "team_blue"

    def test_team_id_map__red_label(self):
        assert _TEAM_ID_MAP[200] == "team_red"

    def test_team_id_map__only_two_entries(self):
        assert len(_TEAM_ID_MAP) == 2

    def test_participant_field_map__is_nonempty(self):
        assert len(_PARTICIPANT_FIELD_MAP) > 0

    def test_participant_field_map__kills_mapping(self):
        riot_field, default = _PARTICIPANT_FIELD_MAP["kills"]
        assert riot_field == "kills"
        assert default == 0

    def test_participant_field_map__champion_id_mapping(self):
        riot_field, default = _PARTICIPANT_FIELD_MAP["champion_id"]
        assert riot_field == "championId"
        assert default == ""

    def test_participant_field_map__all_values_are_tuples(self):
        for redis_field, value in _PARTICIPANT_FIELD_MAP.items():
            assert isinstance(value, tuple), f"{redis_field} is not a tuple"
            assert len(value) == 2, f"{redis_field} tuple has {len(value)} elements"


# -------------------------------------------------------------------------
# _helpers.py key builders
# -------------------------------------------------------------------------


class TestParserKeyBuilders:
    """PRIN-PAR-01: Redis key patterns extracted to key-builder functions."""

    def test_key_player_matches__format(self):
        assert _key_player_matches("puuid-abc") == "player:matches:puuid-abc"

    def test_key_match_participants__format(self):
        assert _key_match_participants("NA1_123") == "match:participants:NA1_123"

    def test_key_player__format(self):
        assert _key_player("puuid-xyz") == "player:puuid-xyz"

    def test_key_player_matches__empty(self):
        assert _key_player_matches("") == "player:matches:"

    def test_key_match_participants__empty(self):
        assert _key_match_participants("") == "match:participants:"


# -------------------------------------------------------------------------
# _validate
# -------------------------------------------------------------------------


class TestValidate:
    """Parser _validate extracts info + metadata from match data."""

    def test_validate__valid_data__returns_metadata_and_info(self):
        data: dict[str, Any] = {
            "metadata": {"matchId": "NA1_123"},
            "info": {
                "participants": [{"puuid": "abc"}],
                "gameStartTimestamp": 1700000000000,
            },
        }
        metadata, info = _validate(data)
        assert metadata["matchId"] == "NA1_123"
        assert len(info["participants"]) == 1

    def test_validate__missing_info__raises_key_error(self):
        with pytest.raises(KeyError):
            _validate({"metadata": {}})

    def test_validate__empty_participants__raises_key_error(self):
        with pytest.raises(KeyError, match="participants"):
            _validate(
                {
                    "metadata": {},
                    "info": {"participants": [], "gameStartTimestamp": 1700000000000},
                }
            )

    def test_validate__missing_game_start__raises_key_error(self):
        with pytest.raises(KeyError, match="gameStartTimestamp"):
            _validate(
                {
                    "metadata": {},
                    "info": {"participants": [{"puuid": "abc"}]},
                }
            )


# -------------------------------------------------------------------------
# _build_pid_mappings (PRIN-PAR-03)
# -------------------------------------------------------------------------


class TestBuildPidMappings:
    """PRIN-PAR-03: _build_pid_mappings extracts participantId->puuid and ->champion maps."""

    def test_build_pid_mappings__single_participant(self):
        participants = [
            {"participantId": 1, "puuid": "abc-123", "championName": "Jinx"},
        ]
        pid_to_puuid, pid_to_champ = _build_pid_mappings(participants)
        assert pid_to_puuid[1] == "abc-123"
        assert pid_to_champ[1] == "Jinx"

    def test_build_pid_mappings__multiple_participants(self):
        participants = [
            {"participantId": 1, "puuid": "p1", "championName": "Jinx"},
            {"participantId": 2, "puuid": "p2", "championName": "Lux"},
        ]
        pid_to_puuid, pid_to_champ = _build_pid_mappings(participants)
        assert len(pid_to_puuid) == 2
        assert pid_to_puuid[2] == "p2"
        assert pid_to_champ[2] == "Lux"

    def test_build_pid_mappings__missing_fields__uses_defaults(self):
        participants = [{"participantId": 5}]
        pid_to_puuid, pid_to_champ = _build_pid_mappings(participants)
        assert pid_to_puuid[5] == ""
        assert pid_to_champ[5] == "Unknown"

    def test_build_pid_mappings__missing_participant_id__uses_zero(self):
        participants = [{"puuid": "p1", "championName": "Jinx"}]
        pid_to_puuid, _pid_to_champ = _build_pid_mappings(participants)
        assert pid_to_puuid[0] == "p1"

    def test_build_pid_mappings__empty_list__returns_empty_dicts(self):
        pid_to_puuid, pid_to_champ = _build_pid_mappings([])
        assert pid_to_puuid == {}
        assert pid_to_champ == {}


# -------------------------------------------------------------------------
# _queue_player_matches_trim (PRIN-PAR-03)
# -------------------------------------------------------------------------


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.flushall()
    await redis.aclose()


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    return Config(_env_file=None)  # type: ignore[call-arg]


class TestQueuePlayerMatchesTrim:
    """PRIN-PAR-03: _queue_player_matches_trim trims player:matches sorted sets."""

    async def test_queue_player_matches_trim__trims_excess_matches(self, r, cfg):
        """When a player has more matches than player_matches_max, oldest are trimmed."""
        puuid = "puuid-trim-test"
        pm_key = f"player:matches:{puuid}"
        # Add more matches than the max
        max_matches = cfg.player_matches_max
        for i in range(max_matches + 5):
            await r.zadd(pm_key, {f"match_{i}": float(i)})

        async with r.pipeline(transaction=False) as pipe:
            _queue_player_matches_trim(pipe, {puuid}, cfg)
            await pipe.execute()

        count = await r.zcard(pm_key)
        assert count == max_matches

    async def test_queue_player_matches_trim__sets_ttl(self, r, cfg):
        """Trim also applies TTL to the player:matches key."""
        puuid = "puuid-ttl-test"
        pm_key = f"player:matches:{puuid}"
        await r.zadd(pm_key, {"match_1": 1.0})

        async with r.pipeline(transaction=False) as pipe:
            _queue_player_matches_trim(pipe, {puuid}, cfg)
            await pipe.execute()

        ttl = await r.ttl(pm_key)
        assert ttl > 0

    async def test_queue_player_matches_trim__multiple_puuids(self, r, cfg):
        """Trim is applied to all puuids in the set."""
        puuids = {"puuid-a", "puuid-b"}
        for puuid in puuids:
            await r.zadd(f"player:matches:{puuid}", {"m1": 1.0})

        async with r.pipeline(transaction=False) as pipe:
            _queue_player_matches_trim(pipe, puuids, cfg)
            await pipe.execute()

        for puuid in puuids:
            ttl = await r.ttl(f"player:matches:{puuid}")
            assert ttl > 0
