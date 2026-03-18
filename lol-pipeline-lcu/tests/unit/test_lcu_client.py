"""Tests for LcuClient — lockfile parsing and API calls."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lol_lcu.lcu_client import LcuClient, LcuNotRunningError


class TestLockfileParsing:
    """Tests for reading the League lockfile."""

    def test_parse_lockfile(self, tmp_path):
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("LeagueClient:12345:54321:password123:https")
        client = LcuClient(install_path=str(tmp_path))
        assert client.port == 54321
        assert client.password == "password123"

    def test_missing_lockfile_raises(self, tmp_path):
        with pytest.raises(LcuNotRunningError):
            LcuClient(install_path=str(tmp_path))

    def test_empty_lockfile_raises(self, tmp_path):
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("")
        with pytest.raises(LcuNotRunningError):
            LcuClient(install_path=str(tmp_path))


class TestLcuClientApi:
    """Tests for LCU HTTP API calls."""

    def _make_client(self, tmp_path):
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("LeagueClient:12345:54321:password123:https")
        return LcuClient(install_path=str(tmp_path))

    @patch("lol_lcu.lcu_client.requests.get")
    def test_current_summoner(self, mock_get, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "puuid": "test-puuid",
            "gameName": "Faker",
            "tagLine": "KR1",
        }
        mock_get.return_value = mock_resp
        result = client.current_summoner()
        assert result["puuid"] == "test-puuid"
        assert result["gameName"] == "Faker"

    @patch("lol_lcu.lcu_client.requests.get")
    def test_current_summoner_not_running(self, mock_get, tmp_path):
        client = self._make_client(tmp_path)
        mock_get.side_effect = Exception("Connection refused")
        with pytest.raises(LcuNotRunningError):
            client.current_summoner()

    @patch("lol_lcu.lcu_client.requests.get")
    def test_match_history(self, mock_get, tmp_path):
        client = self._make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "games": {
                "games": [
                    {"gameId": 1, "gameCreation": 1700000000000},
                    {"gameId": 2, "gameCreation": 1700000001000},
                ]
            }
        }
        mock_get.return_value = mock_resp
        games = client.match_history("test-puuid", beg_index=0, end_index=20)
        assert len(games) == 2
        assert games[0]["gameId"] == 1

    @patch("lol_lcu.lcu_client.requests.get")
    def test_api_uses_correct_host(self, mock_get, tmp_path):
        client = self._make_client(tmp_path)
        client.host = "host.docker.internal"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "puuid": "test",
            "gameName": "Test",
            "tagLine": "NA1",
        }
        mock_get.return_value = mock_resp
        client.current_summoner()
        call_url = mock_get.call_args[0][0]
        assert "host.docker.internal" in call_url
