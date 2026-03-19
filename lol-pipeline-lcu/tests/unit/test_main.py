"""Tests for LCU collector main logic — collect_once and deduplication."""

import json
from unittest.mock import MagicMock, patch

import pytest

from lol_lcu.__main__ import main as lcu_main
from lol_lcu.lcu_client import LcuAuthError
from lol_lcu.main import (
    _build_participants,
    _extract_player_stats,
    _identity_map,
    _show_summary,
    collect_once,
    load_existing_game_ids,
    run,
)

# ---------------------------------------------------------------------------
# Helpers — LCU v4 format uses participantIdentities + participants
# ---------------------------------------------------------------------------


def _make_game(
    game_id: int,
    participants: list[dict],
    *,
    game_creation: int = 0,
    game_duration: int = 0,
    queue_id: int = 0,
    game_mode: str = "SR",
) -> dict:
    """Build a LCU v4 game dict with participantIdentities auto-generated.

    Each entry in *participants* must have at least ``puuid``, ``championId``,
    and ``participantId``.  Stats go inside a ``stats`` sub-dict.
    """
    identities = []
    parts = []
    for p in participants:
        pid = p.get("participantId", 0)
        identities.append(
            {
                "participantId": pid,
                "player": {
                    "puuid": p.get("puuid", ""),
                    "gameName": p.get("gameName", ""),
                    "tagLine": p.get("tagLine", ""),
                },
            }
        )
        part = {
            "participantId": pid,
            "championId": p.get("championId", 0),
        }
        if "stats" in p:
            part["stats"] = p["stats"]
        parts.append(part)

    return {
        "gameId": game_id,
        "gameCreation": game_creation,
        "gameDuration": game_duration,
        "queueId": queue_id,
        "gameMode": game_mode,
        "participantIdentities": identities,
        "participants": parts,
    }


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


class TestIdentityMap:
    """Tests for _identity_map helper."""

    def test_builds_map_from_identities(self):
        game = {
            "participantIdentities": [
                {"participantId": 1, "player": {"puuid": "aaa", "gameName": "A"}},
                {"participantId": 2, "player": {"puuid": "bbb", "gameName": "B"}},
            ],
        }
        result = _identity_map(game)
        assert len(result) == 2
        assert result[1]["puuid"] == "aaa"
        assert result[2]["puuid"] == "bbb"

    def test_empty_identities(self):
        assert _identity_map({"participantIdentities": []}) == {}
        assert _identity_map({}) == {}

    def test_missing_player_key(self):
        game = {"participantIdentities": [{"participantId": 1}]}
        result = _identity_map(game)
        assert result[1] == {}

    def test_missing_participant_id_skipped(self):
        game = {"participantIdentities": [{"player": {"puuid": "x"}}]}
        result = _identity_map(game)
        # Entry without participantId is skipped (guard: pid is not None)
        assert result == {}


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
            _make_game(
                100,
                [
                    {
                        "puuid": "test-puuid",
                        "participantId": 1,
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
                game_creation=1700000000000,
                game_duration=1800,
                queue_id=900,
                game_mode="URF",
            )
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
            _make_game(
                100,  # duplicate
                [
                    {
                        "puuid": "test-puuid",
                        "participantId": 1,
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
                game_creation=1700000000000,
                game_duration=1800,
                queue_id=900,
                game_mode="URF",
            )
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
        game = _make_game(
            1,
            [
                {
                    "puuid": "abc",
                    "participantId": 7,
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
        )
        result = _extract_player_stats(game, "abc")
        assert result is not None
        assert result["champion_id"] == 91
        assert result["win"] is True
        assert result["kills"] == 10
        assert result["items"] == [3071, 3153, 0, 0, 0, 0, 3340]

    def test_player_not_found(self):
        game = _make_game(
            1,
            [{"puuid": "other", "participantId": 1, "championId": 1, "stats": {}}],
        )
        assert _extract_player_stats(game, "abc") is None

    def test_empty_participants(self):
        game = {"participants": [], "participantIdentities": []}
        assert _extract_player_stats(game, "abc") is None
        assert _extract_player_stats({}, "abc") is None

    def test_missing_stats_dict(self):
        """Participant without stats key should use defaults."""
        game = _make_game(
            1,
            [{"puuid": "abc", "participantId": 1, "championId": 50}],
        )
        result = _extract_player_stats(game, "abc")
        assert result is not None
        assert result["kills"] == 0
        assert result["win"] is False


class TestBuildParticipants:
    """Tests for _build_participants helper."""

    def test_happy_path(self):
        game = _make_game(
            1,
            [
                {"puuid": "a", "participantId": 1, "championId": 1},
                {"puuid": "b", "participantId": 2, "championId": 2},
            ],
        )
        result = _build_participants(game)
        assert len(result) == 2
        assert result[0] == {"puuid": "a", "championId": 1}

    def test_empty_participants(self):
        assert _build_participants({"participants": [], "participantIdentities": []}) == []
        assert _build_participants({}) == []

    def test_missing_fields_use_defaults(self):
        game = {"participants": [{"participantId": 1}], "participantIdentities": []}
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
            _make_game(
                i,
                [
                    {
                        "puuid": "test",
                        "participantId": 1,
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
            )
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
            _make_game(
                i,
                [
                    {
                        "puuid": "test",
                        "participantId": 1,
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
            )
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
        # Return same 20 games (v4 format)
        page = [
            _make_game(
                i,
                [
                    {
                        "puuid": "test",
                        "participantId": 1,
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
            )
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
            _make_game(
                100,
                [{"puuid": "someone_else", "participantId": 1, "championId": 1, "stats": {}}],
            )
        ]
        count = collect_once(client, str(data_dir))
        assert count == 0


class TestExtractPlayerStatsEdgeCases:
    """Additional edge case tests for _extract_player_stats."""

    def test_partial_stats_fills_defaults(self):
        """Participant with some stat keys missing should fill defaults for others."""
        game = _make_game(
            1,
            [
                {
                    "puuid": "abc",
                    "participantId": 1,
                    "championId": 91,
                    "stats": {
                        "kills": 10,
                        # deaths, assists, goldEarned, items etc. all missing
                    },
                }
            ],
        )
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
            _make_game(
                100,
                [
                    {
                        "puuid": "test",
                        "participantId": 1,
                        "championId": 1,
                        "stats": {"win": True, "kills": 0, "deaths": 0, "assists": 0},
                    }
                ],
            )
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


class TestTerminalOutputUsesPrint:
    """CQ-3: terminal-visible messages use print(), not JSON logger."""

    def test_show_summary__prints_to_stdout(self, tmp_path, capsys):
        """_show_summary outputs match counts to stdout via print()."""
        import json as _json

        for puuid in ["aaa", "bbb"]:
            f = tmp_path / f"{puuid}.jsonl"
            f.write_text(_json.dumps({"game_id": 1}) + "\n")
        _show_summary(str(tmp_path))
        captured = capsys.readouterr()
        assert "aaa: 1 matches" in captured.out
        assert "bbb: 1 matches" in captured.out
