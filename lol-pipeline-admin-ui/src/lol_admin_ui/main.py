"""Admin UI — FastAPI service for DLQ management, replay, and system controls.

All Redis write operations (DLQ replay/clear, system halt/resume) are
handled here, keeping lol-pipeline-ui strictly read-only.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="LoL Pipeline Admin UI")

_ADMIN_SECRET = os.environ.get("ADMIN_UI_SECRET", "")


def _get_redis(request: Request) -> Any:
    """Return the Redis client from app state."""
    return request.app.state.r


def _check_auth(request: Request) -> JSONResponse | None:
    """Return a 401 JSONResponse if the request is not authenticated, else None."""
    secret = request.headers.get("X-Admin-Secret", "")
    if not secret or secret != _ADMIN_SECRET:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None


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
async def list_dlq(request: Request) -> JSONResponse:
    """List all entries in stream:dlq."""
    auth_err = _check_auth(request)
    if auth_err:
        return auth_err

    r = _get_redis(request)
    raw_entries: list[tuple[str, dict[str, str]]] = await r.xrange("stream:dlq")
    entries: list[dict[str, str]] = []
    for entry_id, fields in raw_entries:
        entry: dict[str, str] = {"id": entry_id, **fields}
        entries.append(entry)
    return JSONResponse({"entries": entries, "total": len(entries)})


# ---------------------------------------------------------------------------
# POST /dlq/replay/{message_id} — replay a single DLQ entry
# ---------------------------------------------------------------------------


@app.post("/dlq/replay/{message_id}")
async def replay_dlq_entry(request: Request, message_id: str) -> JSONResponse:
    """Replay a DLQ entry back to its original stream."""
    auth_err = _check_auth(request)
    if auth_err:
        return auth_err

    r = _get_redis(request)
    entries: list[tuple[str, dict[str, str]]] = await r.xrange(
        "stream:dlq", min=message_id, max=message_id, count=1
    )
    if not entries:
        return JSONResponse({"error": "not found"}, status_code=404)

    entry_id, fields = entries[0]
    original_stream = fields.get("original_stream", "")
    if not original_stream:
        return JSONResponse({"error": "no original_stream field"}, status_code=422)

    # Publish message fields to the original stream
    publish_fields: dict[str, str] = {
        k: v for k, v in fields.items() if k != "original_stream"
    }
    await r.xadd(original_stream, publish_fields)
    await r.xdel("stream:dlq", entry_id)

    return JSONResponse({"replayed": entry_id, "to": original_stream})


# ---------------------------------------------------------------------------
# POST /dlq/clear — remove all DLQ entries
# ---------------------------------------------------------------------------


@app.post("/dlq/clear")
async def clear_dlq(request: Request) -> JSONResponse:
    """Remove all entries from stream:dlq."""
    auth_err = _check_auth(request)
    if auth_err:
        return auth_err

    r = _get_redis(request)
    # Delete the entire stream key (recreated automatically on next xadd)
    await r.delete("stream:dlq")
    return JSONResponse({"cleared": True})


# ---------------------------------------------------------------------------
# POST /system/halt — set system:halted
# ---------------------------------------------------------------------------


@app.post("/system/halt")
async def system_halt(request: Request) -> JSONResponse:
    """Set system:halted flag in Redis."""
    auth_err = _check_auth(request)
    if auth_err:
        return auth_err

    r = _get_redis(request)
    await r.set("system:halted", "1")
    return JSONResponse({"halted": True})


# ---------------------------------------------------------------------------
# POST /system/resume — delete system:halted
# ---------------------------------------------------------------------------


@app.post("/system/resume")
async def system_resume(request: Request) -> JSONResponse:
    """Remove system:halted flag from Redis."""
    auth_err = _check_auth(request)
    if auth_err:
        return auth_err

    r = _get_redis(request)
    await r.delete("system:halted")
    return JSONResponse({"halted": False})
