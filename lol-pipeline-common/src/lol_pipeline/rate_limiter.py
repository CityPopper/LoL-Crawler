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

from lol_pipeline._rate_limiter_data import (
    _LONG_LIMIT,
    _LONG_WINDOW_MS,
    _SHORT_LIMIT,
    _SHORT_WINDOW_MS,
)
from lol_pipeline._rate_limiter_data import (
    _LUA_RATE_LIMIT_SCRIPT as _LUA_RATE_LIMIT_SCRIPT,
)


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
