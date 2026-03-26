"""Priority queue helpers for player priority management.

4-tier priority system (highest to lowest):
  manual_20     — manually seeded player, first 20 matches
  manual_20plus — manually seeded player, matches beyond 20
  auto_20       — auto-discovered player, first 20 matches
  auto_new      — auto-discovered new player
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from lol_pipeline.config import Config

_log = logging.getLogger(__name__)

_PRIORITY_KEY_PREFIX = "player:priority:"
PRIORITY_ACTIVE_SET = "priority:active"


def _ttl_from_config(default: int = 86400) -> int:
    """Read priority_key_ttl_seconds from Config, falling back to *default*."""
    try:
        return Config().priority_key_ttl_seconds
    except Exception:
        _log.debug("Config() unavailable — using default priority TTL %d", default)
        return default


PRIORITY_KEY_TTL_SECONDS: int = _ttl_from_config()
# TTL for the priority:active SET itself — slightly longer than individual keys
# so the SET expires after all member keys have expired naturally.
PRIORITY_ACTIVE_SET_TTL_SECONDS: int = PRIORITY_KEY_TTL_SECONDS + 3600  # +1h buffer

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

    Fetches all members of the priority:active SET, checks each member's
    ``player:priority:{puuid}`` key via a pipeline, and batch-removes any
    orphans (expired keys) in one SREM call.  Returns True if live members
    remain after cleanup.
    """
    try:
        members: set[str] = await r.smembers(PRIORITY_ACTIVE_SET)  # type: ignore[misc]
        if not members:
            return False

        member_list = list(members)
        # Pipeline EXISTS for each member's priority key
        async with r.pipeline(transaction=False) as pipe:
            for puuid in member_list:
                pipe.exists(f"{_PRIORITY_KEY_PREFIX}{puuid}")
            results: list[int] = await pipe.execute()

        # Collect orphans (members whose priority key no longer exists)
        dead: list[str] = [
            puuid for puuid, exists in zip(member_list, results) if not exists
        ]

        if dead:
            await r.srem(PRIORITY_ACTIVE_SET, *dead)  # type: ignore[misc]

        return len(member_list) - len(dead) > 0
    except Exception:
        _log.warning("has_priority_players: Redis error, returning True conservatively")
        return True
