"""Rate-limiter HTTP service — FastAPI app with startup lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lol_rate_limiter._headers import parse_rate_limit_header
from lol_rate_limiter._token import acquire_token
from lol_rate_limiter.config import Config

_log = logging.getLogger("rate_limiter")

# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = Config()
    app.state.cfg = cfg
    app.state.r = aioredis.from_url(cfg.redis_url, decode_responses=True)
    _log.info("Rate-limiter starting", extra={"redis_url": cfg.redis_url})
    yield
    await app.state.r.aclose()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="LoL Rate Limiter", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# IMP-048: Shared-secret authentication middleware
# ---------------------------------------------------------------------------

_AUTH_EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/status"})


@app.middleware("http")
async def _check_secret(request: Request, call_next: Any) -> Any:
    """Reject unauthenticated requests to mutable endpoints.

    When ``RATE_LIMITER_SECRET`` is set (non-empty), all requests except
    ``/health`` and ``/status`` must include a matching
    ``X-Rate-Limiter-Secret`` header. Returns 403 on mismatch.
    When the secret is empty, auth is disabled (dev/test convenience).
    """
    cfg: Config = app.state.cfg
    if cfg.secret and request.url.path not in _AUTH_EXEMPT_PATHS:
        provided = request.headers.get("X-Rate-Limiter-Secret", "")
        if provided != cfg.secret:
            return JSONResponse(
                status_code=403,
                content={"error": "invalid or missing X-Rate-Limiter-Secret"},
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/status")
async def status() -> dict[str, Any]:
    """Observability: bucket cardinalities and configured limits."""
    r: aioredis.Redis = app.state.r
    cfg: Config = app.state.cfg
    try:
        short_count = await r.zcard("ratelimit:short")
        long_count = await r.zcard("ratelimit:long")
    except Exception:
        short_count = -1
        long_count = -1
    return {
        "buckets": {"short": short_count, "long": long_count},
        "short_limit": cfg.short_limit,
        "long_limit": cfg.long_limit,
    }


@app.post("/token/acquire")
async def token_acquire(body: dict[str, str]) -> JSONResponse:
    """Attempt to acquire a rate-limit token for the given source."""
    r: aioredis.Redis = app.state.r
    cfg: Config = app.state.cfg

    source = body.get("source", "")
    if source not in cfg.known_sources:
        return JSONResponse(
            status_code=404,
            content={"error": "unknown source"},
        )

    try:
        granted, retry_after_ms = await acquire_token(r, cfg, key_prefix=f"ratelimit:{source}")
    except Exception:
        _log.warning("Redis unreachable — failing open", exc_info=True)
        return JSONResponse(
            status_code=200,
            content={"granted": True, "retry_after_ms": None},
        )

    return JSONResponse(
        status_code=200,
        content={"granted": granted, "retry_after_ms": retry_after_ms},
    )


@app.post("/headers")
async def headers_update(body: dict[str, str]) -> dict[str, bool]:
    """Parse Riot rate-limit response headers and update Redis buckets."""
    r: aioredis.Redis = app.state.r
    cfg: Config = app.state.cfg

    source = body.get("source", "riot")
    rate_limit = body.get("rate_limit", "")
    rate_limit_count = body.get("rate_limit_count", "")
    prefix = f"ratelimit:{source}"

    # Parse limit header to update stored limits
    limits = parse_rate_limit_header(rate_limit)
    if limits is not None:
        short_limit, long_limit = limits
        await r.set(f"{prefix}:limits:short", short_limit, ex=3600)
        await r.set(f"{prefix}:limits:long", long_limit, ex=3600)

    # Parse count header for throttle detection
    throttle = False
    counts = parse_rate_limit_header(rate_limit_count)
    if counts is not None and limits is not None:
        short_count, long_count = counts
        short_limit, long_limit = limits
        # Throttle when current count > 90% of limit
        if short_count > short_limit * 0.9 or long_count > long_limit * 0.9:
            throttle = True
            await r.set(f"{prefix}:throttle", "1", px=cfg.short_window_ms)

    return {"updated": True, "throttle": throttle}
