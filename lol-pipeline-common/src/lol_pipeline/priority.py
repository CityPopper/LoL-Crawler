"""Priority queue helpers for player priority management.

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
PRIORITY_ACTIVE_SET = "priority:active"

PRIORITY_KEY_TTL_SECONDS = int(os.getenv("PRIORITY_KEY_TTL", "86400"))
# TTL for the priority:active SET itself — slightly longer than individual keys
# so the SET expires after all member keys have expired naturally.
PRIORITY_ACTIVE_SET_TTL_SECONDS = PRIORITY_KEY_TTL_SECONDS + 3600  # +1h buffer

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
    "high": 4,  # backwards compat → same as manual_20
    "normal": 1,  # backwards compat → same as auto_new
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
    async with r.pipeline(transaction=False) as pipe:
        pipe.set(f"{_PRIORITY_KEY_PREFIX}{puuid}", "1", nx=True, ex=ttl)
        pipe.sadd(PRIORITY_ACTIVE_SET, puuid)
        pipe.expire(PRIORITY_ACTIVE_SET, PRIORITY_ACTIVE_SET_TTL_SECONDS)
        await pipe.execute()


async def clear_priority(r: aioredis.Redis, puuid: str) -> None:
    """DEL player:priority:{puuid} and remove from priority:active SET."""
    async with r.pipeline(transaction=False) as pipe:
        pipe.delete(f"{_PRIORITY_KEY_PREFIX}{puuid}")
        pipe.srem(PRIORITY_ACTIVE_SET, puuid)
        await pipe.execute()


async def has_priority_players(r: aioredis.Redis) -> bool:
    """Return True if any player has active priority.

    Uses SCARD on the priority:active SET for an initial O(1) check, then
    spot-checks one random member to verify its ``player:priority:{puuid}``
    key still exists.  If the sampled key has expired (orphan), the member is
    removed and SCARD is re-checked.  This prevents orphaned SET entries from
    permanently blocking Discovery.
    """
    count: int = await r.scard(PRIORITY_ACTIVE_SET)  # type: ignore[misc]
    if count == 0:
        return False
    # Spot-check: verify at least one member is still live
    members: list[str] = await r.srandmember(PRIORITY_ACTIVE_SET, 1)  # type: ignore[misc]
    if members:
        exists: int = await r.exists(f"{_PRIORITY_KEY_PREFIX}{members[0]}")
        if not exists:
            await r.srem(PRIORITY_ACTIVE_SET, members[0])  # type: ignore[misc]
            # Re-check after cleanup
            return await r.scard(PRIORITY_ACTIVE_SET) > 0  # type: ignore[misc,no-any-return]
    return True
