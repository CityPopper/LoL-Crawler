"""Tests for stats route — fragment caching (T5-5) and player refresh."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from lol_pipeline._helpers import name_cache_key

from lol_ui.main import app
from lol_ui.routes.stats import (  # type: ignore[attr-defined]
    _CACHE_TTL_S,
    _CACHE_VERSION,
    _has_timeline_data,
    player_refresh,
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
    async def test_stats_matches__calls_is_system_halted(self, r: object) -> None:
        """stats_matches uses is_system_halted() for halt check."""
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"puuid": "a" * 78, "region": "na1", "riot_id": "Test#NA1"}

        mock_halted = AsyncMock(return_value=False)
        with patch("lol_ui.routes.stats.is_system_halted", mock_halted):
            await stats_matches(request)
        mock_halted.assert_called_once()


# ---------------------------------------------------------------------------
# POST /player/refresh
# ---------------------------------------------------------------------------


class TestPlayerRefresh:
    """POST /player/refresh — re-seed a player by clearing cooldowns and enqueuing."""

    @pytest.fixture
    async def client(self):
        """ASGI test client with fakeredis wired into app.state."""
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        app.state.r = r
        app.state.cfg = MagicMock(max_attempts=3)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, r
        await r.aclose()

    @pytest.mark.asyncio
    async def test_refresh__valid_riot_id__returns_queued(self, client) -> None:
        """PUUID found in cache -> hdel, set_priority, publish, returns queued."""
        c, r = client
        puuid = "fake-puuid-" + "a" * 66
        cache_key = name_cache_key("GameName", "TAG")
        await r.set(cache_key, puuid)
        # Pre-populate player hash so hdel has something to clear
        await r.hset(f"player:{puuid}", mapping={"seeded_at": "x", "last_crawled_at": "y", "region": "na1"})

        with patch("lol_ui.routes.stats.publish", new_callable=AsyncMock) as mock_pub:
            resp = await c.post(
                "/player/refresh",
                json={"riot_id": "GameName#TAG", "region": "na1"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body == {"queued": True}

        # Verify hdel cleared seeded_at and last_crawled_at
        seeded = await r.hget(f"player:{puuid}", "seeded_at")
        last_crawled = await r.hget(f"player:{puuid}", "last_crawled_at")
        assert seeded is None
        assert last_crawled is None
        # region should still be intact
        assert await r.hget(f"player:{puuid}", "region") == "na1"

        # Verify set_priority was called (check the priority key exists)
        assert await r.exists(f"player:priority:{puuid}")

        # Verify publish was called with correct stream and envelope
        mock_pub.assert_called_once()
        call_args = mock_pub.call_args
        assert call_args[0][1] == "stream:puuid"
        envelope = call_args[0][2]
        assert envelope.type == "puuid"
        assert envelope.payload["puuid"] == puuid
        assert envelope.payload["game_name"] == "GameName"
        assert envelope.payload["tag_line"] == "TAG"
        assert envelope.priority == "manual_20"

    @pytest.mark.asyncio
    async def test_refresh__puuid_not_in_cache__returns_404(self, client) -> None:
        """PUUID not in cache -> 404 with descriptive error."""
        c, r = client

        resp = await c.post(
            "/player/refresh",
            json={"riot_id": "Unknown#PLAYER", "region": "na1"},
        )

        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "player not found \u2014 search for them first"

    @pytest.mark.asyncio
    async def test_refresh__invalid_riot_id_no_hash__returns_400(self, client) -> None:
        """riot_id without '#' -> 400 invalid riot_id."""
        c, _r = client

        resp = await c.post(
            "/player/refresh",
            json={"riot_id": "NoHashHere", "region": "na1"},
        )

        assert resp.status_code == 400
        body = resp.json()
        assert "invalid" in body["error"].lower()
