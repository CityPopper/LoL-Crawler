"""Shared helpers for admin CLI commands."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.models import DLQEnvelope, MessageEnvelope, make_replay_envelope
from lol_pipeline.resolve import resolve_puuid
from lol_pipeline.riot_api import PLATFORM_TO_REGION, RiotClient
from lol_pipeline.streams import maxlen_for_stream

from lol_admin._constants import (
    _STREAM_DLQ,
)

_log: Any = None


def _get_log() -> Any:
    global _log
    if _log is None:
        from lol_pipeline.log import get_logger

        _log = get_logger("admin")
    return _log


def _maxlen_for_stream(stream: str) -> int | None:
    """Return the MAXLEN to use when publishing to *stream*."""
    return maxlen_for_stream(stream)


async def _dlq_entries(r: aioredis.Redis) -> list[tuple[str, DLQEnvelope]]:
    """Return (stream_entry_id, DLQEnvelope) pairs from stream:dlq.

    Corrupt entries are logged and skipped instead of crashing the caller.
    """
    log = _get_log()
    raw: list[Any] = await r.xrange(_STREAM_DLQ, "-", "+")
    result: list[tuple[str, DLQEnvelope]] = []
    for entry_id, fields in raw:
        try:
            result.append((entry_id, DLQEnvelope.from_redis_fields(fields)))
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("skipping corrupt DLQ entry %s: %s", entry_id, exc)
    return result


_make_replay_envelope = make_replay_envelope


def _region_from_match_id(match_id: str) -> str:
    prefix = match_id.split("_")[0].lower()
    return prefix if prefix in PLATFORM_TO_REGION else "na1"


def _sanitize(value: str) -> str:
    """Strip control characters (ANSI escapes, etc.) from user-supplied strings."""
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", value)


def _print_ok(msg: str) -> None:
    """Print a success message with [OK] prefix to stdout."""
    print(f"[OK] {msg}")


def _print_error(msg: str) -> None:
    """Print an error message with [ERROR] prefix to stderr."""
    print(f"[ERROR] {msg}", file=sys.stderr)


def _print_info(msg: str) -> None:
    """Print a neutral informational message."""
    print(f"[--] {msg}")


def _confirm(prompt: str, args: argparse.Namespace) -> bool:
    """Return True if the user confirms a destructive operation.

    Skips the prompt and returns True when ``args.yes`` is set (``-y`` flag).
    """
    if getattr(args, "yes", False):
        return True
    answer = input(prompt).strip().lower()
    return answer in ("y", "yes")


def _relative_age(iso_time: str) -> str:
    """Compute a human-readable relative age from an ISO timestamp."""
    try:
        then = datetime.fromisoformat(iso_time)
        now = datetime.now(tz=UTC)
        delta = now - then
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return "future"
        if total_seconds < 60:
            return f"{total_seconds}s ago"
        if total_seconds < 3600:
            return f"{total_seconds // 60}m ago"
        if total_seconds < 86400:
            return f"{total_seconds // 3600}h ago"
        return f"{total_seconds // 86400}d ago"
    except Exception:
        return "?"


async def _resolve_puuid(
    riot: RiotClient,
    riot_id: str,
    region: str,
    r: aioredis.Redis | None = None,
) -> str | None:
    safe_riot_id = _sanitize(riot_id)
    if "#" not in riot_id:
        _print_error(f"invalid Riot ID \u2014 expected GameName#TagLine: {safe_riot_id}")
        return None
    game_name, tag_line = riot_id.split("#", 1)
    if r is None:
        from lol_pipeline.riot_api import NotFoundError

        try:
            account = await riot.get_account_by_riot_id(game_name, tag_line, region)
            return str(account["puuid"])
        except NotFoundError:
            _print_error(f"player not found: {safe_riot_id}")
            return None
    return await resolve_puuid(r, riot, game_name, tag_line, region, _get_log())
