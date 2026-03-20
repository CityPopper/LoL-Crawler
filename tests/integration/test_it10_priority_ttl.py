"""IT-10 — Priority TTL expiry: key expires, count never cleanly decremented."""

from __future__ import annotations

import asyncio

import pytest
import redis.asyncio as aioredis

from helpers import PUUID, tlog
from lol_pipeline.priority import priority_count, set_priority

_SHORT_TTL_SECONDS = 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_priority_ttl__key_expires(r: aioredis.Redis) -> None:
    """Priority key with short TTL expires after the TTL elapses."""
    tlog("it10")

    # Set priority with a 1-second TTL
    count = await set_priority(r, PUUID, ttl=_SHORT_TTL_SECONDS)
    assert count == 1
    assert await r.exists(f"player:priority:{PUUID}") == 1

    # Wait for TTL to expire
    await asyncio.sleep(2)

    # The key should be gone
    assert await r.exists(f"player:priority:{PUUID}") == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_priority_ttl__count_stale_after_expiry(r: aioredis.Redis) -> None:
    """After TTL expiry, system:priority_count remains stale (never decremented).

    This is an inherent limitation: Redis key expiry does not trigger the Lua DECR
    script. The count stays at 1 even though no priority key exists.
    """
    tlog("it10")

    await set_priority(r, PUUID, ttl=_SHORT_TTL_SECONDS)
    assert await priority_count(r) == 1

    await asyncio.sleep(2)

    # Key is gone but counter was never decremented
    assert await r.exists(f"player:priority:{PUUID}") == 0
    # Count is stale — still reads 1 because TTL expiry does not call clear_priority
    count = await priority_count(r)
    assert count == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_priority_ttl__verify_ttl_set(r: aioredis.Redis) -> None:
    """Verify the priority key has a TTL set (not persistent)."""
    tlog("it10")

    await set_priority(r, PUUID, ttl=_SHORT_TTL_SECONDS)

    ttl = await r.ttl(f"player:priority:{PUUID}")
    # TTL should be 1 or 0 (in the process of expiring) but not -1 (no expiry)
    assert ttl >= 0
    assert ttl <= _SHORT_TTL_SECONDS
