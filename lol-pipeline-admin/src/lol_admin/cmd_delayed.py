"""Admin CLI: delayed-list and delayed-flush commands."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import redis.asyncio as aioredis

from lol_admin._constants import _DELAYED_MESSAGES
from lol_admin._helpers import _confirm, _print_error, _print_info, _print_ok


async def cmd_delayed_list(r: aioredis.Redis, args: argparse.Namespace) -> int:
    """Show entries in delayed:messages sorted set."""
    entries: list[tuple[str, float]] = await r.zrangebyscore(
        _DELAYED_MESSAGES, "-inf", "+inf", withscores=True, start=0, num=50
    )
    if not entries:
        _print_info("delayed:messages is empty")
        return 0
    now_ms = datetime.now(tz=UTC).timestamp() * 1000
    for member, score in entries:
        truncated = member[:80] + ("..." if len(member) > 80 else "")
        ready_dt = datetime.fromtimestamp(score / 1000, tz=UTC).isoformat()
        delta_ms = score - now_ms
        eta = "ready now" if delta_ms <= 0 else f"in {delta_ms / 1000:.0f}s"
        _print_info(f"{truncated}  ready={ready_dt}  ({eta})")
    total: int = await r.zcard(_DELAYED_MESSAGES)
    _print_ok(f"showing {len(entries)} of {total} delayed messages")
    return 0


async def cmd_delayed_flush(r: aioredis.Redis, args: argparse.Namespace) -> int:
    """Remove all members from delayed:messages."""
    if not args.all:
        _print_error("--all is required")
        return 1
    if not _confirm("Are you sure you want to flush all delayed messages? [y/N]: ", args):
        _print_info("aborted")
        return 1
    count: int = await r.zcard(_DELAYED_MESSAGES)
    if count == 0:
        _print_info("delayed:messages is empty — nothing to flush")
        return 0
    await r.delete(_DELAYED_MESSAGES)
    _print_ok(f"flushed {count} entries from {_DELAYED_MESSAGES}")
    return 0
