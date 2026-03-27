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

# IMP-048: Shared secret sent on every request (empty = auth disabled).
_RATE_LIMITER_SECRET: str = os.environ.get("RATE_LIMITER_SECRET", "")

# Number of connection retries before wait_for_token fails open.
_RATE_LIMITER_CONNECT_RETRIES: int = int(
    os.environ.get("RATE_LIMITER_CONNECT_RETRIES", "3")
)

# Shared async HTTP client (connection pooling)
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        headers: dict[str, str] = {}
        if _RATE_LIMITER_SECRET:
            headers["X-Rate-Limiter-Secret"] = _RATE_LIMITER_SECRET
        _client = httpx.AsyncClient(
            base_url=_RATE_LIMITER_URL,
            timeout=5.0,
            headers=headers,
        )
    return _client


async def wait_for_token(
    source: str,
    endpoint: str,
    *,
    max_wait_s: float = 60.0,
) -> None:
    """Block until a token is granted for (source, endpoint).

    Retries with jitter until granted or max_wait_s exceeded.
    Raises ``TimeoutError`` when the deadline is exceeded without a grant.
    Fail open: if service unreachable or unknown source, logs and returns.
    """
    deadline = asyncio.get_event_loop().time() + max_wait_s
    _connect_attempts = 0
    while True:
        try:
            client = _get_client()
            resp = await client.post(
                "/token/acquire",
                json={"source": source, "endpoint": endpoint},
            )
            if resp.status_code == 404:
                _log.warning("rate-limiter: unknown source %r, failing open", source)
                return
            data = resp.json()
            if data.get("granted"):
                return
            # Reset connect attempts on successful communication
            _connect_attempts = 0
            retry_after_ms: int = data.get("retry_after_ms") or 1000
            wait_s = retry_after_ms / 1000.0
            # Add jitter (+-10%)
            jitter = wait_s * 0.1 * (random.random() * 2 - 1)  # noqa: S311
            actual_wait = max(0.01, wait_s + jitter)
            if asyncio.get_event_loop().time() + actual_wait > deadline:
                raise TimeoutError(
                    f"rate-limiter: deadline exceeded waiting for token "
                    f"(source={source!r}, endpoint={endpoint!r})"
                )
            await asyncio.sleep(actual_wait)
        except TimeoutError:
            raise
        except Exception as exc:
            _connect_attempts += 1
            if _connect_attempts < _RATE_LIMITER_CONNECT_RETRIES:
                _log.debug(
                    "rate-limiter: attempt %d/%d failed (%s), retrying",
                    _connect_attempts,
                    _RATE_LIMITER_CONNECT_RETRIES,
                    exc,
                )
                await asyncio.sleep(0.5)
                continue
            _log.warning(
                "rate-limiter: service unreachable after %d retries, failing open",
                _RATE_LIMITER_CONNECT_RETRIES,
            )
            return


async def notify_cooling_off(source: str, delay_ms: int) -> None:
    """Tell the rate-limiter a real 429 was received; blocks all tokens for delay_ms ms.

    Best-effort: logs on failure but does not raise.
    """
    try:
        client = _get_client()
        await client.post(
            "/cooling-off",
            json={"source": source, "delay_ms": delay_ms},
            timeout=1.0,
        )
    except Exception as exc:
        _log.warning("rate-limiter: /cooling-off failed (%s)", exc)


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
