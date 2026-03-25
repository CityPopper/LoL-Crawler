"""Admin CLI: player-targeted commands (reseed, reset-stats, clear-priority)."""

from __future__ import annotations

import argparse
import uuid

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import (
    PRIORITY_ACTIVE_SET,
    PRIORITY_MANUAL_20,
    set_priority,
)
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.streams import ANALYZE_STREAM_MAXLEN, publish

from lol_admin._constants import _STREAM_ANALYZE, _STREAM_PUUID
from lol_admin._helpers import (
    _print_error,
    _print_info,
    _print_ok,
    _resolve_puuid,
    _sanitize,
    _scan_priority_keys,
)


async def cmd_reseed(
    r: aioredis.Redis, riot: RiotClient, cfg: Config, args: argparse.Namespace
) -> int:
    """Clear cooldown and re-enqueue player to stream:puuid."""
    puuid = await _resolve_puuid(riot, args.riot_id, args.region, r)
    if puuid is None:
        return 1
    game_name, tag_line = args.riot_id.split("#", 1)

    # Clear cooldown fields so the next Seed run is not blocked
    await r.hdel(f"player:{puuid}", "seeded_at", "last_crawled_at")  # type: ignore[misc]

    # Publish directly to stream:puuid (manual_20 priority, matching Seed service)
    envelope = MessageEnvelope(
        source_stream=_STREAM_PUUID,
        type="puuid",
        payload={
            "puuid": puuid,
            "game_name": game_name,
            "tag_line": tag_line,
            "region": args.region,
        },
        max_attempts=cfg.max_attempts,
        priority=PRIORITY_MANUAL_20,
        correlation_id=str(uuid.uuid4()),
    )
    await set_priority(r, puuid)
    entry_id = await publish(r, _STREAM_PUUID, envelope)
    _print_ok(f"reseeded {_sanitize(args.riot_id)} \u2192 {_STREAM_PUUID} ({entry_id})")
    return 0


async def cmd_reset_stats(
    r: aioredis.Redis, riot: RiotClient, cfg: Config, args: argparse.Namespace
) -> int:
    """Wipe player stats and re-trigger analysis."""
    puuid = await _resolve_puuid(riot, args.riot_id, args.region, r)
    if puuid is None:
        return 1
    keys_to_delete = [
        f"player:stats:{puuid}",
        f"player:stats:cursor:{puuid}",
        f"player:champions:{puuid}",
        f"player:roles:{puuid}",
    ]
    deleted: int = await r.delete(*keys_to_delete)
    envelope = MessageEnvelope(
        source_stream=_STREAM_ANALYZE,
        type="analyze",
        payload={"puuid": puuid},
        max_attempts=cfg.max_attempts,
        correlation_id=str(uuid.uuid4()),
    )
    await publish(r, _STREAM_ANALYZE, envelope, maxlen=ANALYZE_STREAM_MAXLEN)
    safe_rid = _sanitize(args.riot_id)
    _print_ok(f"deleted {deleted} keys for {safe_rid}; enqueued re-analysis")
    return 0


async def cmd_clear_priority(r: aioredis.Redis, riot: RiotClient, args: argparse.Namespace) -> int:
    """Delete player:priority:* keys for a specific player or all players."""
    if not getattr(args, "all", False) and not getattr(args, "riot_id", None):
        _print_error("specify a Riot ID or --all")
        return 1
    if getattr(args, "all", False):
        keys = await _scan_priority_keys(r)
        if keys:
            await r.delete(*keys)
        await r.delete(PRIORITY_ACTIVE_SET)
        count = len(keys)
        _print_ok(f"deleted {count} player:priority:* keys")
        return 0
    # Single player
    puuid = await _resolve_puuid(riot, args.riot_id, args.region, r)
    if puuid is None:
        return 1
    deleted: int = await r.delete(f"player:priority:{puuid}")
    await r.srem(PRIORITY_ACTIVE_SET, puuid)  # type: ignore[misc]
    safe_rid = _sanitize(args.riot_id)
    if deleted:
        _print_ok(f"deleted player:priority:{puuid}")
    else:
        _print_info(f"no priority key found for {safe_rid}")
    return 0
