"""Tests for LCU collector main logic — collect_once and deduplication."""

import json

import pytest
from unittest.mock import MagicMock, patch

from lol_lcu.__main__ import main as lcu_main
from lol_lcu.lcu_client import LcuAuthError
from lol_lcu.main import (
    _build_participants,
    _extract_player_stats,
    _show_summary,
    collect_once,
    load_existing_game_ids,
    run,
)


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
            json.dumps(
                {
                    "game_id": 100,
                    "game_creation": 0,
                    "game_duration": 0,
                    "queue_id": 0,
                    "game_mode": "URF",
                    "champion_id": 1,
                    "win": True,
                    "kills": 0,
                    "deaths": 0,
                    "assists": 0,
                    "gold_earned": 0,
                    "damage_to_champions": 0,
                    "puuid": "p",
                    "riot_id": "a#1",
                }
            ),
            json.dumps(
                {
                    "game_id": 200,
                    "game_creation": 0,
                    "game_duration": 0,
                    "queue_id": 0,
                    "game_mode": "ARAM",
                    "champion_id": 2,
                    "win": False,
                    "kills": 0,
                    "deaths": 0,
                    "assists": 0,
                    "gold_earned": 0,
                    "damage_to_champions": 0,
                    "puuid": "p",
                    "riot_id": "a#1",
                }
            ),
        ]
        f.write_text("\n".join(lines) + "\n")
        ids = load_existing_game_ids(f)
        assert ids == {100, 200}


class TestCollectOnce:
    """Tests for the collect_once function."""

    @patch("lol_lcu.main.LcuClient")
    def test_appends_new_matches(self, mock_cls, tmp_path):
        data_dir = tmp_path / "lcu-data"
        data_dir.mkdir()

        client = mock_cls.return_value
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
                            "item0": 3071,
                            "item1": 3153,
                            "item2": 3006,
                            "item3": 3036,
                            "item4": 0,
                            "item5": 0,
                            "item6": 3340,
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
    def test_deduplicates(self, mock_cls, tmp_path):
        data_dir = tmp_path / "lcu-data"
        data_dir.mkdir()

        # Pre-existing match
        existing = json.dumps(
            {
                "game_id": 100,
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
                "puuid": "test-puuid",
                "riot_id": "Faker#KR1",
                "items": [],
                "participants": [],
            }
        )
        jsonl_file = data_dir / "test-puuid.jsonl"
        jsonl_file.write_text(existing + "\n")

        client = mock_cls.return_value
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
                            "win": True,
                            "kills": 15,
                            "deaths": 3,
                            "assists": 7,
                            "goldEarned": 14000,
                            "totalDamageDealtToChampions": 45000,
                            "item0": 0,
                            "item1": 0,
                            "item2": 0,
                            "item3": 0,
                            "item4": 0,
                            "item5": 0,
                            "item6": 0,
                        },
                    }
                ],
            }
        ]

        count = collect_once(client, str(data_dir))
        assert count == 0

        lines = jsonl_file.read_text().strip().split("\n")
        assert len(lines) == 1  # no duplicate appended


class TestRunAuthRetry:
    """Tests for retrying on stale lockfile credentials (403)."""

    @patch("lol_lcu.main.time.sleep")
    @patch("lol_lcu.main.collect_once")
    @patch("lol_lcu.main.LcuClient")
    def test_run_retries_on_auth_error(self, mock_cls, mock_collect, mock_sleep, tmp_path):
        """On LcuAuthError, run() should retry with a fresh lockfile read."""
        data_dir = str(tmp_path / "lcu-data")

        # run() creates one client for the initial check, then _collect_with_auth_retry
        # creates one per attempt (2 attempts: fail + succeed = 3 total)
        mock_cls.return_value = MagicMock()

        # First collect raises LcuAuthError, second succeeds
        mock_collect.side_effect = [LcuAuthError("stale"), 5]

        with patch.dict("os.environ", {"LEAGUE_INSTALL_PATH": "/fake"}):
            run(data_dir=data_dir, poll_interval_minutes=0)

        # 1 initial check + 2 retries
        assert mock_cls.call_count == 3
        assert mock_collect.call_count == 2
        mock_sleep.assert_called_once_with(2)

    @patch("lol_lcu.main.time.sleep")
    @patch("lol_lcu.main.collect_once")
    @patch("lol_lcu.main.LcuClient")
    def test_run_gives_up_after_max_retries(self, mock_cls, mock_collect, mock_sleep, tmp_path):
        """After max retries on LcuAuthError, run() should give up gracefully."""
        data_dir = str(tmp_path / "lcu-data")

        mock_cls.return_value = MagicMock()
        mock_collect.side_effect = LcuAuthError("stale")

        with patch.dict("os.environ", {"LEAGUE_INSTALL_PATH": "/fake"}):
            run(data_dir=data_dir, poll_interval_minutes=0)

        # 1 initial check + 3 retries
        assert mock_cls.call_count == 4
        assert mock_collect.call_count == 3


