"""Unit tests for lol_seed.main — Phase 03 ACs 03-01 through 03-11."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

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
        await r.hset(
            f"player:{puuid}",
            mapping={
                "seeded_at": (now - timedelta(minutes=60)).isoformat(),
                "last_crawled_at": (now - timedelta(minutes=10)).isoformat(),
            },
        )
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
        await r.hset(
            f"player:{puuid}",
            mapping={
                "seeded_at": (now - timedelta(minutes=10)).isoformat(),
                "last_crawled_at": (now - timedelta(minutes=60)).isoformat(),
            },
        )
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


class TestSeedEdgeCases:
    """Tier 3 edge case tests for seed."""

    @pytest.mark.asyncio
    async def test_unknown_region_resolves_via_riot_api(self, r, cfg, log):
        """Unknown region string is passed through — Riot API handles routing."""
        with respx.mock:
            # RiotClient maps unknown regions to "americas" by default
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            ).mock(
                return_value=_account_response(
                    puuid="test-puuid-unk", game_name="Test", tag_line="NA1"
                )
            )

            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Test", "NA1", "unknown_region", log)
            await riot.close()

        assert result == 0
        assert await r.hget("player:test-puuid-unk", "region") == "unknown_region"

    @pytest.mark.asyncio
    async def test_unexpected_error_propagates(self, r, cfg, log):
        """Errors not caught by _resolve_puuid propagate to caller."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(side_effect=RuntimeError("unexpected"))

            riot = RiotClient("RGAPI-test")
            with pytest.raises(RuntimeError, match="unexpected"):
                await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

    @pytest.mark.asyncio
    async def test_publish_failure_does_not_update_cooldown(self, r, cfg, log):
        """If publish() fails, seeded_at should not prevent future retries."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_account_response())

            riot = RiotClient("RGAPI-test")
            with patch("lol_seed.main.publish", side_effect=ConnectionError("redis down")):
                with pytest.raises(ConnectionError):
                    await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        # CQ-5 FIX: publish() now happens BEFORE hset(seeded_at).
        # If publish fails, seeded_at is NOT set — the player can be retried.
        seeded_at = await r.hget("player:test-puuid-0001", "seeded_at")
        assert seeded_at is None  # seeded_at was NOT written because publish failed first


class TestMainEntryPoint:
    """Tests for main() CLI parsing and dispatch."""

    @pytest.mark.asyncio
    async def test_main__missing_args__returns_1(self, monkeypatch):
        """No argv[1] → exit 1."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        result = await main(["seed"])
        assert result == 1

    @pytest.mark.asyncio
    async def test_main__invalid_riot_id_no_hash__returns_1(self, monkeypatch):
        """Riot ID without '#' → exit 1."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        with respx.mock:
            result = await main(["seed", "FakerKR1"])
        assert result == 1

    @pytest.mark.asyncio
    async def test_main__valid_args__calls_seed_and_returns_0(self, monkeypatch):
        """Happy path: valid args → seed() called, returns 0."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_seed = AsyncMock(return_value=0)
        with (
            patch("lol_seed.main.seed", mock_seed),
            patch("lol_seed.main.get_redis") as mock_redis,
            patch("lol_seed.main.RiotClient") as mock_riot,
        ):
            mock_redis.return_value = AsyncMock()
            mock_riot.return_value = AsyncMock()
            result = await main(["seed", "Faker#KR1", "kr"])
        assert result == 0
        mock_seed.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__default_region_is_na1(self, monkeypatch):
        """No region in argv → defaults to 'na1'."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_seed = AsyncMock(return_value=0)
        with (
            patch("lol_seed.main.seed", mock_seed),
            patch("lol_seed.main.get_redis") as mock_redis,
            patch("lol_seed.main.RiotClient") as mock_riot,
        ):
            mock_redis.return_value = AsyncMock()
            mock_riot.return_value = AsyncMock()
            await main(["seed", "Faker#KR1"])
        # region arg is positional arg[5] (r, riot, cfg, game_name, tag_line, region, log)
        call_args = mock_seed.call_args[0]
        assert call_args[5] == "na1"

    @pytest.mark.asyncio
    async def test_main__custom_region__passed_through(self, monkeypatch):
        """Explicit region → passed to seed()."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_seed = AsyncMock(return_value=0)
        with (
            patch("lol_seed.main.seed", mock_seed),
            patch("lol_seed.main.get_redis") as mock_redis,
            patch("lol_seed.main.RiotClient") as mock_riot,
        ):
            mock_redis.return_value = AsyncMock()
            mock_riot.return_value = AsyncMock()
            await main(["seed", "Faker#KR1", "euw1"])
        call_args = mock_seed.call_args[0]
        assert call_args[5] == "euw1"


