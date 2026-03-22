"""Dual-window Lua sliding window rate limiter for Riot API.

Limits are read dynamically from Redis keys written by RiotClient after each
successful API response (X-App-Rate-Limit header). Falls back to config/default
values when no stored limits exist (e.g. before the first successful response).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import redis.asyncio as aioredis

# Atomic Lua script: checks both windows before admitting a token.
# KEYS[1] = short-window ZSET key, KEYS[2] = long-window ZSET key
# KEYS[3] = stored short limit key, KEYS[4] = stored long limit key
# ARGV[1] = now_ms, ARGV[2] = short limit fallback, ARGV[3] = long limit fallback,
# ARGV[4] = short window ms, ARGV[5] = long window ms, ARGV[6] = unique member ID
#
# Stored limit keys (KEYS[3]/KEYS[4]) are written by RiotClient after each
# successful API response. If present they override the ARGV fallback values so
# the limiter automatically adapts to the real API key limits.
# All key access uses the KEYS array for Redis Cluster compatibility (no CROSSSLOT).
_LUA_RATE_LIMIT_SCRIPT = """
local key_s = KEYS[1]
local key_l = KEYS[2]
local now     = tonumber(ARGV[1])
local win_s   = tonumber(ARGV[4])
local win_l   = tonumber(ARGV[5])
local uid     = ARGV[6]

local stored_s = redis.call("GET", KEYS[3])
local stored_l = redis.call("GET", KEYS[4])
local limit_s = (stored_s and tonumber(stored_s)) or tonumber(ARGV[2])
local limit_l = (stored_l and tonumber(stored_l)) or tonumber(ARGV[3])
if limit_s < 1 then limit_s = tonumber(ARGV[2]) end
if limit_l < 1 then limit_l = tonumber(ARGV[3]) end

redis.call("ZREMRANGEBYSCORE", key_s, "-inf", now - win_s)
redis.call("ZREMRANGEBYSCORE", key_l, "-inf", now - win_l)

local count_s = redis.call("ZCARD", key_s)
local count_l = redis.call("ZCARD", key_l)

if count_s >= limit_s or count_l >= limit_l then
    -- Return negative wait hint: ms until the earliest slot opens.
    local wait_s = 0
    local wait_l = 0
    if count_s >= limit_s then
        local oldest_s = redis.call("ZRANGE", key_s, 0, 0, "WITHSCORES")
        if #oldest_s >= 2 then
            wait_s = tonumber(oldest_s[2]) + win_s - now
        end
    end
    if count_l >= limit_l then
        local oldest_l = redis.call("ZRANGE", key_l, 0, 0, "WITHSCORES")
        if #oldest_l >= 2 then
            wait_l = tonumber(oldest_l[2]) + win_l - now
        end
    end
    local wait = math.max(wait_s, wait_l, 1)
    return -wait  -- negative signals denial; absolute value = ms to wait
end

redis.call("ZADD", key_s, now, uid)
redis.call("ZADD", key_l, now, uid)
redis.call("PEXPIRE", key_s, win_s)
redis.call("PEXPIRE", key_l, win_l)
return 1
"""

_SHORT_WINDOW_MS = 1_000  # 1 second
_SHORT_LIMIT = 20  # 20 req/s
_LONG_WINDOW_MS = 120_000  # 2 minutes
_LONG_LIMIT = 100  # 100 req/2 min


async def acquire_token(
    r: aioredis.Redis,
    key_prefix: str = "ratelimit",
    limit_per_second: int = _SHORT_LIMIT,
) -> int:
    """Try to acquire a rate limit token.

    Returns 1 on success.  On denial, returns a negative value whose absolute
    value is the estimated wait time in milliseconds until a slot opens.

    ``limit_per_second`` controls the 1-second sliding window cap (default: 20).
    The 2-minute window cap is fixed at Riot's hard limit of 100 req/2 min.
    """
    now_ms = int(time.time() * 1000)
    uid = str(uuid.uuid4())
    result: Any = await r.eval(  # type: ignore[misc]
        _LUA_RATE_LIMIT_SCRIPT,
        4,
        f"{key_prefix}:short",
        f"{key_prefix}:long",
        "ratelimit:limits:short",
        "ratelimit:limits:long",
        now_ms,
        limit_per_second,
        _LONG_LIMIT,
        _SHORT_WINDOW_MS,
        _LONG_WINDOW_MS,
        uid,
    )
    return int(result)


async def wait_for_token(
    r: aioredis.Redis,
    key_prefix: str = "ratelimit",
    limit_per_second: int = _SHORT_LIMIT,
    max_wait_s: float = 60.0,
    region: str = "",  # kept for API compat, not used
) -> None:
    """Block until a rate limit token is acquired.

    Uses the wait hint returned by the Lua script to sleep precisely until
    the next slot opens, instead of polling at a fixed interval.  Adds
    jitter (10-50% of wait time) to prevent thundering herd.

    Riot API rate limits are global (not per-region), so the *region*
    parameter is accepted for API compatibility but ignored.  All callers
    share a single ``ratelimit`` sliding window.

    When a ``ratelimit:throttle`` key exists (set by RiotClient when API
    capacity drops below 5%), adds a 200ms sleep to proactively slow down.

    Raises ``TimeoutError`` if *max_wait_s* seconds elapse without acquiring a token.
    """
    import random

    # Proactive throttle: slow down when RiotClient signals near-capacity
    throttled: str | None = await r.get("ratelimit:throttle")
    if throttled:
        await asyncio.sleep(0.2)
    deadline = time.monotonic() + max_wait_s
    while True:
        result = await acquire_token(r, key_prefix, limit_per_second)
        if result == 1:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Rate limiter wait exceeded {max_wait_s}s")
        # result is negative: abs(result) = ms until next slot opens
        wait_ms = max(abs(result), 10)
        # Add 10-50% jitter to prevent thundering herd
        jitter = wait_ms * random.uniform(0.1, 0.5)  # noqa: S311
        sleep_s = min((wait_ms + jitter) / 1000.0, deadline - time.monotonic())
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)
