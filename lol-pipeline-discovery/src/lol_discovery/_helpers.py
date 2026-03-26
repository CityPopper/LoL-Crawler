"""Discovery helpers — pure utility functions extracted from main.py."""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError


def _parse_member(member: str, *, default_region: str) -> tuple[str, str]:
    """Split 'puuid:region' member into (puuid, region). Region has no colons."""
    idx = member.rfind(":")
    if idx == -1:
        return member, default_region
    puuid, region = member[:idx], member[idx + 1 :]
    if not puuid:
        return member, default_region
    return puuid, region


async def _xinfo_groups_safe(r: aioredis.Redis, stream: str) -> list[Any] | None:
    """Fetch XINFO GROUPS for a stream, returning None if the stream does not exist."""
    try:
        result: list[Any] = await r.xinfo_groups(stream)
        return result
    except ResponseError as exc:
        exc_str = str(exc)
        if "NOGROUP" not in exc_str and "no such key" not in exc_str:
            raise
        return None  # stream does not exist yet — idle for this stream


def _should_skip_seeded(
    recrawl_after: str | None,
    now: float,
) -> bool | None:
    """Decide whether to skip a seeded player.

    Returns True to skip (remove from queue), False to skip (not yet due),
    and None to allow re-promotion (recrawl_after has passed).
    """
    if not recrawl_after:
        return True  # no recrawl scheduled -- skip
    try:
        if float(recrawl_after) > now:
            return False  # not yet due — skip but keep in queue
    except (ValueError, TypeError):
        pass
    return None  # recrawl_after has passed -- allow