class TestExtractPlayerStats:
    """Tests for _extract_player_stats helper."""

    def test_happy_path(self):
        game = {
            "participants": [
                {
                    "puuid": "abc",
                    "championId": 91,
                    "stats": {
                        "win": True,
                        "kills": 10,
                        "deaths": 2,
                        "assists": 5,
                        "goldEarned": 12000,
                        "totalDamageDealtToChampions": 30000,
                        "item0": 3071,
                        "item1": 3153,
                        "item2": 0,
                        "item3": 0,
                        "item4": 0,
                        "item5": 0,
                        "item6": 3340,
                    },
                }
            ],
        }
        result = _extract_player_stats(game, "abc")
        assert result is not None
        assert result["champion_id"] == 91
        assert result["win"] is True
        assert result["kills"] == 10
        assert result["items"] == [3071, 3153, 0, 0, 0, 0, 3340]

    def test_player_not_found(self):
        game = {"participants": [{"puuid": "other", "championId": 1, "stats": {}}]}
        assert _extract_player_stats(game, "abc") is None

    def test_empty_participants(self):
        assert _extract_player_stats({"participants": []}, "abc") is None
        assert _extract_player_stats({}, "abc") is None

    def test_missing_stats_dict(self):
        """Participant without stats key should use defaults."""
        game = {"participants": [{"puuid": "abc", "championId": 50}]}
        result = _extract_player_stats(game, "abc")
        assert result is not None
        assert result["kills"] == 0
        assert result["win"] is False


class TestBuildParticipants:
    """Tests for _build_participants helper."""

    def test_happy_path(self):
        game = {
            "participants": [
                {"puuid": "a", "championId": 1},
                {"puuid": "b", "championId": 2},
            ]
        }
        result = _build_participants(game)
        assert len(result) == 2
        assert result[0] == {"puuid": "a", "championId": 1}

    def test_empty_participants(self):
        assert _build_participants({"participants": []}) == []
        assert _build_participants({}) == []

    def test_missing_fields_use_defaults(self):
        game = {"participants": [{}]}
        result = _build_participants(game)
        assert result[0] == {"puuid": "", "championId": 0}


class TestShowSummary:
    """Tests for _show_summary."""

    def test_no_directory(self, tmp_path):
        """No data directory logs info, doesn't crash."""
        _show_summary(str(tmp_path / "nonexistent"))

    def test_empty_directory(self, tmp_path):
        """Empty directory doesn't crash."""
        _show_summary(str(tmp_path))

    def test_multiple_jsonl_files(self, tmp_path):
        """Should process each JSONL file."""
        import json as _json

        for puuid in ["aaa", "bbb"]:
            f = tmp_path / f"{puuid}.jsonl"
            f.write_text(_json.dumps({"game_id": 1}) + "\n")
        _show_summary(str(tmp_path))  # should not raise


