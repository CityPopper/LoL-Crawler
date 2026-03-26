"""Thin HTTP client for the rate-limiter service.

Replaces the Redis-based rate_limiter.py. All rate-limit decisions
now go through the lol-pipeline-rate-limiter HTTP service.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random

import httpx

_log = logging.getLogger(__name__)

# Service URL from env (default: internal Docker network name)
_RATE_LIMITER_URL: str = os.environ.get("RATE_LIMITER_URL", "http://rate-limiter:8079")

# Shared async HTTP client (connection pooling)
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client  # noqa: PLW0603
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(base_url=_RATE_LIMITER_URL, timeout=5.0)
    return _client


async def wait_for_token(
    source: str,
    endpoint: str,
    *,
    max_wait_s: float = 60.0,
) -> None:
    """Block until a token is granted for (source, endpoint).

    Retries with jitter until granted or max_wait_s exceeded.
    Fail open: if service unreachable, logs warning and returns immediately.
    """
    deadline = asyncio.get_event_loop().time() + max_wait_s
    while True:
        try:
            client = _get_client()
            resp = await client.post(
                "/token/acquire",
                json={"source": source, "endpoint": endpoint},
            )
            if resp.status_code == 404:
                # Unknown source — fail open
                _log.warning("rate-limiter: unknown source %r, failing open", source)
                return
            data = resp.json()
            if data.get("granted"):
                return
            retry_after_ms: int = data.get("retry_after_ms") or 1000
            wait_s = retry_after_ms / 1000.0
            # Add jitter (+-10%)
            jitter = wait_s * 0.1 * (random.random() * 2 - 1)  # noqa: S311
            actual_wait = max(0.01, wait_s + jitter)
            if asyncio.get_event_loop().time() + actual_wait > deadline:
                _log.warning("rate-limiter: timeout waiting for token, failing open")
                return
            await asyncio.sleep(actual_wait)
        except Exception as exc:
            _log.warning("rate-limiter: service unreachable (%s), failing open", exc)
            return


async def try_token(source: str, endpoint: str) -> bool:
    """Try to acquire a token once. Returns True if granted.

    Fail open: returns True if service unreachable.
    """
    try:
        client = _get_client()
        resp = await client.post(
            "/token/acquire",
            json={"source": source, "endpoint": endpoint},
        )
        if resp.status_code == 404:
            _log.warning("rate-limiter: unknown source %r, failing open", source)
            return True
        return bool(resp.json().get("granted", True))
    except Exception as exc:
        _log.warning("rate-limiter: service unreachable (%s), failing open", exc)
        return True
