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
from lol_rate_limiter._token import acquire_token_for_domain
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
    _log.info(
        "Rate-limiter starting",
        extra={"redis_url": cfg.redis_url, "domains": list(cfg.domains)},
    )
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
    """Observability: per-domain bucket status and configured limits."""
    r: aioredis.Redis = app.state.r
    cfg: Config = app.state.cfg
    domains_status: dict[str, Any] = {}

    for domain_name, domain in cfg.domains.items():
        try:
            halved_raw = await r.get(f"ratelimit:{domain_name}:halved")
            halve_count_raw = await r.get(f"ratelimit:{domain_name}:halve_count")
            halved_at_raw = await r.get(f"ratelimit:{domain_name}:halved_at")
            stored_short = await r.get(f"ratelimit:{domain_name}:limits:short")
            stored_long = await r.get(f"ratelimit:{domain_name}:limits:long")
        except Exception:
            halved_raw = halve_count_raw = halved_at_raw = stored_short = stored_long = None

        entry: dict[str, Any] = {
            "short_limit": int(stored_short) if stored_short else domain.short_limit,
            "long_limit": int(stored_long) if stored_long else domain.long_limit,
            "halved": bool(halved_raw),
            "halve_count": int(halve_count_raw) if halve_count_raw else 0,
            "header_aware": domain.header_aware,
            "has_method_limits": domain.has_method_limits,
        }
        if halved_at_raw:
            entry["halved_at"] = int(halved_at_raw)
        domains_status[domain_name] = entry

    return {"domains": domains_status}


@app.post("/token/acquire")
async def token_acquire(body: dict[str, Any]) -> JSONResponse:
    """Attempt to acquire a rate-limit token for the given domain."""
    r: aioredis.Redis = app.state.r
    cfg: Config = app.state.cfg

    domain_name = body.get("domain", "")
    endpoint = str(body.get("endpoint", ""))
    is_ui = bool(body.get("is_ui", False))
    # priority accepted but not used in DYN-2 (reserved for DYN-3)

    domain = cfg.domains.get(domain_name)
    if domain is None:
        return JSONResponse(status_code=404, content={"error": "unknown domain"})

    try:
        granted, retry_after_ms = await acquire_token_for_domain(
            r,
            domain,
            endpoint,
            is_ui=is_ui,
        )
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


@app.post("/cooling-off")
async def cooling_off_notify(body: dict[str, Any]) -> JSONResponse:
    """Record that the given domain received a real 429 — blocks all tokens."""
    r: aioredis.Redis = app.state.r
    cfg: Config = app.state.cfg
    domain_name = body.get("domain", "")
    domain = cfg.domains.get(domain_name)
    if domain is None:
        return JSONResponse(status_code=404, content={"error": "unknown domain"})
    delay_ms = int(body.get("delay_ms", 60_000))
    try:
        await r.set(f"ratelimit:{domain_name}:cooling_off", "1", px=max(delay_ms, 1000))
    except Exception:
        _log.warning("Redis unreachable for cooling-off set", exc_info=True)
        return JSONResponse(status_code=200, content={"ok": False})
    _log.warning("domain %r entering cooling-off for %dms", domain_name, delay_ms)
    return JSONResponse(status_code=200, content={"ok": True})


@app.post("/cooling-off/reset")
async def cooling_off_reset(body: dict[str, Any]) -> JSONResponse:
    """Reset halved limits for a domain, falling back to config defaults."""
    r: aioredis.Redis = app.state.r
    cfg: Config = app.state.cfg
    domain_name = body.get("domain", "")
    if domain_name not in cfg.domains:
        return JSONResponse(status_code=404, content={"error": "unknown domain"})
    await r.delete(
        f"ratelimit:{domain_name}:halved",
        f"ratelimit:{domain_name}:limits:short",
        f"ratelimit:{domain_name}:limits:long",
    )
    return JSONResponse(status_code=200, content={"ok": True})


@app.post("/headers")
async def headers_update(body: dict[str, str]) -> JSONResponse:
    """Parse rate-limit response headers and update Redis buckets."""
    r: aioredis.Redis = app.state.r
    cfg: Config = app.state.cfg

    domain_name = body.get("domain", "")
    domain = cfg.domains.get(domain_name)
    if domain is None:
        return JSONResponse(status_code=404, content={"error": "unknown domain"})

    if not domain.header_aware:
        return JSONResponse(status_code=200, content={"updated": False})

    rate_limit = body.get("rate_limit", "")
    prefix = f"ratelimit:{domain_name}"

    limits = parse_rate_limit_header(rate_limit)
    if limits is not None:
        short_limit, long_limit = limits
        await r.set(f"{prefix}:limits:short", short_limit, ex=3600)
        await r.set(f"{prefix}:limits:long", long_limit, ex=3600)

    return JSONResponse(status_code=200, content={"updated": True})