class TestCollectOnceEdgeCases:
    """Edge case tests for collect_once."""

    @patch("lol_lcu.main.LcuClient")
    def test_exactly_page_size_fetches_next_page(self, mock_cls, tmp_path):
        """When result count equals page_size, should fetch next page."""
        data_dir = tmp_path / "lcu-data"
        data_dir.mkdir()
        client = mock_cls.return_value
        client.current_summoner.return_value = {
            "puuid": "test",
            "gameName": "Test",
            "tagLine": "NA1",
        }
        # First page: exactly 20 games, second page: empty
        page1 = [
            {
                "gameId": i,
                "gameCreation": 0,
                "gameDuration": 0,
                "queueId": 0,
                "gameMode": "SR",
                "participants": [
                    {
                        "puuid": "test",
                        "championId": 1,
                        "stats": {
                            "win": True,
                            "kills": 0,
                            "deaths": 0,
                            "assists": 0,
                            "goldEarned": 0,
                            "totalDamageDealtToChampions": 0,
                            "item0": 0,
                            "item1": 0,
                            "item2": 0,
                            "item3": 0,
                            "item4": 0,
                            "item5": 0,
                            "item6": 0,
                        },
                    }
                ],
            }
            for i in range(20)
        ]
        client.match_history.side_effect = [page1, []]
        count = collect_once(client, str(data_dir))
        assert count == 20
        assert client.match_history.call_count == 2

    @patch("lol_lcu.main.LcuClient")
    def test_less_than_page_size_stops(self, mock_cls, tmp_path):
        """When result count < page_size, should NOT fetch next page."""
        data_dir = tmp_path / "lcu-data"
        data_dir.mkdir()
        client = mock_cls.return_value
        client.current_summoner.return_value = {
            "puuid": "test",
            "gameName": "Test",
            "tagLine": "NA1",
        }
        page = [
            {
                "gameId": i,
                "gameCreation": 0,
                "gameDuration": 0,
                "queueId": 0,
                "gameMode": "SR",
                "participants": [
                    {
                        "puuid": "test",
                        "championId": 1,
                        "stats": {
                            "win": True,
                            "kills": 0,
                            "deaths": 0,
                            "assists": 0,
                            "goldEarned": 0,
                            "totalDamageDealtToChampions": 0,
                            "item0": 0,
                            "item1": 0,
                            "item2": 0,
                            "item3": 0,
                            "item4": 0,
                            "item5": 0,
                            "item6": 0,
                        },
                    }
                ],
            }
            for i in range(5)
        ]
        client.match_history.return_value = page
        count = collect_once(client, str(data_dir))
        assert count == 5
        assert client.match_history.call_count == 1

    @patch("lol_lcu.main.LcuClient")
    def test_empty_first_page(self, mock_cls, tmp_path):
        data_dir = tmp_path / "lcu-data"
        data_dir.mkdir()
        client = mock_cls.return_value
        client.current_summoner.return_value = {
            "puuid": "test",
            "gameName": "Test",
            "tagLine": "NA1",
        }
        client.match_history.return_value = []
        assert collect_once(client, str(data_dir)) == 0

    @patch("lol_lcu.main.LcuClient")
    def test_all_games_known_stops_early(self, mock_cls, tmp_path):
        """When all games on a page are already known, stop paginating."""
        data_dir = tmp_path / "lcu-data"
        data_dir.mkdir()
        # Pre-populate with game IDs 0-19
        jsonl_file = data_dir / "test.jsonl"
        lines = [
            json.dumps(
                {
                    "game_id": i,
                    "game_creation": 0,
                    "game_duration": 0,
                    "queue_id": 0,
                    "game_mode": "SR",
                    "champion_id": 1,
                    "win": True,
                    "kills": 0,
                    "deaths": 0,
                    "assists": 0,
                    "gold_earned": 0,
                    "damage_to_champions": 0,
                    "puuid": "test",
                    "riot_id": "Test#NA1",
                }
            )
            for i in range(20)
        ]
        jsonl_file.write_text("\n".join(lines) + "\n")

        client = mock_cls.return_value
        client.current_summoner.return_value = {
            "puuid": "test",
            "gameName": "Test",
            "tagLine": "NA1",
        }
        # Return same 20 games
        page = [
            {
                "gameId": i,
                "gameCreation": 0,
                "gameDuration": 0,
                "queueId": 0,
                "gameMode": "SR",
                "participants": [
                    {
                        "puuid": "test",
                        "championId": 1,
                        "stats": {
                            "win": True,
                            "kills": 0,
                            "deaths": 0,
                            "assists": 0,
                            "goldEarned": 0,
                            "totalDamageDealtToChampions": 0,
                            "item0": 0,
                            "item1": 0,
                            "item2": 0,
                            "item3": 0,
                            "item4": 0,
                            "item5": 0,
                            "item6": 0,
                        },
                    }
                ],
            }
            for i in range(20)
        ]
        client.match_history.return_value = page
        count = collect_once(client, str(data_dir))
        assert count == 0
        # Should only fetch one page since all known
        assert client.match_history.call_count == 1

    @patch("lol_lcu.main.LcuClient")
    def test_player_not_in_participants_skips(self, mock_cls, tmp_path):
        """Games where player is not in participants are skipped."""
        data_dir = tmp_path / "lcu-data"
        data_dir.mkdir()
        client = mock_cls.return_value
        client.current_summoner.return_value = {
            "puuid": "test",
            "gameName": "Test",
            "tagLine": "NA1",
        }
        client.match_history.return_value = [
            {
                "gameId": 100,
                "gameCreation": 0,
                "gameDuration": 0,
                "queueId": 0,
                "gameMode": "SR",
                "participants": [{"puuid": "someone_else", "championId": 1, "stats": {}}],
            }
        ]
        count = collect_once(client, str(data_dir))
        assert count == 0


