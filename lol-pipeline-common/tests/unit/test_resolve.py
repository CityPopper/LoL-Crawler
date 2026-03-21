"""Unit tests for lol_pipeline.resolve — unified PUUID resolution."""

from __future__ import annotations

import logging

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_pipeline.resolve import resolve_puuid
from lol_pipeline.riot_api import RiotClient


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def log():
    return logging.getLogger("test-resolve")


def _account_response(puuid: str = "test-puuid-001") -> httpx.Response:
    return httpx.Response(
        200,
        json={"puuid": puuid, "gameName": "Player", "tagLine": "NA1"},
        headers={"X-App-Rate-Limit": "20:1,100:120"},
    )


class TestResolvePuuidCache:
    @pytest.mark.asyncio
    async def test_returns_cached_puuid(self, r, log):
        """Cached player:name key → returns without API call."""
        await r.set("player:name:player#na1", "cached-puuid")
        with respx.mock:
            # No routes — any HTTP call would raise
            riot = RiotClient("RGAPI-test")
            result = await resolve_puuid(r, riot, "Player", "NA1", "na1", log)
            await riot.close()
        assert result == "cached-puuid"


class TestResolvePuuidApi:
    @pytest.mark.asyncio
    async def test_api_fallback_and_caches(self, r, log):
        """No cache → resolves via API and writes cache with TTL."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Player/NA1"
            ).mock(return_value=_account_response())
            riot = RiotClient("RGAPI-test")
            result = await resolve_puuid(r, riot, "Player", "NA1", "na1", log)
            await riot.close()
        assert result == "test-puuid-001"
        assert await r.get("player:name:player#na1") == "test-puuid-001"
        ttl = await r.ttl("player:name:player#na1")
        assert 86390 <= ttl <= 86400


class TestResolvePuuidErrors:
    @pytest.mark.asyncio
    async def test_404_returns_none(self, r, log):
        """NotFoundError → returns None, no system halt."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Nobody/NA1"
            ).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            result = await resolve_puuid(r, riot, "Nobody", "NA1", "na1", log)
            await riot.close()
        assert result is None
        assert await r.get("system:halted") is None

    @pytest.mark.asyncio
    async def test_403_halts_system(self, r, log):
        """AuthError → sets system:halted, returns None."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Player/NA1"
            ).mock(return_value=httpx.Response(403))
            riot = RiotClient("RGAPI-test")
            result = await resolve_puuid(r, riot, "Player", "NA1", "na1", log)
            await riot.close()
        assert result is None
        assert await r.get("system:halted") == "1"

    @pytest.mark.asyncio
    async def test_429_returns_none(self, r, log):
        """RateLimitError → returns None, no halt."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Player/NA1"
            ).mock(return_value=httpx.Response(429))
            riot = RiotClient("RGAPI-test")
            result = await resolve_puuid(r, riot, "Player", "NA1", "na1", log)
            await riot.close()
        assert result is None
        assert await r.get("system:halted") is None

    @pytest.mark.asyncio
    async def test_5xx_returns_none(self, r, log):
        """ServerError → returns None, no halt."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Player/NA1"
            ).mock(return_value=httpx.Response(503))
            riot = RiotClient("RGAPI-test")
            result = await resolve_puuid(r, riot, "Player", "NA1", "na1", log)
            await riot.close()
        assert result is None
        assert await r.get("system:halted") is None
