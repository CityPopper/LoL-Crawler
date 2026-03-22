"""Web UI — view player stats."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.exceptions
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import RiotClient
from starlette.responses import Response

from lol_ui.rendering import _page
from lol_ui.routes.champions import router as champions_router
from lol_ui.routes.dashboard import router as dashboard_router
from lol_ui.routes.dlq import router as dlq_router
from lol_ui.routes.logs import router as logs_router
from lol_ui.routes.matchups import router as matchups_router
from lol_ui.routes.players import router as players_router
from lol_ui.routes.stats import router as stats_router
from lol_ui.routes.streams import router as streams_router

_log = get_logger("ui")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = Config()
    app.state.cfg = cfg
    app.state.r = get_redis(cfg.redis_url)
    app.state.riot = RiotClient(cfg.riot_api_key, r=app.state.r)

    yield

    await app.state.r.aclose()
    await app.state.riot.close()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="LoL Pipeline UI", lifespan=_lifespan)


@app.middleware("http")
async def add_security_headers(request: Request, call_next: Any) -> Response:
    """Add security headers to every response."""
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' ddragon.leagueoflegends.com data:; "
        "connect-src 'self'"
    )
    return response


@app.exception_handler(redis.exceptions.RedisError)
async def redis_error_handler(request: Request, exc: redis.exceptions.RedisError) -> HTMLResponse:
    """Return a user-friendly 503 page when Redis is unreachable."""
    body = _page(
        "Error",
        "<p>Cannot connect to Redis. Is the stack running? Try: <code>just up</code></p>",
    )
    return HTMLResponse(content=body, status_code=503)


@app.exception_handler(ConnectionError)
async def connection_error_handler(request: Request, exc: ConnectionError) -> HTMLResponse:
    """Return a user-friendly 503 page on connection errors."""
    body = _page(
        "Error",
        "<p>Cannot connect to Redis. Is the stack running? Try: <code>just up</code></p>",
    )
    return HTMLResponse(content=body, status_code=503)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Include route modules
# ---------------------------------------------------------------------------

app.include_router(dashboard_router)
app.include_router(stats_router)
app.include_router(players_router)
app.include_router(streams_router)
app.include_router(dlq_router)
app.include_router(champions_router)
app.include_router(matchups_router)
app.include_router(logs_router)
