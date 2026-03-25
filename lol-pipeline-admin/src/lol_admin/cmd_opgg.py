"""Admin CLI: OP.GG status command."""

from __future__ import annotations

import argparse
import json

import redis.asyncio as aioredis
from lol_pipeline.config import Config

from lol_admin._helpers import _data_dir_size_mb, _print_info, _print_ok


async def cmd_opgg_status(
    r: aioredis.Redis,
    cfg: Config,
    args: argparse.Namespace,
) -> int:
    """Show OP.GG integration status: enabled flag, fetch count, data dir size."""
    enabled: bool = cfg.opgg_enabled
    fetch_count = int(await r.get("opgg:fetch_count") or 0)

    data_dir_size = _data_dir_size_mb(cfg.opgg_match_data_dir)

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
