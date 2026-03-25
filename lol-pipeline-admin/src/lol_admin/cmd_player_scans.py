"""Admin CLI: global-scan player commands (recalc-priority, recalc-players)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import redis.asyncio as aioredis

from lol_admin._helpers import _print_ok, _scan_priority_keys


async def cmd_recalc_priority(r: aioredis.Redis, args: argparse.Namespace) -> int:
    """Diagnostic: scan player:priority:* keys and report the count."""
    keys = await _scan_priority_keys(r)
    count = len(keys)
    if getattr(args, "json", False):
        print(json.dumps({"count": count}))
    else:
        _print_ok(
            f"player:priority:* keys found: {count}  (read-only diagnostic — no changes made)"
        )
    return 0


async def cmd_recalc_players(r: aioredis.Redis, args: argparse.Namespace) -> int:
    """Rebuild players:all sorted set from existing player:{puuid} hashes."""
    count = 0
    async for key in r.scan_iter(match="player:*", count=200):
        if key.count(":") != 1:
            continue
        puuid = key.removeprefix("player:")
        seeded_at: str | None = await r.hget(key, "seeded_at")  # type: ignore[misc]
        if not seeded_at:
            continue
        try:
            score = datetime.fromisoformat(seeded_at).timestamp()
        except ValueError:
            continue
        await r.zadd("players:all", {puuid: score})
        count += 1
    if getattr(args, "json", False):
        print(json.dumps({"count": count}))
    else:
        _print_ok(f"players:all rebuilt — {count} players indexed")
    return 0
