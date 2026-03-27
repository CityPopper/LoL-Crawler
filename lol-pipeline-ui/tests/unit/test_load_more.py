"""Tests for POST /stats/load_more — LOAD-MORE-1."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def _post_load_more(puuid: str):
    """POST /stats/load_more?puuid=... with a mocked app and return the response."""
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
        mock_cfg.default_region = "na1"
        mock_cfg_cls.return_value = mock_cfg

        mock_redis = AsyncMock()
        mock_redis.hget.return_value = "euw1"
        mock_redis.xadd.return_value = "1-0"
        mock_get_redis.return_value = mock_redis

        mock_riot = AsyncMock()
        mock_riot_cls.return_value = mock_riot

        from lol_ui.main import app

        with TestClient(app) as client:
            return client.post(f"/stats/load_more?puuid={puuid}"), mock_redis


class TestLoadMoreValidPuuid:
    """POST /stats/load_more with a valid puuid returns queued."""

    def test_load_more__valid_puuid__returns_queued(self) -> None:
        puuid = "abc123def456"
        resp, mock_redis = _post_load_more(puuid)
        assert resp.status_code == 200
        data = resp.json()
        assert data["queued"] is True
        # Verify publish was called (xadd on the stream)
        mock_redis.xadd.assert_called_once()


class TestLoadMoreInvalidPuuid:
    """POST /stats/load_more with invalid puuid returns 400."""

    def test_load_more__invalid_puuid__400(self) -> None:
        resp, _mock_redis = _post_load_more("../../etc/passwd")
        assert resp.status_code == 400
        data = resp.json()
        assert "invalid puuid" in data["error"]

    def test_load_more__empty_puuid__400(self) -> None:
        resp, _mock_redis = _post_load_more("")
        assert resp.status_code == 400
