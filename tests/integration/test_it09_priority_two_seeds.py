"""IT-09 — Two manual seeds: both process before Discovery priority clears."""

from __future__ import annotations

import pytest
import redis.asyncio as aioredis

from helpers import tlog
from lol_pipeline.priority import clear_priority, has_priority_players, set_priority

_PUUID_A = "test-puuid-player-a"
_PUUID_B = "test-puuid-player-b"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_two_seeds__both_detected(r: aioredis.Redis) -> None:
    """Seeding two players creates two priority keys, both detected by SCAN."""
    tlog("it09")

    await set_priority(r, _PUUID_A)
    await set_priority(r, _PUUID_B)
    assert await has_priority_players(r) is True

    # Both keys exist
    assert await r.exists(f"player:priority:{_PUUID_A}") == 1
    assert await r.exists(f"player:priority:{_PUUID_B}") == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_two_seeds__clear_one__still_detected(r: aioredis.Redis) -> None:
    """Clearing one of two priorities still detects remaining key."""
    tlog("it09")

    await set_priority(r, _PUUID_A)
    await set_priority(r, _PUUID_B)
    assert await has_priority_players(r) is True

    await clear_priority(r, _PUUID_A)

    # A is gone, B remains
    assert await r.exists(f"player:priority:{_PUUID_A}") == 0
    assert await r.exists(f"player:priority:{_PUUID_B}") == 1
    assert await has_priority_players(r) is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_two_seeds__clear_both__not_detected(r: aioredis.Redis) -> None:
    """Clearing both priorities means SCAN finds no keys."""
    tlog("it09")

    await set_priority(r, _PUUID_A)
    await set_priority(r, _PUUID_B)
    assert await has_priority_players(r) is True

    await clear_priority(r, _PUUID_A)
    await clear_priority(r, _PUUID_B)

    # Both keys gone
    assert await r.exists(f"player:priority:{_PUUID_A}") == 0
    assert await r.exists(f"player:priority:{_PUUID_B}") == 0
    assert await has_priority_players(r) is False
