"""Tests for LcuClient — lockfile parsing and API calls."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from lol_lcu.lcu_client import LcuAuthError, LcuClient, LcuNotRunningError


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

    def test_too_few_parts_raises(self, tmp_path):
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("LeagueClient:12345")
        with pytest.raises(LcuNotRunningError, match="at least 4"):
            LcuClient(install_path=str(tmp_path))

    def test_non_numeric_port_raises(self, tmp_path):
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("LeagueClient:pid:abc:pass:https")
        with pytest.raises(LcuNotRunningError, match="non-numeric port"):
            LcuClient(install_path=str(tmp_path))

    def test_whitespace_only_raises(self, tmp_path):
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("  \n  ")
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
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")
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
    def test_http_403_raises_auth_error(self, mock_get, tmp_path):
        """A 403 means stale lockfile credentials, not 'LCU not running'."""
        client = self._make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "403 Client Error: Forbidden", response=mock_resp
        )
        mock_get.return_value = mock_resp
        with pytest.raises(LcuAuthError, match="stale"):
            client.current_summoner()

    @patch("lol_lcu.lcu_client.requests.get")
    def test_http_401_raises_auth_error(self, mock_get, tmp_path):
        """A 401 also indicates stale credentials."""
        client = self._make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "401 Client Error: Unauthorized", response=mock_resp
        )
        mock_get.return_value = mock_resp
        with pytest.raises(LcuAuthError):
            client.current_summoner()

    @patch("lol_lcu.lcu_client.requests.get")
    def test_connection_error_raises_not_running(self, mock_get, tmp_path):
        """Connection refused means LCU truly not running."""
        client = self._make_client(tmp_path)
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")
        with pytest.raises(LcuNotRunningError):
            client.current_summoner()

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


class TestLockfileValidation:
    """Tests for malformed lockfile handling."""

    def test_malformed_lockfile_too_few_parts(self, tmp_path):
        """Lockfile with too few colon-separated parts should raise."""
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("LeagueClient:12345")
        with pytest.raises((LcuNotRunningError, IndexError, ValueError)):
            LcuClient(install_path=str(tmp_path))

    def test_malformed_lockfile_non_numeric_port(self, tmp_path):
        """Lockfile with non-numeric port should raise."""
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("LeagueClient:pid:notaport:password:https")
        with pytest.raises((LcuNotRunningError, ValueError)):
            LcuClient(install_path=str(tmp_path))

    def test_whitespace_only_lockfile(self, tmp_path):
        """Whitespace-only lockfile treated as empty."""
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("   \n  ")
        with pytest.raises(LcuNotRunningError, match="empty"):
            LcuClient(install_path=str(tmp_path))

    def test_port_out_of_range_raises(self, tmp_path):
        """Port outside 1-65535 should raise."""
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("LeagueClient:pid:99999:pass:https")
        with pytest.raises(LcuNotRunningError, match="out of range"):
            LcuClient(install_path=str(tmp_path))

    def test_port_zero_raises(self, tmp_path):
        """Port 0 should raise."""
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("LeagueClient:pid:0:pass:https")
        with pytest.raises(LcuNotRunningError, match="out of range"):
            LcuClient(install_path=str(tmp_path))


class TestLcuClientGetEdgeCases:
    """Tests for _get method edge cases."""

    def _make_client(self, tmp_path):
        lockfile = tmp_path / "lockfile"
        lockfile.write_text("LeagueClient:12345:54321:password123:https")
        return LcuClient(install_path=str(tmp_path))

    @patch("lol_lcu.lcu_client.requests.get")
    def test_json_decode_error_raises_not_running(self, mock_get, tmp_path):
        """HTTP 200 but non-JSON body should raise LcuNotRunningError."""
        client = self._make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.side_effect = ValueError("No JSON")
        mock_get.return_value = mock_resp
        with pytest.raises(LcuNotRunningError):
            client.current_summoner()

    @patch("lol_lcu.lcu_client.requests.get")
    def test_timeout_raises_not_running(self, mock_get, tmp_path):
        """Connection timeout should raise LcuNotRunningError."""
        client = self._make_client(tmp_path)
        mock_get.side_effect = requests.exceptions.Timeout("timed out")
        with pytest.raises(LcuNotRunningError):
            client.current_summoner()

    @patch("lol_lcu.lcu_client.requests.get")
    def test_http_500_raises_not_running(self, mock_get, tmp_path):
        """HTTP 500 (not auth) should raise LcuNotRunningError, not LcuAuthError."""
        client = self._make_client(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500 Server Error", response=mock_resp
        )
        mock_get.return_value = mock_resp
        with pytest.raises(LcuNotRunningError):
            client.current_summoner()
