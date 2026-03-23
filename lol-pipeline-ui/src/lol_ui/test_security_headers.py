"""Tests for security headers — Permissions-Policy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


class TestPermissionsPolicy:
    """SEC-1: Permissions-Policy header present on every response."""

    def _get_response(self):
        from starlette.testclient import TestClient

        with (
            patch("lol_ui.main.Config") as mock_cfg_cls,
            patch("lol_ui.main.get_redis") as mock_get_redis,
            patch("lol_ui.main.RiotClient") as mock_riot_cls,
        ):
            mock_cfg = MagicMock()
            mock_cfg.redis_url = "redis://localhost:6379/0"
            mock_cfg.riot_api_key = "RGAPI-test"
            mock_cfg_cls.return_value = mock_cfg

            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis

            mock_riot = AsyncMock()
            mock_riot_cls.return_value = mock_riot

            from lol_ui.main import app

            with TestClient(app) as client:
                return client.get("/health")

    def test_permissions_policy__present(self):
        resp = self._get_response()
        pp = resp.headers.get("Permissions-Policy")
        assert pp is not None

    def test_permissions_policy__denies_camera(self):
        resp = self._get_response()
        pp = resp.headers.get("Permissions-Policy", "")
        assert "camera=()" in pp

    def test_permissions_policy__denies_microphone(self):
        resp = self._get_response()
        pp = resp.headers.get("Permissions-Policy", "")
        assert "microphone=()" in pp

    def test_permissions_policy__denies_geolocation(self):
        resp = self._get_response()
        pp = resp.headers.get("Permissions-Policy", "")
        assert "geolocation=()" in pp

    def test_permissions_policy__denies_payment(self):
        resp = self._get_response()
        pp = resp.headers.get("Permissions-Policy", "")
        assert "payment=()" in pp
