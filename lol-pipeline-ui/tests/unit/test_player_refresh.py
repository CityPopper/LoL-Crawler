"""Tests for POST /player/refresh — region validation (SEED-6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


class TestPlayerRefreshRegionValidation:
    """SEED-6: /player/refresh must reject invalid regions with 422."""

    def _post_refresh(self, body):
        """POST /player/refresh with a mocked app and return the response."""
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
            mock_cfg_cls.return_value = mock_cfg

            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis

            mock_riot = AsyncMock()
            mock_riot_cls.return_value = mock_riot

            from lol_ui.main import app

            with TestClient(app) as client:
                return client.post("/player/refresh", json=body)

    def test_player_refresh_invalid_region(self):
        """POST /player/refresh with invalid region returns 422."""
        resp = self._post_refresh({"riot_id": "Test#NA1", "region": "invalid_region"})
        assert resp.status_code == 422
        assert "invalid region" in resp.json()["error"]

    def test_player_refresh_valid_region_passes_validation(self):
        """POST /player/refresh with valid region does NOT return 422.

        It may return 400 (bad riot_id) or 404 (player not cached), but not 422.
        """
        resp = self._post_refresh({"riot_id": "NoHash", "region": "na1"})
        # Should not be 422 — region is valid; should be 400 for missing '#'
        assert resp.status_code == 400
