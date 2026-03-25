"""Admin UI — FastAPI service for DLQ management, replay, and system controls.

All Redis write operations (DLQ replay/clear, system halt/resume) are
handled here, keeping lol-pipeline-ui strictly read-only.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from lol_admin_ui import _helpers

# ---------------------------------------------------------------------------
# PRIN-AUI-03 — refuse to start when ADMIN_UI_SECRET is unset or empty
# ---------------------------------------------------------------------------
_ADMIN_SECRET = os.environ.get("ADMIN_UI_SECRET", "")
if not _ADMIN_SECRET:
    raise ValueError("ADMIN_UI_SECRET environment variable must be set and non-empty")

app = FastAPI(title="LoL Pipeline Admin UI")


# ---------------------------------------------------------------------------
# PRIN-AUI-01 — shared dependency: auth check + Redis injection
# ---------------------------------------------------------------------------


def _get_authed_redis(request: Request) -> Any:
    """Verify auth header and return the Redis client.

    Raises HTTPException(401) when the X-Admin-Secret header is missing or
    does not match the expected value.
    """
    secret = request.headers.get("X-Admin-Secret", "")
    if not secret or secret != _ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")
    return request.app.state.r


AuthedRedis = Annotated[Any, Depends(_get_authed_redis)]


# ---------------------------------------------------------------------------
# GET /health — no auth required
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> JSONResponse:
    """Return basic health status."""
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# GET /dlq — list DLQ entries
# ---------------------------------------------------------------------------


@app.get("/dlq")
async def list_dlq(r: AuthedRedis) -> JSONResponse:
    """List all entries in stream:dlq."""
    entries = await _helpers.list_dlq_entries(r)
    return JSONResponse({"entries": entries, "total": len(entries)})


# ---------------------------------------------------------------------------
# POST /dlq/replay/{message_id} — replay a single DLQ entry
# ---------------------------------------------------------------------------


@app.post("/dlq/replay/{message_id}")
async def replay_dlq_entry(r: AuthedRedis, message_id: str) -> JSONResponse:
    """Replay a DLQ entry back to its original stream."""
    try:
        result = await _helpers.replay_entry(r, message_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)

    if result is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    entry_id, original_stream = result
    return JSONResponse({"replayed": entry_id, "to": original_stream})


# ---------------------------------------------------------------------------
# POST /dlq/clear — remove all DLQ entries
# ---------------------------------------------------------------------------


@app.post("/dlq/clear")
async def clear_dlq(r: AuthedRedis) -> JSONResponse:
    """Remove all entries from stream:dlq."""
    await _helpers.clear_dlq(r)
    return JSONResponse({"cleared": True})


# ---------------------------------------------------------------------------
# POST /system/halt — set system:halted
# ---------------------------------------------------------------------------


@app.post("/system/halt")
async def system_halt(r: AuthedRedis) -> JSONResponse:
    """Set system:halted flag in Redis."""
    await _helpers.set_system_halted(r)
    return JSONResponse({"halted": True})


# ---------------------------------------------------------------------------
# POST /system/resume — delete system:halted
# ---------------------------------------------------------------------------


@app.post("/system/resume")
async def system_resume(r: AuthedRedis) -> JSONResponse:
    """Remove system:halted flag from Redis."""
    await _helpers.clear_system_halted(r)
    return JSONResponse({"halted": False})
