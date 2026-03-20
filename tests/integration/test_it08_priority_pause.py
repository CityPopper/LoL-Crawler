"""IT-08 — Seed with priority: verify Discovery paused until priority clears."""

from __future__ import annotations

import pytest
import redis.asyncio as aioredis

from helpers import PUUID, tlog
from lol_pipeline.priority import clear_priority, priority_count, set_priority


@pytest.mark.asyncio
@pytest.mark.integration
async def test_set_priority__increments_count(r: aioredis.Redis) -> None:
    """set_priority creates player:priority:{puuid} and increments system:priority_count."""
    tlog("it08")

    # Baseline: no priority
    assert await priority_count(r) == 0

    # Set priority for a single player
    count = await set_priority(r, PUUID)
    assert count == 1

    # Verify the priority key exists
    assert await r.exists(f"player:priority:{PUUID}") == 1

    # Verify system:priority_count reflects one active priority
    assert await priority_count(r) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_clear_priority__decrements_count_to_zero(r: aioredis.Redis) -> None:
    """clear_priority removes the key and decrements system:priority_count back to 0."""
    tlog("it08")

    # Set then clear
    await set_priority(r, PUUID)
    assert await priority_count(r) == 1

    count = await clear_priority(r, PUUID)
    assert count == 0

    # Key should be gone
    assert await r.exists(f"player:priority:{PUUID}") == 0
    assert await priority_count(r) == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_set_priority__idempotent(r: aioredis.Redis) -> None:
    """Calling set_priority twice for the same puuid does not double-increment."""
    tlog("it08")

    await set_priority(r, PUUID)
    count = await set_priority(r, PUUID)
    assert count == 1

    # Only one key exists, count stays at 1
    assert await priority_count(r) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_clear_priority__idempotent(r: aioredis.Redis) -> None:
    """Calling clear_priority when no key exists does not go negative."""
    tlog("it08")

    count = await clear_priority(r, PUUID)
    assert count == 0
    assert await priority_count(r) == 0
