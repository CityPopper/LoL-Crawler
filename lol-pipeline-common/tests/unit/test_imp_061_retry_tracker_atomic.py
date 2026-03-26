"""IMP-061: RetryTracker.incr uses MULTI/EXEC for atomic INCR + EXPIRE."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.service import RetryTracker, _RETRY_KEY_TTL


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestRetryTrackerAtomic:
    async def test_incr_sets_ttl_atomically(self, r):
        """INCR and EXPIRE happen within a MULTI/EXEC transaction."""
        tracker = RetryTracker(ttl=300)
        count = await tracker.incr(r, "stream:test", "msg-1")
        assert count == 1
        key = tracker.key("stream:test", "msg-1")
        ttl = await r.ttl(key)
        assert 0 < ttl <= 300

    async def test_transaction_flag_is_true(self):
        """RetryTracker.incr uses transaction=True in its pipeline."""
        import inspect

        source = inspect.getsource(RetryTracker.incr)
        assert "transaction=True" in source, (
            "RetryTracker.incr must use transaction=True for atomic INCR+EXPIRE"
        )

    async def test_incr_returns_correct_count(self, r):
        """Successive calls return incrementing counts."""
        tracker = RetryTracker(ttl=300)
        assert await tracker.incr(r, "stream:test", "msg-2") == 1
        assert await tracker.incr(r, "stream:test", "msg-2") == 2
        assert await tracker.incr(r, "stream:test", "msg-2") == 3

    async def test_incr_refreshes_ttl_on_each_call(self, r):
        """Each incr call refreshes the TTL (not just the first)."""
        tracker = RetryTracker(ttl=500)
        await tracker.incr(r, "stream:test", "msg-3")
        key = tracker.key("stream:test", "msg-3")
        ttl1 = await r.ttl(key)
        await tracker.incr(r, "stream:test", "msg-3")
        ttl2 = await r.ttl(key)
        # TTL should be refreshed (close to 500 again)
        assert ttl2 >= ttl1
