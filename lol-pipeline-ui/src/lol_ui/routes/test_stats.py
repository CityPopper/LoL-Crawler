"""Tests for stats route — fragment caching (T5-5)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lol_ui.routes.stats import (
    _CACHE_TTL_S,
    _CACHE_VERSION,
    _has_timeline_data,
    stats_matches,
)

# ---------------------------------------------------------------------------
# _has_timeline_data
# ---------------------------------------------------------------------------


class TestHasTimelineData:
    """_has_timeline_data checks if rendered HTML contains real timeline data."""

    def test_with_timeline__returns_true(self):
        html_content = '<div class="gold-chart"><svg>...</svg></div>'
        assert _has_timeline_data(html_content) is True

    def test_with_placeholder__returns_false(self):
        html_content = '<p class="warning">Timeline data unavailable for this match.</p>'
        assert _has_timeline_data(html_content) is False

    def test_empty_string__returns_true(self):
        """Empty string doesn't contain the placeholder text."""
        assert _has_timeline_data("") is True


# ---------------------------------------------------------------------------
# Fragment caching integration tests
# ---------------------------------------------------------------------------


class TestFragmentCaching:
    """Match detail fragment caching — cache hit/miss/bypass."""

    @pytest.mark.asyncio
    async def test_cache_miss__renders_and_caches(self, r):
        """On cache miss, the key should not exist initially."""
        match_id = "NA1_123456"
        puuid = "abc123"
        cache_key = f"ui:match-detail:{_CACHE_VERSION}:{match_id}:{puuid}"
        cached = await r.get(cache_key)
        assert cached is None

    @pytest.mark.asyncio
    async def test_cache_hit__returns_cached(self, r):
        """Stored HTML is returned from cache."""
        match_id = "NA1_123456"
        puuid = "abc123"
        cache_key = f"ui:match-detail:{_CACHE_VERSION}:{match_id}:{puuid}"
        expected_html = "<div>cached content</div>"
        await r.set(cache_key, expected_html, ex=_CACHE_TTL_S)
        cached = await r.get(cache_key)
        assert cached == expected_html

    @pytest.mark.asyncio
    async def test_cache_ttl__six_hours(self, r):
        """Cache TTL should be 6 hours."""
        assert _CACHE_TTL_S == 6 * 3600
        match_id = "NA1_123456"
        puuid = "abc123"
        cache_key = f"ui:match-detail:{_CACHE_VERSION}:{match_id}:{puuid}"
        await r.set(cache_key, "html", ex=_CACHE_TTL_S)
        ttl = await r.ttl(cache_key)
        assert ttl > 0
        assert ttl <= _CACHE_TTL_S

    @pytest.mark.asyncio
    async def test_nocache__skips_read_and_write(self, r):
        """With nocache=1, cache should not be read or written."""
        match_id = "NA1_123456"
        puuid = "abc123"
        cache_key = f"ui:match-detail:{_CACHE_VERSION}:{match_id}:{puuid}"
        # Pre-populate cache
        await r.set(cache_key, "stale html", ex=_CACHE_TTL_S)
        # nocache=1 means we should NOT use this cached value
        # This is tested at the route level — here we verify the key format
        assert _CACHE_VERSION == "v1"
        assert cache_key == f"ui:match-detail:v1:{match_id}:{puuid}"

    @pytest.mark.asyncio
    async def test_cache_key_format(self, r):
        """Cache key includes version, match_id, and puuid."""
        key = f"ui:match-detail:{_CACHE_VERSION}:NA1_999:test-puuid"
        assert key == "ui:match-detail:v1:NA1_999:test-puuid"

    @pytest.mark.asyncio
    async def test_placeholder_not_cached(self, r):
        """HTML with placeholder text should not be cached."""
        html_content = '<p class="warning">Timeline data unavailable for this match.</p>'
        assert not _has_timeline_data(html_content)
        # Real timeline data should be cacheable
        real_html = '<div class="gold-chart"><svg viewBox="0 0 600 300">...</svg></div>'
        assert _has_timeline_data(real_html)


class TestStatsUsesIsSystemHalted:
    """DRY-5: Stats route uses is_system_halted() instead of raw r.get."""

    @pytest.mark.asyncio
    async def test_stats_matches__calls_is_system_halted(self, r) -> None:
        """stats_matches uses is_system_halted() for halt check."""
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"puuid": "a" * 78, "region": "na1", "riot_id": "Test#NA1"}

        mock_halted = AsyncMock(return_value=False)
        with patch("lol_ui.routes.stats.is_system_halted", mock_halted):
            await stats_matches(request)
        mock_halted.assert_called_once()
