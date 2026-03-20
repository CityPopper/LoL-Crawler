"""Priority queue helpers — atomic Lua scripts for player priority management."""

from __future__ import annotations

import redis.asyncio as aioredis

_PRIORITY_KEY_PREFIX = "player:priority:"
_COUNTER_KEY = "system:priority_count"

_SET_INCR_LUA = """
local created = redis.call("SET", KEYS[1], ARGV[1], "NX")
if created then
    redis.call("INCR", KEYS[2])
end
return redis.call("GET", KEYS[2])
"""

_DEL_DECR_LUA = """
if redis.call("DEL", KEYS[1]) == 1 then
    if tonumber(redis.call("GET", KEYS[2]) or "0") > 0 then
        redis.call("DECR", KEYS[2])
    end
end
return redis.call("GET", KEYS[2])
"""


async def set_priority(r: aioredis.Redis, puuid: str) -> int:
    """Atomically SET player:priority:{puuid} (no TTL) and INCR system:priority_count."""
    result = await r.eval(  # type: ignore[misc]
        _SET_INCR_LUA,
        2,
        f"{_PRIORITY_KEY_PREFIX}{puuid}",
        _COUNTER_KEY,
        "high",
    )
    return int(result)


async def clear_priority(r: aioredis.Redis, puuid: str) -> int:
    """Atomically DEL player:priority:{puuid} and DECR system:priority_count."""
    result = await r.eval(  # type: ignore[misc]
        _DEL_DECR_LUA,
        2,
        f"{_PRIORITY_KEY_PREFIX}{puuid}",
        _COUNTER_KEY,
    )
    return int(result or 0)


async def priority_count(r: aioredis.Redis) -> int:
    """Return current system:priority_count (0 if not set)."""
    val = await r.get(_COUNTER_KEY)
    return int(val) if val else 0
