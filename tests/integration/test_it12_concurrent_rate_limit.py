"""IT-12 — Concurrent rate-limit tokens are non-negative.

Uses the HTTP rate-limiter service (lol-pipeline-rate-limiter) via the
thin client in ``lol_pipeline.rate_limiter_client``.  The service handles
the Lua sliding-window logic; the tests exercise concurrency correctness
through the HTTP boundary.
"""

from __future__ import annotations

import asyncio

import pytest
import redis.asyncio as aioredis

from helpers import tlog
from lol_pipeline.rate_limiter_client import try_token, wait_for_token

_SHORT_LIMIT = 5
_CONCURRENT_CALLS = 20


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_wait_for_token__all_succeed(
    r: aioredis.Redis,
    rate_limiter_container: str,
) -> None:
    """20 concurrent wait_for_token calls all complete without exception."""
    tlog("it12")

    # Set stored limits so the Lua script uses our test values
    await r.set("ratelimit:limits:short", str(_SHORT_LIMIT))
    await r.set("ratelimit:limits:long", "100")

    async def acquire(idx: int) -> int:
        await wait_for_token("riot", "match")
        return idx

    # Fire 20 concurrent calls — all must succeed (no exception)
    results = await asyncio.wait_for(
        asyncio.gather(*[acquire(i) for i in range(_CONCURRENT_CALLS)]),
        timeout=30,
    )
    assert len(results) == _CONCURRENT_CALLS
    assert set(results) == set(range(_CONCURRENT_CALLS))


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_acquire_token__never_exceeds_limit(
    r: aioredis.Redis,
    rate_limiter_container: str,
) -> None:
    """Concurrent try_token calls never admit more tokens than the short limit."""
    tlog("it12")

    # Use a tight limit to make over-admission detectable
    limit = _SHORT_LIMIT

    # Set stored limits so the Lua script uses our test values
    await r.set("ratelimit:limits:short", str(limit))
    await r.set("ratelimit:limits:long", "1000")

    # Fire many concurrent try_token calls
    results = await asyncio.gather(
        *[try_token("riot", "match") for _ in range(_CONCURRENT_CALLS)]
    )

    admitted = sum(1 for granted in results if granted)
    denied = sum(1 for granted in results if not granted)

    # At most 'limit' tokens should be admitted in the 1-second window
    assert admitted <= limit, (
        f"Admitted {admitted} tokens but limit is {limit}"
    )
    # Some should be denied since 20 > 5
    assert denied > 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_rate_limit__zset_counts_non_negative(
    r: aioredis.Redis,
    rate_limiter_container: str,
) -> None:
    """After concurrent token acquisitions, ZSET cardinality is non-negative and bounded."""
    tlog("it12")

    limit = _SHORT_LIMIT
    await r.set("ratelimit:limits:short", str(limit))
    await r.set("ratelimit:limits:long", "1000")

    # Acquire tokens concurrently via HTTP
    await asyncio.gather(
        *[try_token("riot", "match") for _ in range(_CONCURRENT_CALLS)]
    )

    # Check ZSET sizes are non-negative and within bounds
    short_count: int = await r.zcard("ratelimit:short")
    long_count: int = await r.zcard("ratelimit:long")

    assert short_count >= 0
    assert long_count >= 0
    # Should not exceed the limit
    assert short_count <= limit
    assert long_count <= limit


@pytest.mark.asyncio
@pytest.mark.integration
async def test_wait_for_token__sequential_beyond_limit(
    r: aioredis.Redis,
    rate_limiter_container: str,
) -> None:
    """wait_for_token blocks (polls) when limit is exhausted, then succeeds."""
    tlog("it12")

    limit = 3
    # Set stored limits so the Lua script uses our test values
    await r.set("ratelimit:limits:short", str(limit))
    await r.set("ratelimit:limits:long", "1000")

    # Exhaust the short window
    for _ in range(limit):
        assert await try_token("riot", "match") is True

    # Next acquire should be denied (window is full)
    assert await try_token("riot", "match") is False

    # wait_for_token should eventually succeed (within ~1s as window slides)
    await asyncio.wait_for(
        wait_for_token("riot", "match"),
        timeout=5,
    )
    # If we reach here, the token was acquired after the window slid
