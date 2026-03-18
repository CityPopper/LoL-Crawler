"""Unit tests for lol_discovery.main — player promotion with name resolution."""

from __future__ import annotations

import logging

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_pipeline.config import Config
from lol_pipeline.riot_api import RiotClient
from lol_discovery.main import _promote_batch


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    return Config(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def log():
    return logging.getLogger("test-discovery")


class TestPromoteBatchNames:
    @pytest.mark.asyncio
    async def test_uses_backfilled_names_when_available(self, r, cfg, log):
        """When parser has backfilled game_name/tag_line, discovery should use them."""
        # Backfill: parser set game_name/tag_line but no seeded_at
        await r.hset("player:puuid-abc", mapping={
            "game_name": "TestPlayer", "tag_line": "NA1",
        })
        await r.zadd("discover:players", {"puuid-abc:na1": 1700000000000.0})

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 1
        # Player hash should now have seeded_at and names
        assert await r.hget("player:puuid-abc", "game_name") == "TestPlayer"
        assert await r.hget("player:puuid-abc", "tag_line") == "NA1"
        assert await r.hget("player:puuid-abc", "seeded_at") is not None

        # Published payload should include game_name and tag_line
        entries = await r.xrange("stream:puuid")
        assert len(entries) == 1

    @pytest.mark.asyncio
    async def test_resolves_names_via_riot_api_when_not_backfilled(self, r, cfg, log):
        """When no backfilled names exist, discovery should resolve via Riot API."""
        await r.zadd("discover:players", {"puuid-xyz:na1": 1700000000000.0})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-xyz"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid-xyz", "gameName": "Resolved", "tagLine": "007"},
                )
            )
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 1
        assert await r.hget("player:puuid-xyz", "game_name") == "Resolved"
        assert await r.hget("player:puuid-xyz", "tag_line") == "007"

    @pytest.mark.asyncio
    async def test_removes_player_on_404(self, r, cfg, log):
        """When Riot API returns 404 for a PUUID, remove from queue permanently."""
        await r.zadd("discover:players", {"puuid-gone:na1": 1700000000000.0})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-gone"
            ).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 0
        assert await r.zcard("discover:players") == 0
        assert not await r.exists("player:puuid-gone")

    @pytest.mark.asyncio
    async def test_retains_player_on_transient_api_error(self, r, cfg, log):
        """Transient API errors (500) should leave player in queue for retry."""
        await r.zadd("discover:players", {"puuid-retry:na1": 1700000000000.0})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-retry"
            ).mock(return_value=httpx.Response(500, text="Internal Server Error"))
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 0
        # Player should STILL be in discovery queue for retry
        assert await r.zcard("discover:players") == 1

    @pytest.mark.asyncio
    async def test_skips_already_seeded_player(self, r, cfg, log):
        """Players seeded after being added to discover:players should be skipped."""
        await r.hset("player:puuid-seeded", mapping={
            "game_name": "Already", "tag_line": "Here",
            "region": "na1", "seeded_at": "2024-01-01T00:00:00+00:00",
        })
        await r.zadd("discover:players", {"puuid-seeded:na1": 1700000000000.0})

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 0
        assert await r.zcard("discover:players") == 0
