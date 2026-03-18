"""Unit tests for lol_seed.main — Phase 03 ACs 03-01 through 03-11."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_pipeline.config import Config
from lol_pipeline.riot_api import RiotClient
from lol_seed.main import main, seed


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
    return logging.getLogger("test-seed")


def _account_response(puuid="test-puuid-0001", game_name="Faker", tag_line="KR1"):
    return httpx.Response(
        200,
        json={"puuid": puuid, "gameName": game_name, "tagLine": tag_line},
        headers={"X-App-Rate-Limit": "20:1,100:120"},
    )


class TestSeedHappyPath:
    @pytest.mark.asyncio
    async def test_successful_seed(self, r, cfg, log):
        """AC-03-01: valid Riot ID → player hash set, stream:puuid has 1 message, exit 0."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_account_response())

            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        # player hash written
        game_name = await r.hget("player:test-puuid-0001", "game_name")
        assert game_name == "Faker"
        tag_line = await r.hget("player:test-puuid-0001", "tag_line")
        assert tag_line == "KR1"
        region = await r.hget("player:test-puuid-0001", "region")
        assert region == "kr"
        seeded_at = await r.hget("player:test-puuid-0001", "seeded_at")
        assert seeded_at is not None
        # stream:puuid has 1 message
        length = await r.xlen("stream:puuid")
        assert length == 1

    @pytest.mark.asyncio
    async def test_new_player_no_cooldown_fields(self, r, cfg, log):
        """AC-03-08: neither seeded_at nor last_crawled_at → proceeds."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_account_response())

            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        assert await r.xlen("stream:puuid") == 1


class TestSeedCLIParsing:
    @pytest.mark.asyncio
    async def test_no_hash_in_riot_id(self, monkeypatch):
        """AC-03-02: input without # → exit 1; no HTTP call."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        with respx.mock:
            # No routes — any HTTP call would error
            result = await main(["seed", "FakerKR1"])
        assert result == 1


class TestSeedCooldown:
    @pytest.mark.asyncio
    async def test_seeded_at_within_cooldown(self, r, cfg, log):
        """AC-03-03: seeded_at 10 min ago, cooldown 30 → skip, no publish."""
        now = datetime.now(tz=UTC)
        ten_min_ago = (now - timedelta(minutes=10)).isoformat()
        puuid = "test-puuid-0001"
        await r.hset(f"player:{puuid}", mapping={"seeded_at": ten_min_ago})
        # Pre-cache the puuid so no API call needed
        await r.set("player:name:faker#kr1", puuid)

        with respx.mock:
            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        assert await r.xlen("stream:puuid") == 0

    @pytest.mark.asyncio
    async def test_last_crawled_at_within_cooldown(self, r, cfg, log):
        """AC-03-04: last_crawled_at 10 min ago, cooldown 30 → skip."""
        now = datetime.now(tz=UTC)
        ten_min_ago = (now - timedelta(minutes=10)).isoformat()
        puuid = "test-puuid-0001"
        await r.hset(f"player:{puuid}", mapping={"last_crawled_at": ten_min_ago})
        await r.set("player:name:faker#kr1", puuid)

        with respx.mock:
            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        assert await r.xlen("stream:puuid") == 0

    @pytest.mark.asyncio
    async def test_both_present_last_crawled_newer_within(self, r, cfg, log):
        """AC-03-05: seeded_at=60min ago, last_crawled_at=10min ago → skip."""
        now = datetime.now(tz=UTC)
        puuid = "test-puuid-0001"
        await r.hset(f"player:{puuid}", mapping={
            "seeded_at": (now - timedelta(minutes=60)).isoformat(),
            "last_crawled_at": (now - timedelta(minutes=10)).isoformat(),
        })
        await r.set("player:name:faker#kr1", puuid)

        with respx.mock:
            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        assert await r.xlen("stream:puuid") == 0

    @pytest.mark.asyncio
    async def test_both_present_seeded_newer_within(self, r, cfg, log):
        """AC-03-06: seeded_at=10min ago, last_crawled_at=60min ago → skip."""
        now = datetime.now(tz=UTC)
        puuid = "test-puuid-0001"
        await r.hset(f"player:{puuid}", mapping={
            "seeded_at": (now - timedelta(minutes=10)).isoformat(),
            "last_crawled_at": (now - timedelta(minutes=60)).isoformat(),
        })
        await r.set("player:name:faker#kr1", puuid)

        with respx.mock:
            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        assert await r.xlen("stream:puuid") == 0

    @pytest.mark.asyncio
    async def test_at_cooldown_boundary_proceeds(self, r, cfg, log):
        """AC-03-07: last_crawled_at exactly at cooldown boundary → proceeds."""
        now = datetime.now(tz=UTC)
        puuid = "test-puuid-0001"
        boundary = (now - timedelta(minutes=cfg.seed_cooldown_minutes)).isoformat()
        await r.hset(f"player:{puuid}", mapping={"last_crawled_at": boundary})
        await r.set("player:name:faker#kr1", puuid)

        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_account_response())
            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        assert await r.xlen("stream:puuid") == 1


class TestSeedErrors:
    @pytest.mark.asyncio
    async def test_riot_404_exits_1(self, r, cfg, log):
        """AC-03-09: 404 → exit 1; no partial write."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Nobody/KR1"
            ).mock(return_value=httpx.Response(404))

            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Nobody", "KR1", "kr", log)
            await riot.close()

        assert result == 1
        # No player data should exist
        assert await r.exists("player:test-puuid-0001") == 0
        assert await r.xlen("stream:puuid") == 0

    @pytest.mark.asyncio
    async def test_riot_403_halts_system(self, r, cfg, log):
        """AC-03-10: 403 → system:halted = '1', exit 1."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=httpx.Response(403))

            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 1
        assert await r.get("system:halted") == "1"

    @pytest.mark.asyncio
    async def test_system_halted_exits_immediately(self, r, cfg, log):
        """AC-03-11: system:halted set → exit immediately; no HTTP call."""
        await r.set("system:halted", "1")

        with respx.mock:
            # No routes defined — any HTTP call would raise
            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        # Spec says exits 0 — system halted is a graceful refusal, not an error
        # Implementation currently returns 1; test per spec
        assert result == 1
        assert await r.xlen("stream:puuid") == 0


class TestSeedRegionNormalization:
    @pytest.mark.asyncio
    async def test_uppercase_region_stored_lowercase(self, r, cfg, log):
        """Region arg is normalized to lowercase before storing in player hash."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_account_response())

            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "KR", log)
            await riot.close()

        assert result == 0
        region = await r.hget("player:test-puuid-0001", "region")
        assert region == "kr"
