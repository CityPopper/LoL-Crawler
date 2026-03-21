"""Priority queue helpers — SCAN-based detection for player priority management.

4-tier priority system (highest to lowest):
  manual_20     — manually seeded player, first 20 matches
  manual_20plus — manually seeded player, matches beyond 20
  auto_20       — auto-discovered player, first 20 matches
  auto_new      — auto-discovered new player
"""

from __future__ import annotations

import os

import redis.asyncio as aioredis

_PRIORITY_KEY_PREFIX = "player:priority:"

PRIORITY_KEY_TTL_SECONDS = int(os.getenv("PRIORITY_KEY_TTL", "86400"))

# 4-tier priority constants
PRIORITY_MANUAL_20: str = "manual_20"
PRIORITY_MANUAL_20PLUS: str = "manual_20plus"
PRIORITY_AUTO_20: str = "auto_20"
PRIORITY_AUTO_NEW: str = "auto_new"

# Numeric ordering for comparison (higher = more urgent).
# Includes backwards-compat aliases for legacy "high" / "normal" values.
PRIORITY_ORDER: dict[str, int] = {
    PRIORITY_MANUAL_20: 4,
    PRIORITY_MANUAL_20PLUS: 3,
    PRIORITY_AUTO_20: 2,
    PRIORITY_AUTO_NEW: 1,
    "high": 4,      # backwards compat → same as manual_20
    "normal": 1,     # backwards compat → same as auto_new
}

# Threshold: after this many match IDs published, priority downgrades.
PRIORITY_DOWNGRADE_THRESHOLD: int = 20

# Mapping from tier to its downgraded tier (after 20 matches published).
_DOWNGRADE_MAP: dict[str, str] = {
    PRIORITY_MANUAL_20: PRIORITY_MANUAL_20PLUS,
    PRIORITY_AUTO_20: PRIORITY_AUTO_NEW,
}


def downgrade_priority(current: str) -> str:
    """Return the downgraded priority tier, or *current* if no downgrade applies."""
    return _DOWNGRADE_MAP.get(current, current)


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