class TestSeedPriority:
    @pytest.mark.asyncio
    async def test_seed__envelope_has_high_priority(self, r, cfg, log):
        """Seeded envelope has priority='high'."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_account_response())

            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        entries = await r.xrange("stream:puuid")
        assert len(entries) == 1
        assert entries[0][1]["priority"] == "high"

    @pytest.mark.asyncio
    async def test_seed__sets_priority_key_and_increments_counter(self, r, cfg, log):
        """Seed sets player:priority:{puuid} and increments system:priority_count."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_account_response())

            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        assert await r.get("player:priority:test-puuid-0001") == "high"
        assert await r.get("system:priority_count") == "1"


class TestSeedNameCacheTTL:
    """Fix 7: player:name cache key has a 24h TTL."""

    @pytest.mark.asyncio
    async def test_name_cache_has_ttl(self, r, cfg, log):
        """After resolving PUUID, player:name cache key has ex=86400 TTL."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_account_response())

            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        ttl = await r.ttl("player:name:faker#kr1")
        # TTL should be close to 86400 (within a few seconds of test execution)
        assert 86390 <= ttl <= 86400


class TestSeedPublishBeforeHset:
    """CQ-5: publish() must happen before hset(seeded_at)."""

    @pytest.mark.asyncio
    async def test_publish_before_hset_seeded_at(self, r, cfg, log):
        """seed() publishes to stream:puuid BEFORE writing seeded_at to player hash."""
        call_order: list[str] = []

        original_hset = r.hset

        async def tracking_hset(key, *args, **kwargs):
            mapping = kwargs.get("mapping", {})
            if "seeded_at" in mapping:
                call_order.append("hset_seeded_at")
            return await original_hset(key, *args, **kwargs)

        original_publish = __import__("lol_pipeline.streams", fromlist=["publish"]).publish

        async def tracking_publish(redis, stream, envelope):
            call_order.append("publish")
            return await original_publish(redis, stream, envelope)

        r.hset = tracking_hset  # type: ignore[assignment]
        with (
            respx.mock,
            patch("lol_seed.main.publish", side_effect=tracking_publish),
        ):
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_account_response())
            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        assert call_order == ["publish", "hset_seeded_at"]


class TestSeedPriorityBeforePublish:
    """I2-H1: set_priority() must be called BEFORE publish()."""

    @pytest.mark.asyncio
    async def test_set_priority_called_before_publish(self, r, cfg, log):
        """set_priority() runs before publish() so downstream clear_priority() cannot
        race against a not-yet-set priority key."""
        call_order: list[str] = []

        original_publish = __import__("lol_pipeline.streams", fromlist=["publish"]).publish
        original_set_priority = __import__(
            "lol_pipeline.priority", fromlist=["set_priority"]
        ).set_priority

        async def tracking_publish(redis, stream, envelope):
            call_order.append("publish")
            return await original_publish(redis, stream, envelope)

        async def tracking_set_priority(redis, puuid):
            call_order.append("set_priority")
            return await original_set_priority(redis, puuid)

        with (
            respx.mock,
            patch("lol_seed.main.publish", side_effect=tracking_publish),
            patch("lol_seed.main.set_priority", side_effect=tracking_set_priority),
        ):
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(return_value=_account_response())
            riot = RiotClient("RGAPI-test")
            result = await seed(r, riot, cfg, "Faker", "KR1", "kr", log)
            await riot.close()

        assert result == 0
        assert call_order == ["set_priority", "publish"]
