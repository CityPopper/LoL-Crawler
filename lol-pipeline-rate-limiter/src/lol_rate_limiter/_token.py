"""Token acquire logic using the dual-window Lua script."""

from __future__ import annotations

import time
import uuid
from typing import Any

import redis.asyncio as aioredis

from lol_rate_limiter._lua import LUA_RATE_LIMIT_METHOD_SCRIPT, LUA_RATE_LIMIT_SCRIPT
from lol_rate_limiter.config import Config


async def acquire_token(
    r: aioredis.Redis,
    cfg: Config,
    key_prefix: str = "ratelimit",
    *,
    short_limit: int | None = None,
    long_limit: int | None = None,
) -> tuple[bool, int | None]:
    """Try to acquire a rate-limit token.

    Returns (granted, retry_after_ms).
    - granted=True, retry_after_ms=None when a token is acquired.
    - granted=False, retry_after_ms=N when denied.

    When *short_limit* or *long_limit* are provided they override the
    corresponding ``cfg`` values, allowing per-source budgets.
    """
    # Check cooling-off key (set when a real 429 was received from the upstream API)
    cooling_key = f"{key_prefix}:cooling_off"
    cooling_ttl = await r.pttl(cooling_key)
    if cooling_ttl > 0:
        return False, cooling_ttl

    now_ms = int(time.time() * 1000)
    uid = str(uuid.uuid4())
    result: Any = await r.eval(  # type: ignore[misc]
        LUA_RATE_LIMIT_SCRIPT,
        4,
        f"{key_prefix}:short",
        f"{key_prefix}:long",
        f"{key_prefix}:limits:short",
        f"{key_prefix}:limits:long",
        now_ms,
        short_limit if short_limit is not None else cfg.short_limit,
        long_limit if long_limit is not None else cfg.long_limit,
        cfg.short_window_ms,
        cfg.long_window_ms,
        uid,
    )
    result_int = int(result)
    if result_int == 1:
        minute_bucket = now_ms // 60_000
        counter_key = f"{key_prefix}:rpm:{minute_bucket}"
        async with r.pipeline(transaction=False) as pipe:
            pipe.incr(counter_key)
            pipe.expire(counter_key, 7200)  # 2-hour TTL
            await pipe.execute()
        return True, None
    return False, abs(result_int)


async def acquire_token_with_method(
    r: aioredis.Redis,
    cfg: Config,
    key_prefix: str,
    endpoint: str,
    *,
    method_short_limit: int | None = None,
    method_long_limit: int | None = None,
) -> tuple[bool, int | None]:
    """Acquire a token checking both app-level and per-endpoint buckets.

    Returns (granted, retry_after_ms) — same convention as ``acquire_token``.
    Both the app-level bucket and the method-level bucket must have capacity.
    App-level limits always come from ``cfg``; method-level limits default to
    the app-level values unless explicitly overridden.
    """
    # Check cooling-off key (set when a real 429 was received from the upstream API)
    cooling_key = f"{key_prefix}:cooling_off"
    cooling_ttl = await r.pttl(cooling_key)
    if cooling_ttl > 0:
        return False, cooling_ttl

    now_ms = int(time.time() * 1000)
    uid = str(uuid.uuid4())
    app_sl = cfg.short_limit
    app_ll = cfg.long_limit
    mth_sl = method_short_limit if method_short_limit is not None else app_sl
    mth_ll = method_long_limit if method_long_limit is not None else app_ll
    method_prefix = f"{key_prefix}:{endpoint}"

    result: Any = await r.eval(  # type: ignore[misc]
        LUA_RATE_LIMIT_METHOD_SCRIPT,
        8,
        f"{key_prefix}:short",
        f"{key_prefix}:long",
        f"{key_prefix}:limits:short",
        f"{key_prefix}:limits:long",
        f"{method_prefix}:short",
        f"{method_prefix}:long",
        f"{method_prefix}:limits:short",
        f"{method_prefix}:limits:long",
        now_ms,
        app_sl,
        app_ll,
        cfg.short_window_ms,
        cfg.long_window_ms,
        uid,
        mth_sl,
        mth_ll,
    )
    result_int = int(result)
    if result_int == 1:
        minute_bucket = now_ms // 60_000
        counter_key = f"{key_prefix}:rpm:{minute_bucket}"
        async with r.pipeline(transaction=False) as pipe:
            pipe.incr(counter_key)
            pipe.expire(counter_key, 7200)  # 2-hour TTL
            await pipe.execute()
        return True, None
    return False, abs(result_int)


async def set_cooling_off(r: aioredis.Redis, key_prefix: str, delay_ms: int) -> None:
    """Block all token grants for this source for delay_ms milliseconds."""
    await r.set(f"{key_prefix}:cooling_off", "1", px=max(delay_ms, 1000))
