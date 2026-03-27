"""Tests for GET /stats/poll — lightweight JSON polling endpoint (UI-LOAD-1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch


def _get_poll(puuid: str, *, exists_side_effect: list[int] | None = None):
    """GET /stats/poll?puuid=... with a mocked app and return the response."""
    from starlette.testclient import TestClient

    with (
        patch("lol_ui.main.Config") as mock_cfg_cls,
        patch("lol_ui.main.get_redis") as mock_get_redis,
        patch("lol_ui.main.RiotClient") as mock_riot_cls,
    ):
        mock_cfg = MagicMock()
        mock_cfg.redis_url = "redis://localhost:6379/0"
        mock_cfg.riot_api_key = "RGAPI-test"
        mock_cfg.max_attempts = 5
        mock_cfg.stats_fragment_cache_ttl_s = 6 * 3600
        mock_cfg.opgg_enabled = False
        mock_cfg.blob_data_dir = ""
        mock_cfg_cls.return_value = mock_cfg

        mock_redis = AsyncMock()
        if exists_side_effect is not None:
            mock_redis.exists.side_effect = exists_side_effect
        else:
            mock_redis.exists.return_value = 0
        mock_redis.zcard.return_value = 0
        mock_get_redis.return_value = mock_redis

        mock_riot = AsyncMock()
        mock_riot_cls.return_value = mock_riot

        from lol_ui.main import app

        with TestClient(app) as client:
            return client.get(f"/stats/poll?puuid={puuid}"), mock_redis


class TestStatsPollEmptyStats:
    """GET /stats/poll returns not_ready when player:stats hash is empty."""

    def test_stats_poll__empty_stats__not_ready(self) -> None:
        puuid = "abc123def456"
        resp, mock_redis = _get_poll(puuid, exists_side_effect=[0, 0])
        mock_redis.exists.assert_any_call(f"player:stats:{puuid}")
        mock_redis.zcard.assert_called_once_with(f"player:matches:{puuid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stats_ready"] is False
        assert data["matches_processed"] == 0
        assert data["history_exhausted"] is False


class TestStatsPollStatsPresent:
    """GET /stats/poll returns ready when player:stats hash is populated."""

    def test_stats_poll__stats_present__ready(self) -> None:
        puuid = "abc123def456"

        with (
            patch("lol_ui.main.Config") as mock_cfg_cls,
            patch("lol_ui.main.get_redis") as mock_get_redis,
            patch("lol_ui.main.RiotClient") as mock_riot_cls,
        ):
            mock_cfg = MagicMock()
            mock_cfg.redis_url = "redis://localhost:6379/0"
            mock_cfg.riot_api_key = "RGAPI-test"
            mock_cfg.max_attempts = 5
            mock_cfg.stats_fragment_cache_ttl_s = 6 * 3600
            mock_cfg.opgg_enabled = False
            mock_cfg.blob_data_dir = ""
            mock_cfg_cls.return_value = mock_cfg

            mock_redis = AsyncMock()
            # First call: player:stats exists (1), second: history_exhausted (0)
            mock_redis.exists.side_effect = [1, 0]
            mock_redis.zcard.return_value = 15
            mock_get_redis.return_value = mock_redis

            mock_riot = AsyncMock()
            mock_riot_cls.return_value = mock_riot

            from lol_ui.main import app
            from starlette.testclient import TestClient

            with TestClient(app) as client:
                resp = client.get(f"/stats/poll?puuid={puuid}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["stats_ready"] is True
        assert data["matches_processed"] == 15
        assert data["history_exhausted"] is False


class TestStatsPollInvalidPuuid:
    """GET /stats/poll rejects invalid puuid with 400."""

    def test_stats_poll__invalid_puuid__400(self) -> None:
        resp, _mock_redis = _get_poll("../../etc/passwd")
        assert resp.status_code == 400


class TestStatsPollHistoryExhausted:
    """GET /stats/poll includes history_exhausted field."""

    def test_stats_poll__includes_history_exhausted_false(self) -> None:
        """r.exists returns 0 for history_exhausted -> false."""
        puuid = "abc123def456"
        resp, mock_redis = _get_poll(puuid, exists_side_effect=[0, 0])
        assert resp.status_code == 200
        data = resp.json()
        assert data["history_exhausted"] is False

    def test_stats_poll__includes_history_exhausted_true(self) -> None:
        """r.exists returns 1 for history_exhausted -> true."""
        puuid = "abc123def456"

        with (
            patch("lol_ui.main.Config") as mock_cfg_cls,
            patch("lol_ui.main.get_redis") as mock_get_redis,
            patch("lol_ui.main.RiotClient") as mock_riot_cls,
        ):
            mock_cfg = MagicMock()
            mock_cfg.redis_url = "redis://localhost:6379/0"
            mock_cfg.riot_api_key = "RGAPI-test"
            mock_cfg.max_attempts = 5
            mock_cfg.stats_fragment_cache_ttl_s = 6 * 3600
            mock_cfg.opgg_enabled = False
            mock_cfg.blob_data_dir = ""
            mock_cfg_cls.return_value = mock_cfg

            mock_redis = AsyncMock()
            # First call: player:stats (1), second: history_exhausted (1)
            mock_redis.exists.side_effect = [1, 1]
            mock_redis.zcard.return_value = 42
            mock_get_redis.return_value = mock_redis

            mock_riot = AsyncMock()
            mock_riot_cls.return_value = mock_riot

            from lol_ui.main import app
            from starlette.testclient import TestClient

            with TestClient(app) as client:
                resp = client.get(f"/stats/poll?puuid={puuid}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["stats_ready"] is True
        assert data["matches_processed"] == 42
        assert data["history_exhausted"] is True
