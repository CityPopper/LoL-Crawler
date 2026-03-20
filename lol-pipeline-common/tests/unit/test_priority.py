"""Unit tests for lol_pipeline.priority — atomic Lua scripts for priority management."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.priority import clear_priority, priority_count, set_priority

try:
    import lupa  # noqa: F401

    _LUPA_AVAILABLE = True
except ImportError:
    _LUPA_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _LUPA_AVAILABLE, reason="lupa required for Lua script evaluation"
)


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestSetPriority:
    @pytest.mark.asyncio
    async def test_set_priority__creates_key_without_ttl(self, r):
        """set_priority creates player:priority:{puuid} without a TTL."""
        await set_priority(r, "puuid-abc")

        val = await r.get("player:priority:puuid-abc")
        assert val == "high"
        ttl = await r.ttl("player:priority:puuid-abc")
        assert ttl == -1  # no expiry — TTL removed to prevent counter drift

    @pytest.mark.asyncio
    async def test_set_priority__increments_counter(self, r):
        """Each set_priority call increments system:priority_count."""
        count1 = await set_priority(r, "puuid-1")
        assert count1 == 1

        count2 = await set_priority(r, "puuid-2")
        assert count2 == 2


class TestClearPriority:
    @pytest.mark.asyncio
    async def test_clear_priority__deletes_key_and_decrements(self, r):
        """clear_priority deletes the key and decrements the counter."""
        await set_priority(r, "puuid-abc")
        assert await r.get("player:priority:puuid-abc") == "high"

        count = await clear_priority(r, "puuid-abc")
        assert count == 0
        assert await r.get("player:priority:puuid-abc") is None

    @pytest.mark.asyncio
    async def test_clear_priority__key_not_exists__no_decrement(self, r):
        """Clearing a non-existent key does not decrement the counter."""
        await set_priority(r, "puuid-other")
        assert await priority_count(r) == 1

        count = await clear_priority(r, "puuid-nonexistent")
        # Counter should remain at 1 — no decrement since key didn't exist
        assert count == 1

    @pytest.mark.asyncio
    async def test_clear_priority__double_clear__no_underflow(self, r):
        """Clearing the same key twice does not underflow the counter."""
        await set_priority(r, "puuid-abc")
        await clear_priority(r, "puuid-abc")
        count = await clear_priority(r, "puuid-abc")
        assert count == 0  # no underflow below 0


class TestSetPriorityIdempotency:
    @pytest.mark.asyncio
    async def test_set_priority__same_puuid_twice__increments_counter_once(self, r):
        """Calling set_priority twice for the same PUUID only increments counter once."""
        count1 = await set_priority(r, "puuid-dup")
        assert count1 == 1

        count2 = await set_priority(r, "puuid-dup")
        assert count2 == 1  # still 1, not 2

    @pytest.mark.asyncio
    async def test_set_priority__same_puuid_twice_then_clear__counter_returns_to_zero(self, r):
        """After duplicate set + clear, counter must be 0 (no drift)."""
        await set_priority(r, "puuid-dup")
        await set_priority(r, "puuid-dup")
        count = await clear_priority(r, "puuid-dup")
        assert count == 0  # no permanent drift

    @pytest.mark.asyncio
    async def test_set_priority__different_puuids__increments_counter_for_each(self, r):
        """set_priority for distinct PUUIDs increments the counter once per PUUID."""
        count1 = await set_priority(r, "puuid-a")
        assert count1 == 1

        count2 = await set_priority(r, "puuid-b")
        assert count2 == 2

        count3 = await set_priority(r, "puuid-c")
        assert count3 == 3


class TestPriorityCounterFloor:
    """R2: _DEL_DECR_LUA must never let system:priority_count go negative."""

    @pytest.mark.asyncio
    async def test_clear_priority__counter_at_zero_key_exists__no_negative(self, r):
        """Counter manually set to 0 but priority key exists — DECR must be skipped."""
        # Simulate drift: key exists but counter was externally reset to 0
        await r.set("player:priority:puuid-x", "high")
        await r.set("system:priority_count", "0")

        count = await clear_priority(r, "puuid-x")
        assert count == 0  # must NOT be -1


class TestNoTTLOnPriorityKeys:
    """R1: player:priority:{puuid} must NOT have a TTL to prevent counter drift."""

    @pytest.mark.asyncio
    async def test_set_priority__key_has_no_ttl(self, r):
        """set_priority creates key WITHOUT TTL — expiry caused counter drift."""
        await set_priority(r, "puuid-abc")

        val = await r.get("player:priority:puuid-abc")
        assert val == "high"
        ttl = await r.ttl("player:priority:puuid-abc")
        assert ttl == -1  # no expiry


class TestPriorityCount:
    @pytest.mark.asyncio
    async def test_priority_count__returns_zero_when_unset(self, r):
        """priority_count returns 0 when system:priority_count does not exist."""
        assert await priority_count(r) == 0

    @pytest.mark.asyncio
    async def test_priority_count__returns_current_value(self, r):
        """priority_count returns the current counter value."""
        await set_priority(r, "puuid-1")
        await set_priority(r, "puuid-2")
        assert await priority_count(r) == 2
