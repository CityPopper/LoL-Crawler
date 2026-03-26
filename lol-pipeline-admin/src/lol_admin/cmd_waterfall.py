"""Admin CLI: waterfall-stats command."""

from __future__ import annotations

import argparse
import json

import redis.asyncio as aioredis

from lol_admin._helpers import _print_info, _print_ok


async def cmd_waterfall_stats(r: aioredis.Redis, args: argparse.Namespace) -> int:
    stats: dict[str, dict[str, str]] = {}
    async for key in r.scan_iter(match="source:stats:*", count=100):
        source_name = key.removeprefix("source:stats:")
        data: dict[str, str] = await r.hgetall(key)  # type: ignore[misc]
        stats[source_name] = data

    if not stats:
        _print_info("no source:stats:* keys found — fetcher has not recorded any stats yet")
        return 0

    if getattr(args, "json", False):
        for source_name in sorted(stats):
            record = {
                "source": source_name,
                "fetch_count": int(stats[source_name].get("fetch_count", "0")),
                "success_count": int(stats[source_name].get("success_count", "0")),
                "throttle_count": int(stats[source_name].get("throttle_count", "0")),
            }
            print(json.dumps(record))
        return 0

    # Column widths
    name_w = max(len(s) for s in stats)
    name_w = max(name_w, len("Source"))
    hdr = f"  {'Source':<{name_w}}  {'Fetches':>9}  {'Success':>9}  {'Throttled':>9}"
    sep = f"  {'-' * name_w}  {'-' * 9}  {'-' * 9}  {'-' * 9}"
    print(hdr)
    print(sep)
    for source_name in sorted(stats):
        d = stats[source_name]
        fetch = int(d.get("fetch_count", "0"))
        success = int(d.get("success_count", "0"))
        throttle = int(d.get("throttle_count", "0"))
        print(f"  {source_name:<{name_w}}  {fetch:>9}  {success:>9}  {throttle:>9}")

    total_fetches = sum(int(d.get("fetch_count", "0")) for d in stats.values())
    _print_ok(f"{len(stats)} sources, {total_fetches} total fetches")
    return 0
