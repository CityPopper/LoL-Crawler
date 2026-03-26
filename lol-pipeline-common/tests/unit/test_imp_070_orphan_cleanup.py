"""IMP-070: has_priority_players cleans all orphans in a single call."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.priority import (
    PRIORITY_ACTIVE_SET,
    has_priority_players,
    set_priority,
)


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestBatchOrphanCleanup:
    async def test_100_orphans_cleared_in_single_call(self, r):
        """All 100 orphaned entries are cleaned in a single call."""
        # Add 100 orphans to the active SET (no corresponding priority keys)
        orphans = [f"orphan-{i}" for i in range(100)]
        await r.sadd(PRIORITY_ACTIVE_SET, *orphans)
        assert await r.scard(PRIORITY_ACTIVE_SET) == 100

        result = await has_priority_players(r)
        assert result is False
        # All orphans should be removed in one call
        assert await r.scard(PRIORITY_ACTIVE_SET) == 0

    async def test_mixed_orphans_and_live(self, r):
        """Only orphans are removed; live entries remain."""
        # Set up 5 live players
        for i in range(5):
            await set_priority(r, f"live-{i}")
        # Add 50 orphans
        orphans = [f"orphan-{i}" for i in range(50)]
        await r.sadd(PRIORITY_ACTIVE_SET, *orphans)
        assert await r.scard(PRIORITY_ACTIVE_SET) == 55

        result = await has_priority_players(r)
        assert result is True
        # Only the 5 live entries should remain
        assert await r.scard(PRIORITY_ACTIVE_SET) == 5

    async def test_no_orphans_returns_true(self, r):
        """When all entries are live, returns True and removes nothing."""
        await set_priority(r, "live-a")
        await set_priority(r, "live-b")

        result = await has_priority_players(r)
        assert result is True
        assert await r.scard(PRIORITY_ACTIVE_SET) == 2

    async def test_empty_set_returns_false(self, r):
        """Empty SET returns False immediately."""
        result = await has_priority_players(r)
        assert result is False
