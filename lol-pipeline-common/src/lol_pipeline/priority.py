"""Priority queue helpers — atomic Lua scripts for player priority management."""

from __future__ import annotations

import redis.asyncio as aioredis

_PRIORITY_KEY_PREFIX = "player:priority:"
_COUNTER_KEY = "system:priority_count"
_TTL_SECONDS = 86400  # 24 hours

_SET_INCR_LUA = """
redis.call("set", KEYS[1], ARGV[1], "EX", ARGV[2])
redis.call("incr", KEYS[2])
return redis.call("get", KEYS[2])
"""

_DEL_DECR_LUA = """
if redis.call("del", KEYS[1]) == 1 then
    redis.call("decr", KEYS[2])
end
return redis.call("get", KEYS[2])
"""


async def set_priority(r: aioredis.Redis, puuid: str) -> int:
    """Atomically SET player:priority:{puuid} with TTL and INCR system:priority_count."""
    result = await r.eval(
        _SET_INCR_LUA,
        2,
        f"{_PRIORITY_KEY_PREFIX}{puuid}",
        _COUNTER_KEY,
        "high",
        _TTL_SECONDS,
    )
    return int(result)


async def clear_priority(r: aioredis.Redis, puuid: str) -> int:
    """Atomically DEL player:priority:{puuid} and DECR system:priority_count."""
    result = await r.eval(
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
