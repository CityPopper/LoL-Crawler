"""Async Redis connection pool factory and health check."""

from __future__ import annotations

import os

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

_REDIS_SOCKET_TIMEOUT = float(os.getenv("REDIS_SOCKET_TIMEOUT", "30"))
_REDIS_CONNECT_TIMEOUT = float(os.getenv("REDIS_CONNECT_TIMEOUT", "10"))


def get_redis(url: str) -> aioredis.Redis:
    """Create a decode_responses=True Redis client from a URL."""
    return aioredis.from_url(
        url,
        decode_responses=True,
        socket_timeout=_REDIS_SOCKET_TIMEOUT,
        socket_connect_timeout=_REDIS_CONNECT_TIMEOUT,
    )


async def health_check(r: aioredis.Redis) -> bool:
    """Return True if Redis responds to PING."""
    try:
        return bool(await r.ping())  # type: ignore[misc]
    except RedisConnectionError, RedisTimeoutError, OSError:
        return False
