"""IT-08 — Seed with priority: verify Discovery paused until priority clears."""

from __future__ import annotations

import pytest
import redis.asyncio as aioredis

from helpers import PUUID, tlog
from lol_pipeline.priority import clear_priority, has_priority_players, set_priority


@pytest.mark.asyncio
@pytest.mark.integration
async def test_set_priority__creates_key(r: aioredis.Redis) -> None:
    """set_priority creates player:priority:{puuid} and SCAN detects it."""
    tlog("it08")

    # Baseline: no priority
    assert await has_priority_players(r) is False

    # Set priority for a single player
    await set_priority(r, PUUID)

    # Verify the priority key exists
    assert await r.exists(f"player:priority:{PUUID}") == 1

    # SCAN-based detection finds it
    assert await has_priority_players(r) is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_clear_priority__removes_key(r: aioredis.Redis) -> None:
    """clear_priority removes the key and SCAN reports no priority players."""
    tlog("it08")

    # Set then clear
    await set_priority(r, PUUID)
    assert await has_priority_players(r) is True

    await clear_priority(r, PUUID)

    # Key should be gone
    assert await r.exists(f"player:priority:{PUUID}") == 0
    assert await has_priority_players(r) is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_set_priority__idempotent(r: aioredis.Redis) -> None:
    """Calling set_priority twice for the same puuid does not create duplicate keys."""
    tlog("it08")

    await set_priority(r, PUUID)
    await set_priority(r, PUUID)

    # Only one key exists, SCAN finds it
    assert await r.exists(f"player:priority:{PUUID}") == 1
    assert await has_priority_players(r) is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_clear_priority__idempotent(r: aioredis.Redis) -> None:
    """Calling clear_priority when no key exists does not error."""
    tlog("it08")

    await clear_priority(r, PUUID)
    assert await has_priority_players(r) is False
