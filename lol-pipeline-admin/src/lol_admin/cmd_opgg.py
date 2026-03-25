"""Admin CLI: OP.GG status command."""

from __future__ import annotations

import argparse
import json
import os

import redis.asyncio as aioredis
from lol_pipeline.config import Config

from lol_admin._helpers import _data_dir_size_mb, _print_info, _print_ok


async def cmd_opgg_status(
    r: aioredis.Redis,
    cfg: Config,
    args: argparse.Namespace,
) -> int:
    """Show OP.GG integration status: enabled flag, fetch count, data dir size."""
    enabled: bool = getattr(cfg, "opgg_enabled", False)
    fetch_count = int(await r.get("opgg:fetch_count") or 0)

    # Determine data directory — check opgg-specific, fall back to match_data_dir
    opgg_data_dir: str = getattr(cfg, "opgg_match_data_dir", "")
    if not opgg_data_dir:
        base = getattr(cfg, "match_data_dir", "")
        if base:
            opgg_data_dir = os.path.join(base, "opgg")

    data_dir_size = _data_dir_size_mb(opgg_data_dir)

    if getattr(args, "json", False):
        record = {
            "enabled": enabled,
            "fetch_count": fetch_count,
            "data_dir_size_mb": data_dir_size,
        }
        print(json.dumps(record))
    else:
        _print_ok(f"OP.GG enabled: {enabled}")
        _print_info(f"Fetch count: {fetch_count}")
        _print_info(f"Data dir size: {data_dir_size} MB")

    return 0
