"""IMP-071: RawStore.exists(redis_only=True) skips disk scan.

The coordinator's hot path should check Redis only, avoiding expensive
bundle scanning on every message.
"""

from __future__ import annotations

from unittest.mock import patch

import fakeredis.aioredis
import pytest

from lol_pipeline.raw_store import RawStore


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestExistsRedisOnlySkipsDiskScan:
    """exists(redis_only=True) never touches disk."""

    @pytest.mark.asyncio
    async def test_exists_redis_only_returns_true_when_in_redis(self, r):
        store = RawStore(r, data_dir="/tmp/fake")
        await r.set("raw:match:NA1_100", "data")

        result = await store.exists("NA1_100", redis_only=True)

        assert result is True

    @pytest.mark.asyncio
    async def test_exists_redis_only_returns_false_when_not_in_redis(self, r, tmp_path):
        """Even though the match exists on disk, redis_only=True returns False."""
        store = RawStore(r, data_dir=str(tmp_path))
        # Write to disk via a normal set, then delete from Redis
        await store.set("NA1_200", '{"data": 1}')
        await r.delete("raw:match:NA1_200")

        result = await store.exists("NA1_200", redis_only=True)

        assert result is False

    @pytest.mark.asyncio
    async def test_exists_redis_only_does_not_call_to_thread(self, r):
        """redis_only=True must not invoke asyncio.to_thread at all."""
        store = RawStore(r, data_dir="/tmp/fake")

        calls = []

        async def tracking_to_thread(fn, *args, **kwargs):
            calls.append(fn.__name__)

        with patch("lol_pipeline.raw_store.asyncio.to_thread", side_effect=tracking_to_thread):
            await store.exists("NA1_1234567890", redis_only=True)

        assert len(calls) == 0, f"Expected no to_thread calls, got: {calls}"

    @pytest.mark.asyncio
    async def test_exists_default_still_falls_back_to_disk(self, r, tmp_path):
        """Default exists() (redis_only=False) still checks disk."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_300", '{"data": 1}')
        await r.delete("raw:match:NA1_300")

        # Default behaviour should find it on disk
        result = await store.exists("NA1_300")

        assert result is True
