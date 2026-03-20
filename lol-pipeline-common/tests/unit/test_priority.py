"""Unit tests for lol_pipeline.priority — SCAN-based priority detection."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.priority import clear_priority, has_priority_players, set_priority


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestSetPriority:
    @pytest.mark.asyncio
    async def test_set_priority__creates_key_with_ttl(self, r):
        """set_priority creates player:priority:{puuid} with a TTL (B12)."""
        await set_priority(r, "puuid-abc")

        val = await r.get("player:priority:puuid-abc")
        assert val == "1"
        ttl = await r.ttl("player:priority:puuid-abc")
        assert ttl > 0  # has expiry — prevents orphaned keys blocking Discovery

    @pytest.mark.asyncio
    async def test_set_priority__idempotent_nx(self, r):
        """SET NX means second call does not overwrite."""
        await set_priority(r, "puuid-abc")
        await set_priority(r, "puuid-abc")
        val = await r.get("player:priority:puuid-abc")
        assert val == "1"

    @pytest.mark.asyncio
    async def test_set_priority__no_counter_key(self, r):
        """set_priority must NOT create system:priority_count (counter removed)."""
        await set_priority(r, "puuid-abc")
        assert await r.exists("system:priority_count") == 0


class TestClearPriority:
    @pytest.mark.asyncio
    async def test_clear_priority__deletes_key(self, r):
        """clear_priority deletes the player:priority:{puuid} key."""
        await set_priority(r, "puuid-abc")
        assert await r.get("player:priority:puuid-abc") == "1"

        await clear_priority(r, "puuid-abc")
        assert await r.get("player:priority:puuid-abc") is None

    @pytest.mark.asyncio
    async def test_clear_priority__nonexistent_key__no_error(self, r):
        """Clearing a non-existent key does not raise."""
        await clear_priority(r, "puuid-nonexistent")
        # Should succeed silently

    @pytest.mark.asyncio
    async def test_clear_priority__no_counter_key(self, r):
        """clear_priority must NOT touch system:priority_count (counter removed)."""
        await set_priority(r, "puuid-abc")
        await clear_priority(r, "puuid-abc")
        assert await r.exists("system:priority_count") == 0


class TestSetPriorityIdempotency:
    @pytest.mark.asyncio
    async def test_set_priority__same_puuid_twice__only_one_key(self, r):
        """Calling set_priority twice for the same PUUID creates only one key."""
        await set_priority(r, "puuid-dup")
        await set_priority(r, "puuid-dup")
        assert await r.exists("player:priority:puuid-dup") == 1

    @pytest.mark.asyncio
    async def test_set_priority__different_puuids__creates_separate_keys(self, r):
        """set_priority for distinct PUUIDs creates one key each."""
        await set_priority(r, "puuid-a")
        await set_priority(r, "puuid-b")
        await set_priority(r, "puuid-c")
        assert await r.exists("player:priority:puuid-a") == 1
        assert await r.exists("player:priority:puuid-b") == 1
        assert await r.exists("player:priority:puuid-c") == 1


class TestHasPriorityPlayers:
    @pytest.mark.asyncio
    async def test_has_priority_players__no_keys__returns_false(self, r):
        """With no player:priority:* keys, returns False."""
        result = await has_priority_players(r)
        assert result is False

    @pytest.mark.asyncio
    async def test_has_priority_players__one_key__returns_true(self, r):
        """With one player:priority:* key, returns True."""
        await set_priority(r, "puuid-abc")
        result = await has_priority_players(r)
        assert result is True

    @pytest.mark.asyncio
    async def test_has_priority_players__multiple_keys__returns_true(self, r):
        """With multiple player:priority:* keys, returns True."""
        await set_priority(r, "puuid-1")
        await set_priority(r, "puuid-2")
        await set_priority(r, "puuid-3")
        result = await has_priority_players(r)
        assert result is True

    @pytest.mark.asyncio
    async def test_has_priority_players__after_clear__returns_false(self, r):
        """After clearing the only priority key, returns False."""
        await set_priority(r, "puuid-abc")
        assert await has_priority_players(r) is True
        await clear_priority(r, "puuid-abc")
        assert await has_priority_players(r) is False

    @pytest.mark.asyncio
    async def test_has_priority_players__partial_clear__returns_true(self, r):
        """After clearing one of two priority keys, still returns True."""
        await set_priority(r, "puuid-a")
        await set_priority(r, "puuid-b")
        await clear_priority(r, "puuid-a")
        assert await has_priority_players(r) is True

    @pytest.mark.asyncio
    async def test_has_priority_players__ignores_stale_counter(self, r):
        """A stale system:priority_count key does not affect SCAN-based detection."""
        await r.set("system:priority_count", "99")
        # No actual priority keys exist
        result = await has_priority_players(r)
        assert result is False

    @pytest.mark.asyncio
    async def test_has_priority_players__ignores_non_priority_keys(self, r):
        """Keys like player:{puuid} do not match player:priority:* pattern."""
        await r.set("player:puuid-abc", "data")
        await r.set("player:stats:puuid-abc", "data")
        result = await has_priority_players(r)
        assert result is False


class TestPriorityKeyTTL:
    """B12: player:priority:{puuid} has a TTL to prevent orphaned keys blocking Discovery."""

    @pytest.mark.asyncio
    async def test_set_priority__key_has_ttl(self, r):
        """set_priority creates key WITH TTL — prevents orphaned keys (B12)."""
        await set_priority(r, "puuid-abc")

        val = await r.get("player:priority:puuid-abc")
        assert val == "1"
        ttl = await r.ttl("player:priority:puuid-abc")
        assert 0 < ttl <= 86400  # default 24h TTL


class TestPriorityKeyTTLValue:
    """B12: TTL can be customised and defaults to 24h."""

    @pytest.mark.asyncio
    async def test_set_priority__default_ttl_is_86400(self, r):
        """Default TTL is 86400 seconds (24 hours)."""
        await set_priority(r, "puuid-ttl")
        ttl = await r.ttl("player:priority:puuid-ttl")
        # Allow some tolerance for test execution time
        assert 86390 <= ttl <= 86400

    @pytest.mark.asyncio
    async def test_set_priority__custom_ttl(self, r):
        """Custom TTL is applied when passed explicitly."""
        await set_priority(r, "puuid-custom", ttl=3600)
        ttl = await r.ttl("player:priority:puuid-custom")
        assert 3590 <= ttl <= 3600

    @pytest.mark.asyncio
    async def test_set_priority__duplicate_does_not_reset_ttl(self, r):
        """Calling set_priority twice with same PUUID does not reset the TTL."""
        await set_priority(r, "puuid-dup-ttl", ttl=100)
        # First call sets TTL around 100
        ttl1 = await r.ttl("player:priority:puuid-dup-ttl")
        assert 90 <= ttl1 <= 100
        # Second call (SET NX fails) — TTL stays
        await set_priority(r, "puuid-dup-ttl", ttl=5000)
        ttl2 = await r.ttl("player:priority:puuid-dup-ttl")
        # Should still be close to original TTL, NOT 5000
        assert ttl2 <= 100
