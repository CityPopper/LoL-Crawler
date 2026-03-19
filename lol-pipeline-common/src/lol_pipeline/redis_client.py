"""Async Redis connection pool factory and health check."""

from __future__ import annotations

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


def get_redis(url: str) -> aioredis.Redis:
    """Create a decode_responses=True Redis client from a URL."""
    return aioredis.from_url(url, decode_responses=True)


async def health_check(r: aioredis.Redis) -> bool:
    """Return True if Redis responds to PING."""
    try:
        return bool(await r.ping())  # type: ignore[misc]
    except (RedisConnectionError, RedisTimeoutError, OSError):
        return False
