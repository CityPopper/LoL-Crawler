"""IT-10 — Priority TTL expiry: SCAN-based detection returns False after key expires."""

from __future__ import annotations

import asyncio

import pytest
import redis.asyncio as aioredis

from helpers import PUUID, tlog
from lol_pipeline.priority import has_priority_players, set_priority

_SHORT_TTL_SECONDS = 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_priority_ttl__key_expires(r: aioredis.Redis) -> None:
    """Priority key with short TTL expires after the TTL elapses."""
    tlog("it10")

    # Set priority with a 1-second TTL
    await set_priority(r, PUUID, ttl=_SHORT_TTL_SECONDS)
    assert await r.exists(f"player:priority:{PUUID}") == 1

    # Wait for TTL to expire
    await asyncio.sleep(2)

    # The key should be gone
    assert await r.exists(f"player:priority:{PUUID}") == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_priority_ttl__scan_detects_no_keys_after_expiry(
    r: aioredis.Redis,
) -> None:
    """After TTL expiry, has_priority_players returns False (no stale counter drift).

    This is the key improvement over the counter-based approach: SCAN correctly
    reports no priority keys after TTL expiry, whereas the old counter would
    remain stale at 1.
    """
    tlog("it10")

    await set_priority(r, PUUID, ttl=_SHORT_TTL_SECONDS)
    assert await has_priority_players(r) is True

    await asyncio.sleep(2)

    # Key is gone AND detection correctly reports False (no drift)
    assert await r.exists(f"player:priority:{PUUID}") == 0
    assert await has_priority_players(r) is False


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
