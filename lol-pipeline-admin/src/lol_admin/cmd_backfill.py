"""Admin CLI: backfill-champions command."""

from __future__ import annotations

import argparse

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.constants import CHAMPION_STATS_TTL_SECONDS

from lol_admin._helpers import (
    _backfill_batch,
    _print_info,
    _print_ok,
    _scan_parsed_matches,
)


async def cmd_backfill_champions(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    """Reprocess all parsed ranked matches to populate champion stats."""
    done_key = "champion:backfill:done"
    parsed = await _scan_parsed_matches(r)
    already_done: set[str] = await r.smembers(done_key)  # type: ignore[misc]
    todo = parsed - already_done
    if not todo:
        _print_info("No matches to backfill (all already processed)")
        return 0
    count = 0
    batch: list[str] = []
    for match_id in todo:
        batch.append(match_id)
        if len(batch) >= 100:
            processed = await _backfill_batch(r, batch)
            count += processed
            await r.sadd(done_key, *batch)  # type: ignore[misc]
            batch = []
            _print_info(f"Progress: {count} matches backfilled...")
    if batch:
        processed = await _backfill_batch(r, batch)
        count += processed
        await r.sadd(done_key, *batch)  # type: ignore[misc]
    await r.expire(done_key, CHAMPION_STATS_TTL_SECONDS)
    _print_ok(f"Backfilled champion stats from {count} ranked matches")
    return 0
