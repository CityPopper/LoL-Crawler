"""Admin CLI: stats command."""

from __future__ import annotations

import argparse
import json

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.riot_api import RiotClient

from lol_admin._formatting import _format_stats_output
from lol_admin._helpers import _print_error, _resolve_puuid, _sanitize


async def cmd_stats(
    r: aioredis.Redis, riot: RiotClient, cfg: Config, args: argparse.Namespace
) -> int:
    puuid = await _resolve_puuid(riot, args.riot_id, args.region, r)
    if puuid is None:
        return 1
    stats: dict[str, str] = await r.hgetall(f"player:stats:{puuid}")  # type: ignore[misc]
    if not stats:
        safe_rid = _sanitize(args.riot_id)
        _print_error(f"player not found in Redis (not yet analyzed): {safe_rid}")
        return 1
    game_name, tag_line = args.riot_id.split("#", 1)
    if getattr(args, "json", False):
        record = {
            "game_name": _sanitize(game_name),
            "tag_line": _sanitize(tag_line),
            "region": args.region,
            "win_rate": stats.get("win_rate"),
            "kda": stats.get("kda"),
            "total_games": stats.get("total_games"),
        }
        print(json.dumps(record))
    else:
        safe_name = _sanitize(game_name)
        safe_tag = _sanitize(tag_line)
        print(_format_stats_output(stats, safe_name, safe_tag, puuid))
    return 0
