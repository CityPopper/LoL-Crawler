"""Unit tests for lol_pipeline.redis_client."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.redis_client import get_redis, health_check


class TestGetRedis:
    def test_returns_redis_instance(self):
        r = get_redis("redis://localhost:6379")
        assert r is not None

    def test_decode_responses_enabled(self):
        r = get_redis("redis://localhost:6379")
        pool = r.connection_pool
        kwargs = pool.connection_kwargs
        assert kwargs.get("decode_responses") is True


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_redis(self):
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        assert await health_check(r) is True
        await r.aclose()
