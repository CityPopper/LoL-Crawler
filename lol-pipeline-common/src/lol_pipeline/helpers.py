"""Shared helpers — DRY utilities used by multiple services."""

from __future__ import annotations

import redis.asyncio as aioredis


def name_cache_key(game_name: str, tag_line: str) -> str:
    """Build the Redis key for the player name→PUUID cache.

    Used by seed, admin, and UI services.
    """
    return f"player:name:{game_name.lower()}#{tag_line.lower()}"


async def is_system_halted(r: aioredis.Redis) -> bool:
    """Return True if the global halt flag is set.

    Used by crawler, fetcher, parser, analyzer handlers as a pre-check.
    """
    return bool(await r.get("system:halted"))
