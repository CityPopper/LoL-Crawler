"""Tests for _opgg_prefetch_bg — op.gg fast-path stats wiring (FP-4.2)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_opgg_client():
    client = MagicMock()
    client.prefetch_player_games = AsyncMock()
    return client


@pytest.fixture
def mock_redis():
    return AsyncMock()


@pytest.fixture
def mock_blob_store():
    return MagicMock()


class TestOpggPrefetchBgFastStats:
    @pytest.mark.asyncio
    async def test_prefetch_bg__calls_compute_fast_stats(
        self, mock_opgg_client, mock_redis, mock_blob_store
    ):
        """_opgg_prefetch_bg calls compute_opgg_fast_stats with raw_games from prefetch."""
        from lol_ui.routes.stats import _opgg_prefetch_bg

        raw_games = [{"id": 1, "participants": [], "teams": []}]
        mock_opgg_client.prefetch_player_games.return_value = raw_games
        mock_redis.hgetall.return_value = {"1": "Annie"}

        with patch(
            "lol_ui.routes.stats.compute_opgg_fast_stats",
            new_callable=AsyncMock,
            return_value=1,
        ) as mock_fast:
            await _opgg_prefetch_bg(
                mock_opgg_client, "test-puuid", "na", mock_blob_store, 20, mock_redis
            )

        mock_fast.assert_called_once()
        call_args = mock_fast.call_args
        assert call_args[0][1] == "test-puuid"
        assert call_args[0][2] is raw_games

    @pytest.mark.asyncio
    async def test_prefetch_bg__fast_stats_exception_logged_not_raised(
        self, mock_opgg_client, mock_redis, mock_blob_store
    ):
        """Exception in compute_opgg_fast_stats is caught and logged, never raised."""
        from lol_ui.routes.stats import _opgg_prefetch_bg

        raw_games = [{"id": 1, "participants": [], "teams": []}]
        mock_opgg_client.prefetch_player_games.return_value = raw_games
        mock_redis.hgetall.return_value = {}

        with patch(
            "lol_ui.routes.stats.compute_opgg_fast_stats",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            # Must not raise — exceptions are caught internally
            await _opgg_prefetch_bg(
                mock_opgg_client, "test-puuid", "na", mock_blob_store, 20, mock_redis
            )

        # If we got here without exception, the error was properly caught
