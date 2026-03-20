"""IT-09 — Two manual seeds: both process before Discovery priority clears."""

from __future__ import annotations

import pytest
import redis.asyncio as aioredis

from helpers import tlog
from lol_pipeline.priority import clear_priority, priority_count, set_priority

_PUUID_A = "test-puuid-player-a"
_PUUID_B = "test-puuid-player-b"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_two_seeds__priority_count_reaches_two(r: aioredis.Redis) -> None:
    """Seeding two players in rapid succession raises priority_count to 2."""
    tlog("it09")

    await set_priority(r, _PUUID_A)
    count = await set_priority(r, _PUUID_B)
    assert count == 2
    assert await priority_count(r) == 2

    # Both keys exist
    assert await r.exists(f"player:priority:{_PUUID_A}") == 1
    assert await r.exists(f"player:priority:{_PUUID_B}") == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_two_seeds__clear_one_drops_to_one(r: aioredis.Redis) -> None:
    """Clearing one of two priorities decrements count to 1."""
    tlog("it09")

    await set_priority(r, _PUUID_A)
    await set_priority(r, _PUUID_B)
    assert await priority_count(r) == 2

    count = await clear_priority(r, _PUUID_A)
    assert count == 1
    assert await priority_count(r) == 1

    # A is gone, B remains
    assert await r.exists(f"player:priority:{_PUUID_A}") == 0
    assert await r.exists(f"player:priority:{_PUUID_B}") == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_two_seeds__clear_both_drops_to_zero(r: aioredis.Redis) -> None:
    """Clearing both priorities brings count back to 0."""
    tlog("it09")

    await set_priority(r, _PUUID_A)
    await set_priority(r, _PUUID_B)
    assert await priority_count(r) == 2

    await clear_priority(r, _PUUID_A)
    count = await clear_priority(r, _PUUID_B)
    assert count == 0
    assert await priority_count(r) == 0

    # Both keys gone
    assert await r.exists(f"player:priority:{_PUUID_A}") == 0
    assert await r.exists(f"player:priority:{_PUUID_B}") == 0
