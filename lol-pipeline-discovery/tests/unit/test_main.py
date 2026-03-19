"""Unit tests for lol_discovery.main — player promotion with name resolution."""

from __future__ import annotations

import logging

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_pipeline.config import Config
from lol_pipeline.riot_api import RiotClient
from lol_discovery.main import _is_idle, _parse_member, _promote_batch, _resolve_names


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


class TestParseMember:
    def test_puuid_with_region(self):
        puuid, region = _parse_member("abc-def-123:na1")
        assert puuid == "abc-def-123"
        assert region == "na1"

    def test_puuid_without_region(self):
        puuid, region = _parse_member("abc-def-123")
        assert puuid == "abc-def-123"
        assert region == "na1"  # default

    def test_puuid_with_colons(self):
        """PUUIDs can contain colons — rfind ensures last colon is the separator."""
        puuid, region = _parse_member("some:complex:puuid:euw1")
        assert puuid == "some:complex:puuid"
        assert region == "euw1"


class TestIsIdle:
    @pytest.mark.asyncio
    async def test_no_stream_returns_true(self, r):
        """When stream doesn't exist, pipeline is idle."""
        assert await _is_idle(r) is True

    @pytest.mark.asyncio
    async def test_no_groups_returns_true(self, r):
        """Stream exists but no consumer groups — idle."""
        await r.xadd("stream:puuid", {"id": "test", "source_stream": "stream:puuid",
                                       "type": "puuid", "payload": "{}", "attempts": "0",
                                       "max_attempts": "5", "enqueued_at": "2024-01-01",
                                       "dlq_attempts": "0"})
        assert await _is_idle(r) is True

    @pytest.mark.asyncio
    async def test_pending_messages_not_idle(self, r):
        """When group has pending (unACKed) messages, not idle."""
        from lol_pipeline.models import MessageEnvelope
        from lol_pipeline.streams import consume, publish
        env = MessageEnvelope(source_stream="stream:puuid", type="puuid",
                             payload={"puuid": "test"}, max_attempts=5)
        await publish(r, "stream:puuid", env)
        await consume(r, "stream:puuid", "crawlers", "c1", block=0)
        # Message delivered but not ACKed
        assert await _is_idle(r) is False


class TestPromoteBatchEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_queue(self, r, cfg, log):
        """Empty discover:players → 0 promoted."""
        riot = RiotClient("RGAPI-test")
        assert await _promote_batch(r, cfg, log, riot) == 0
        await riot.close()

    @pytest.mark.asyncio
    async def test_halted_system_returns_zero(self, r, cfg, log):
        """When system:halted, no promotions occur."""
        await r.set("system:halted", "1")
        await r.zadd("discover:players", {"puuid-abc:na1": 1700000000000.0})
        await r.hset("player:puuid-abc", mapping={"game_name": "T", "tag_line": "1"})
        riot = RiotClient("RGAPI-test")
        assert await _promote_batch(r, cfg, log, riot) == 0
        await riot.close()
        # Player should still be in queue
        assert await r.zcard("discover:players") == 1
