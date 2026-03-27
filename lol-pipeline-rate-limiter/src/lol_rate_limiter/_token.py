"""Token acquire logic using the dual-window Lua script."""

from __future__ import annotations

import math
import time
import uuid
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis

from lol_rate_limiter._lua import LUA_RATE_LIMIT_METHOD_SCRIPT, LUA_RATE_LIMIT_SCRIPT
from lol_rate_limiter.config import Config

if TYPE_CHECKING:
    from lol_rate_limiter.config import Domain


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
        short_limit if short_limit is not None else cfg.short_limit,  # type: ignore[attr-defined]
        long_limit if long_limit is not None else cfg.long_limit,  # type: ignore[attr-defined]
        cfg.short_window_ms,  # type: ignore[attr-defined]
        cfg.long_window_ms,  # type: ignore[attr-defined]
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
    app_sl = cfg.short_limit  # type: ignore[attr-defined]
    app_ll = cfg.long_limit  # type: ignore[attr-defined]
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
        cfg.short_window_ms,  # type: ignore[attr-defined]
        cfg.long_window_ms,  # type: ignore[attr-defined]
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


# ---------------------------------------------------------------------------
# Domain-based token acquisition (RATE-DYN-2)
# ---------------------------------------------------------------------------


def _resolve_limits(
    domain: Domain,
    *,
    is_ui: bool,
) -> tuple[str, int, int]:
    """Resolve key_prefix, short_limit, long_limit for the domain.

    Returns (key_prefix, short_limit, long_limit).
    """
    base = f"ratelimit:{domain.name}"

    if domain.ui_pct == 0.0:
        return base, domain.short_limit, domain.long_limit

    ui_long = math.floor(domain.long_limit * domain.ui_pct)
    if is_ui:
        ui_short = max(1, math.floor(domain.short_limit * domain.ui_pct))
        return f"{base}:ui", ui_short, ui_long

    pipeline_long = domain.long_limit - ui_long
    return f"{base}:pipeline", domain.short_limit, pipeline_long


async def acquire_token_for_domain(
    r: aioredis.Redis,
    domain: Domain,
    endpoint: str = "",
    *,
    is_ui: bool = False,
) -> tuple[bool, int | None]:
    """Acquire a rate-limit token for the given domain.

    1. Check domain-level cooling-off key.
    2. Resolve effective limits and key prefix based on ``ui_pct`` / ``is_ui``.
    3. Dispatch to the appropriate Lua script (4-key or 8-key).
    4. Return ``(True, None)`` on grant, ``(False, retry_after_ms)`` on denial.
    """
    # 1. Cooling-off check (domain-level, not per sub-bucket)
    cooling_key = f"ratelimit:{domain.name}:cooling_off"
    cooling_ttl = await r.pttl(cooling_key)
    if cooling_ttl > 0:
        return False, cooling_ttl

    # 2. Resolve effective limits
    key_prefix, short_limit, long_limit = _resolve_limits(domain, is_ui=is_ui)

    now_ms = int(time.time() * 1000)
    uid = str(uuid.uuid4())

    # 3. Lua script dispatch
    if domain.has_method_limits and endpoint:
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
            short_limit,
            long_limit,
            domain.short_window_ms,
            domain.long_window_ms,
            uid,
            short_limit,
            long_limit,
        )
    else:
        result = await r.eval(  # type: ignore[misc]
            LUA_RATE_LIMIT_SCRIPT,
            4,
            f"{key_prefix}:short",
            f"{key_prefix}:long",
            f"{key_prefix}:limits:short",
            f"{key_prefix}:limits:long",
            now_ms,
            short_limit,
            long_limit,
            domain.short_window_ms,
            domain.long_window_ms,
            uid,
        )

    result_int = int(result)

    # 5. RPM counter on grant
    if result_int == 1:
        minute_bucket = now_ms // 60_000
        counter_key = f"{key_prefix}:rpm:{minute_bucket}"
        async with r.pipeline(transaction=False) as pipe:
            pipe.incr(counter_key)
            pipe.expire(counter_key, 7200)  # 2-hour TTL
            await pipe.execute()
        return True, None

    # 6. Denial
    return False, abs(result_int)
