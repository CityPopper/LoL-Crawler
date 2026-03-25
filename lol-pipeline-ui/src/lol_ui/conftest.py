from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import fakeredis.aioredis
import pytest

from lol_ui.ddragon import _mem_cache


@pytest.fixture(autouse=True)
def _clear_ddragon_mem_cache() -> Iterator[None]:
    """Clear the ddragon module-level in-memory cache before each test."""
    _mem_cache.clear()
    yield
    _mem_cache.clear()


@pytest.fixture
async def r() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()
