from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest


@pytest.fixture
async def r() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()
