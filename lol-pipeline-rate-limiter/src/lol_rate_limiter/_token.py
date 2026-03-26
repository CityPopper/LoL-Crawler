"""Token acquire logic using the dual-window Lua script."""

from __future__ import annotations

import time
import uuid
from typing import Any

import redis.asyncio as aioredis

from lol_rate_limiter._lua import LUA_RATE_LIMIT_SCRIPT
from lol_rate_limiter.config import Config


async def acquire_token(
    r: aioredis.Redis,
    cfg: Config,
    key_prefix: str = "ratelimit",
) -> tuple[bool, int | None]:
    """Try to acquire a rate-limit token.

    Returns (granted, retry_after_ms).
    - granted=True, retry_after_ms=None when a token is acquired.
    - granted=False, retry_after_ms=N when denied.
    """
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
        cfg.short_limit,
        cfg.long_limit,
        cfg.short_window_ms,
        cfg.long_window_ms,
        uid,
    )
    result_int = int(result)
    if result_int == 1:
        return True, None
    return False, abs(result_int)