class TestExtractPlayerStatsEdgeCases:
    """Additional edge case tests for _extract_player_stats."""

    def test_partial_stats_fills_defaults(self):
        """Participant with some stat keys missing should fill defaults for others."""
        game = {
            "participants": [
                {
                    "puuid": "abc",
                    "championId": 91,
                    "stats": {
                        "kills": 10,
                        # deaths, assists, goldEarned, items etc. all missing
                    },
                }
            ],
        }
        result = _extract_player_stats(game, "abc")
        assert result is not None
        assert result["kills"] == 10
        assert result["deaths"] == 0  # default
        assert result["assists"] == 0  # default
        assert result["win"] is False  # default
        assert result["gold_earned"] == 0  # default
        assert result["damage_to_champions"] == 0  # default
        assert result["items"] == [0, 0, 0, 0, 0, 0, 0]  # all default


class TestCollectOnceFileWriteFailure:
    """Test collect_once behavior on file write failure."""

    @patch("lol_lcu.main.LcuClient")
    def test_file_write_failure_raises(self, mock_cls, tmp_path):
        """Disk full / permission denied during write should raise."""
        data_dir = tmp_path / "lcu-data"
        data_dir.mkdir()
        client = mock_cls.return_value
        client.current_summoner.return_value = {
            "puuid": "test",
            "gameName": "Test",
            "tagLine": "NA1",
        }
        client.match_history.return_value = [
            {
                "gameId": 100,
                "gameCreation": 0,
                "gameDuration": 0,
                "queueId": 0,
                "gameMode": "SR",
                "participants": [
                    {
                        "puuid": "test",
                        "championId": 1,
                        "stats": {"win": True, "kills": 0, "deaths": 0, "assists": 0},
                    }
                ],
            }
        ]
        # Make the JSONL file path a directory so open() fails
        jsonl_file = data_dir / "test.jsonl"
        jsonl_file.mkdir()

        with pytest.raises((IsADirectoryError, PermissionError, OSError)):
            collect_once(client, str(data_dir))


class TestLcuMainEntryPoint:
    """Tests for __main__.main() CLI arg parsing."""

    def test_defaults_data_dir_from_env(self, monkeypatch):
        """LCU_DATA_DIR env → --data-dir default."""
        monkeypatch.setenv("LCU_DATA_DIR", "/custom/lcu-data")
        monkeypatch.delenv("LCU_POLL_INTERVAL_MINUTES", raising=False)
        with patch("lol_lcu.__main__.run") as mock_run, patch("sys.argv", ["lcu"]):
            lcu_main()
        mock_run.assert_called_once_with(data_dir="/custom/lcu-data", poll_interval_minutes=0)

    def test_defaults_data_dir_fallback(self, monkeypatch):
        """No LCU_DATA_DIR env → falls back to 'lcu-data'."""
        monkeypatch.delenv("LCU_DATA_DIR", raising=False)
        monkeypatch.delenv("LCU_POLL_INTERVAL_MINUTES", raising=False)
        with patch("lol_lcu.__main__.run") as mock_run, patch("sys.argv", ["lcu"]):
            lcu_main()
        mock_run.assert_called_once_with(data_dir="lcu-data", poll_interval_minutes=0)

    def test_poll_interval_from_env(self, monkeypatch):
        """LCU_POLL_INTERVAL_MINUTES env → --poll-interval default."""
        monkeypatch.delenv("LCU_DATA_DIR", raising=False)
        monkeypatch.setenv("LCU_POLL_INTERVAL_MINUTES", "10")
        with patch("lol_lcu.__main__.run") as mock_run, patch("sys.argv", ["lcu"]):
            lcu_main()
        mock_run.assert_called_once_with(data_dir="lcu-data", poll_interval_minutes=10)

    def test_poll_interval_default_zero(self, monkeypatch):
        """No LCU_POLL_INTERVAL_MINUTES env → defaults to 0."""
        monkeypatch.delenv("LCU_DATA_DIR", raising=False)
        monkeypatch.delenv("LCU_POLL_INTERVAL_MINUTES", raising=False)
        with patch("lol_lcu.__main__.run") as mock_run, patch("sys.argv", ["lcu"]):
            lcu_main()
        assert mock_run.call_args[1]["poll_interval_minutes"] == 0

    def test_explicit_args_override_env(self, monkeypatch):
        """Explicit --data-dir and --poll-interval override env defaults."""
        monkeypatch.setenv("LCU_DATA_DIR", "/env-dir")
        monkeypatch.setenv("LCU_POLL_INTERVAL_MINUTES", "10")
        with (
            patch("lol_lcu.__main__.run") as mock_run,
            patch("sys.argv", ["lcu", "--data-dir", "/cli-dir", "--poll-interval", "5"]),
        ):
            lcu_main()
        mock_run.assert_called_once_with(data_dir="/cli-dir", poll_interval_minutes=5)
