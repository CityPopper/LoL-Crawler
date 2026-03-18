"""Tests for LCU collector main logic — collect_once and deduplication."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from lol_lcu.main import collect_once, load_existing_game_ids


class TestLoadExistingGameIds:
    """Tests for loading existing game IDs from JSONL."""

    def test_empty_file(self, tmp_path):
        f = tmp_path / "test.jsonl"
        f.write_text("")
        ids = load_existing_game_ids(f)
        assert ids == set()

    def test_file_not_exists(self, tmp_path):
        f = tmp_path / "missing.jsonl"
        ids = load_existing_game_ids(f)
        assert ids == set()

    def test_loads_game_ids(self, tmp_path):
        f = tmp_path / "test.jsonl"
        lines = [
            json.dumps({"game_id": 100, "game_creation": 0, "game_duration": 0,
                        "queue_id": 0, "game_mode": "URF", "champion_id": 1,
                        "win": True, "kills": 0, "deaths": 0, "assists": 0,
                        "gold_earned": 0, "damage_to_champions": 0,
                        "puuid": "p", "riot_id": "a#1"}),
            json.dumps({"game_id": 200, "game_creation": 0, "game_duration": 0,
                        "queue_id": 0, "game_mode": "ARAM", "champion_id": 2,
                        "win": False, "kills": 0, "deaths": 0, "assists": 0,
                        "gold_earned": 0, "damage_to_champions": 0,
                        "puuid": "p", "riot_id": "a#1"}),
        ]
        f.write_text("\n".join(lines) + "\n")
        ids = load_existing_game_ids(f)
        assert ids == {100, 200}


class TestCollectOnce:
    """Tests for the collect_once function."""

    @patch("lol_lcu.main.LcuClient")
    def test_appends_new_matches(self, MockClient, tmp_path):
        data_dir = tmp_path / "lcu-data"
        data_dir.mkdir()

        client = MockClient.return_value
        client.current_summoner.return_value = {
            "puuid": "test-puuid",
            "gameName": "Faker",
            "tagLine": "KR1",
        }
        client.match_history.return_value = [
            {
                "gameId": 100,
                "gameCreation": 1700000000000,
                "gameDuration": 1800,
                "queueId": 900,
                "gameMode": "URF",
                "participants": [
                    {
                        "puuid": "test-puuid",
                        "championId": 91,
                        "stats": {
                            "win": True,
                            "kills": 15,
                            "deaths": 3,
                            "assists": 7,
                            "goldEarned": 14000,
                            "totalDamageDealtToChampions": 45000,
                            "item0": 3071, "item1": 3153, "item2": 3006,
                            "item3": 3036, "item4": 0, "item5": 0, "item6": 3340,
                        },
                    }
                ],
            }
        ]

        count = collect_once(client, str(data_dir))
        assert count == 1

        jsonl_file = data_dir / "test-puuid.jsonl"
        assert jsonl_file.exists()
        lines = jsonl_file.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["game_id"] == 100
        assert data["game_mode"] == "URF"

    @patch("lol_lcu.main.LcuClient")
    def test_deduplicates(self, MockClient, tmp_path):
        data_dir = tmp_path / "lcu-data"
        data_dir.mkdir()

        # Pre-existing match
        existing = json.dumps({
            "game_id": 100, "game_creation": 1700000000000, "game_duration": 1800,
            "queue_id": 900, "game_mode": "URF", "champion_id": 91,
            "win": True, "kills": 15, "deaths": 3, "assists": 7,
            "gold_earned": 14000, "damage_to_champions": 45000,
            "puuid": "test-puuid", "riot_id": "Faker#KR1",
            "items": [], "participants": [],
        })
        jsonl_file = data_dir / "test-puuid.jsonl"
        jsonl_file.write_text(existing + "\n")

        client = MockClient.return_value
        client.current_summoner.return_value = {
            "puuid": "test-puuid",
            "gameName": "Faker",
            "tagLine": "KR1",
        }
        client.match_history.return_value = [
            {
                "gameId": 100,  # duplicate
                "gameCreation": 1700000000000,
                "gameDuration": 1800,
                "queueId": 900,
                "gameMode": "URF",
                "participants": [
                    {
                        "puuid": "test-puuid",
                        "championId": 91,
                        "stats": {
                            "win": True, "kills": 15, "deaths": 3, "assists": 7,
                            "goldEarned": 14000, "totalDamageDealtToChampions": 45000,
                            "item0": 0, "item1": 0, "item2": 0,
                            "item3": 0, "item4": 0, "item5": 0, "item6": 0,
                        },
                    }
                ],
            }
        ]

        count = collect_once(client, str(data_dir))
        assert count == 0

        lines = jsonl_file.read_text().strip().split("\n")
        assert len(lines) == 1  # no duplicate appended
