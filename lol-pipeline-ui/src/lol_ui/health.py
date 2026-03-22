"""Health check helper — build a detailed health status dict from Redis."""

from __future__ import annotations

import contextlib
from typing import Any

from lol_ui.constants import _STREAM_KEYS


async def _health_status(r: Any) -> dict[str, object]:
    """Query Redis and return a health status dict.

    Returns a dict with:
    - status: "ok" (always, unless Redis raises)
    - redis: "connected"
    - system_halted: bool
    - streams: dict of stream name -> depth (int)
    - dlq_depth: int (stream:dlq length)
    - redis_memory_mb: float (used_memory from INFO, 0.0 if unavailable)
    """
    # Pipeline for stream lengths + halted flag (one RTT)
    async with r.pipeline(transaction=False) as pipe:
        for s in _STREAM_KEYS:
            pipe.xlen(s)
        pipe.zcard("delayed:messages")
        pipe.get("system:halted")
        results = await pipe.execute()

    n = len(_STREAM_KEYS)
    stream_lengths: list[int] = results[:n]
    delayed: int = results[n]
    halted_raw: str | None = results[n + 1]

    # INFO is not available in fakeredis; degrade gracefully
    memory_mb = 0.0
    with contextlib.suppress(Exception):
        memory_info: dict[str, Any] = await r.info("memory")
        used_memory_bytes: int = memory_info.get("used_memory", 0)
        memory_mb = round(used_memory_bytes / (1024 * 1024), 1)

    streams: dict[str, int] = {}
    for s, length in zip(_STREAM_KEYS, stream_lengths, strict=True):
        streams[s] = length
    streams["delayed:messages"] = delayed

    dlq_depth = streams.get("stream:dlq", 0)

    return {
        "status": "ok",
        "redis": "connected",
        "system_halted": halted_raw == "1",
        "streams": streams,
        "dlq_depth": dlq_depth,
        "redis_memory_mb": memory_mb,
    }
