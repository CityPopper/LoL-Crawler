"""Priority queue helpers — SCAN-based detection for player priority management."""

from __future__ import annotations

import os

import redis.asyncio as aioredis

_PRIORITY_KEY_PREFIX = "player:priority:"

PRIORITY_KEY_TTL_SECONDS = int(os.getenv("PRIORITY_KEY_TTL", "86400"))


async def set_priority(
    r: aioredis.Redis,
    puuid: str,
    ttl: int = PRIORITY_KEY_TTL_SECONDS,
) -> None:
    """SET player:priority:{puuid} with NX + TTL. No-op if key already exists."""
    await r.set(f"{_PRIORITY_KEY_PREFIX}{puuid}", "1", nx=True, ex=ttl)


async def clear_priority(r: aioredis.Redis, puuid: str) -> None:
    """DEL player:priority:{puuid}."""
    await r.delete(f"{_PRIORITY_KEY_PREFIX}{puuid}")


async def has_priority_players(r: aioredis.Redis) -> bool:
    """Return True if any player:priority:* keys exist in Redis.

    Uses SCAN instead of a counter to avoid TTL-expiry drift.
    Iterates until a match is found or the full keyspace is scanned.
    """
    cursor: int = 0
    while True:
        cursor, keys = await r.scan(cursor=cursor, match="player:priority:*", count=100)
        if keys:
            return True
        if cursor == 0:
            return False
