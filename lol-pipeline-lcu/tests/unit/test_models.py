"""Tests for LcuMatch model — JSONL serialization and deserialization."""

import json

from lol_lcu.models import LcuMatch


class TestLcuMatch:
    """Tests for LcuMatch dataclass."""

    def _sample_match(self) -> dict:
        return {
            "game_id": 123456789,
            "game_creation": 1700000000000,
            "game_duration": 1800,
            "queue_id": 900,
            "game_mode": "URF",
            "champion_id": 91,
            "win": True,
            "kills": 15,
            "deaths": 3,
            "assists": 7,
            "gold_earned": 14000,
            "damage_to_champions": 45000,
            "puuid": "test-puuid-abc123",
            "riot_id": "Faker#KR1",
            "items": [3071, 3153, 3006, 3036, 0, 0, 3340],
            "participants": [{"puuid": "p1", "championId": 91}],
        }

    def test_from_dict_creates_match(self):
        data = self._sample_match()
        match = LcuMatch(**data)
        assert match.game_id == 123456789
        assert match.game_mode == "URF"
        assert match.win is True
        assert match.kills == 15
        assert match.items == [3071, 3153, 3006, 3036, 0, 0, 3340]
        assert match.participants == [{"puuid": "p1", "championId": 91}]

    def test_to_json_line(self):
        data = self._sample_match()
        match = LcuMatch(**data)
        line = match.to_json_line()
        assert not line.endswith("\n")
        parsed = json.loads(line)
        assert parsed["game_id"] == 123456789
        assert parsed["game_mode"] == "URF"
        assert parsed["items"] == [3071, 3153, 3006, 3036, 0, 0, 3340]

    def test_from_json_line(self):
        data = self._sample_match()
        line = json.dumps(data)
        match = LcuMatch.from_json_line(line)
        assert match.game_id == 123456789
        assert match.puuid == "test-puuid-abc123"
        assert match.riot_id == "Faker#KR1"

    def test_roundtrip_serialization(self):
        data = self._sample_match()
        original = LcuMatch(**data)
        line = original.to_json_line()
        restored = LcuMatch.from_json_line(line)
        assert original == restored

    def test_default_items_and_participants(self):
        data = self._sample_match()
        del data["items"]
        del data["participants"]
        match = LcuMatch(**data)
        assert match.items == []
        assert match.participants == []
