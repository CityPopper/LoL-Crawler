"""Shared helpers for admin CLI commands."""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.log import get_logger
from lol_pipeline.models import DLQEnvelope, make_replay_envelope
from lol_pipeline.resolve import resolve_puuid
from lol_pipeline.riot_api import PLATFORM_TO_REGION, RiotClient
from lol_pipeline.streams import maxlen_for_stream

from lol_admin._constants import (
    _STREAM_DLQ,
)

_log = get_logger("admin")


def _get_log() -> Any:
    return _log


# PRIN-ADM-1: Removed _maxlen_for_stream wrapper — import maxlen_for_stream directly.
# PRIN-ADM-1: Removed _make_replay_envelope alias — import make_replay_envelope directly.
# Both are kept as re-exports for backward compatibility with tests importing from main.py.
_maxlen_for_stream = maxlen_for_stream
_make_replay_envelope = make_replay_envelope


async def _dlq_entries(r: aioredis.Redis, *, limit: int = 100) -> list[tuple[str, DLQEnvelope]]:
    """Return (stream_entry_id, DLQEnvelope) pairs from stream:dlq.

    Uses COUNT-limited XRANGE to avoid loading the entire stream into memory.
    Corrupt entries are logged and skipped instead of crashing the caller.
    """
    raw: list[Any] = await r.xrange(_STREAM_DLQ, "-", "+", count=limit)
    result: list[tuple[str, DLQEnvelope]] = []
    for entry_id, fields in raw:
        try:
            result.append((entry_id, DLQEnvelope.from_redis_fields(fields)))
        except (KeyError, ValueError, TypeError) as exc:
            _log.warning("skipping corrupt DLQ entry %s: %s", entry_id, exc)
    return result


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
    return await resolve_puuid(r, riot, game_name, tag_line, region, _log)


def _data_dir_size_mb(data_dir: str) -> float:
    """Compute the total size of *data_dir* in megabytes.

    Returns 0.0 if the directory does not exist.
    """
    if not data_dir or not os.path.isdir(data_dir):
        return 0.0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(data_dir):
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            with contextlib.suppress(OSError):
                total += os.path.getsize(fpath)
    return round(total / (1024 * 1024), 2)


async def _scan_priority_keys(r: aioredis.Redis) -> list[str]:
    """Collect all player:priority:* keys via SCAN.

    Shared by cmd_clear_priority (delete) and cmd_recalc_priority (count).
    """
    return [key async for key in r.scan_iter(match="player:priority:*", count=100)]


async def _scan_parsed_matches(r: aioredis.Redis) -> set[str]:
    """Collect match IDs whose per-match hash has status=parsed (RDB-2).

    Shared by cmd_replay and cmd_backfill (PRIN-ADM-1: DRY extraction).
    """
    result: set[str] = set()
    async for key in r.scan_iter(match="match:*", count=200):
        key_str: str = key
        # Skip non-match keys (match:participants:*, match:status:*)
        if key_str.count(":") != 1:
            continue
        status = await r.hget(key_str, "status")  # type: ignore[misc]
        if status == "parsed":
            result.add(key_str.removeprefix("match:"))
    return result


# ---------------------------------------------------------------------------
# Backfill champion stats helpers (PRIN-ADM-03)
# ---------------------------------------------------------------------------

_UPDATE_CHAMPION_LUA = """
local stats_key = KEYS[1]
local index_key = KEYS[2]
local patch_list_key = KEYS[3]
local win     = tonumber(ARGV[1])
local kills   = tonumber(ARGV[2])
local deaths  = tonumber(ARGV[3])
local assists  = tonumber(ARGV[4])
local gold     = tonumber(ARGV[5])
local cs       = tonumber(ARGV[6])
local damage   = tonumber(ARGV[7])
local vision   = tonumber(ARGV[8])
local index_member = ARGV[9]
local game_start   = tonumber(ARGV[10])
local patch        = ARGV[11]
local ttl          = tonumber(ARGV[12])
local double_kills = tonumber(ARGV[13])
local triple_kills = tonumber(ARGV[14])
local quadra_kills = tonumber(ARGV[15])
local penta_kills  = tonumber(ARGV[16])

redis.call('HINCRBY', stats_key, 'games', 1)
redis.call('HINCRBY', stats_key, 'wins', win)
redis.call('HINCRBY', stats_key, 'kills', kills)
redis.call('HINCRBY', stats_key, 'deaths', deaths)
redis.call('HINCRBY', stats_key, 'assists', assists)
redis.call('HINCRBY', stats_key, 'gold', gold)
redis.call('HINCRBY', stats_key, 'cs', cs)
redis.call('HINCRBY', stats_key, 'damage', damage)
redis.call('HINCRBY', stats_key, 'vision', vision)
redis.call('HINCRBY', stats_key, 'double_kills', double_kills)
redis.call('HINCRBY', stats_key, 'triple_kills', triple_kills)
redis.call('HINCRBY', stats_key, 'quadra_kills', quadra_kills)
redis.call('HINCRBY', stats_key, 'penta_kills', penta_kills)
redis.call('EXPIRE', stats_key, ttl)

redis.call('ZINCRBY', index_key, 1, index_member)
redis.call('EXPIRE', index_key, ttl)

redis.call('ZADD', patch_list_key, 'NX', game_start, patch)
redis.call('EXPIRE', patch_list_key, ttl)
return 1
"""


async def _backfill_participant(
    r: aioredis.Redis,
    p: dict[str, str],
    patch: str,
    game_start: str,
    ttl: int,
) -> None:
    """Update champion stats for a single participant via Lua eval."""
    team_position = p.get("team_position", "")
    champion_name = p.get("champion_name", "")
    if not team_position or not champion_name:
        return
    stats_key = f"champion:stats:{champion_name}:{patch}:{team_position}"
    index_key = f"champion:index:{patch}"
    index_member = f"{champion_name}:{team_position}"
    await r.eval(  # type: ignore[misc]
        _UPDATE_CHAMPION_LUA,
        3,
        stats_key,
        index_key,
        "patch:list",
        int(p.get("win", "0")),
        int(p.get("kills", "0")),
        int(p.get("deaths", "0")),
        int(p.get("assists", "0")),
        int(p.get("gold_earned", "0")),
        int(p.get("total_minions_killed", "0")),
        int(p.get("total_damage_dealt_to_champions", "0")),
        int(p.get("vision_score", "0")),
        index_member,
        game_start,
        patch,
        ttl,
        int(p.get("double_kills", "0")),
        int(p.get("triple_kills", "0")),
        int(p.get("quadra_kills", "0")),
        int(p.get("penta_kills", "0")),
    )


async def _backfill_batch(r: aioredis.Redis, match_ids: list[str]) -> int:
    """Process a batch of matches for champion stats backfill.

    Returns count of ranked matches processed.
    """
    from lol_pipeline.constants import CHAMPION_STATS_TTL_SECONDS, RANKED_SOLO_QUEUE_ID

    async with r.pipeline(transaction=False) as pipe:
        for mid in match_ids:
            pipe.hgetall(f"match:{mid}")
        metadata_list: list[dict[str, str]] = await pipe.execute()
    count = 0
    ttl = CHAMPION_STATS_TTL_SECONDS
    for match_id, meta in zip(match_ids, metadata_list, strict=True):
        if not meta or meta.get("queue_id") != RANKED_SOLO_QUEUE_ID:
            continue
        patch = meta.get("patch", "")
        if not patch:
            continue
        participant_keys: list[str] = []
        async for key in r.scan_iter(match=f"participant:{match_id}:*", count=20):
            participant_keys.append(key)
        if not participant_keys:
            continue
        async with r.pipeline(transaction=False) as pipe:
            for key in participant_keys:
                pipe.hgetall(key)
            participants: list[dict[str, str]] = await pipe.execute()
        game_start = meta.get("game_start", "0")
        for p in participants:
            if p:
                await _backfill_participant(r, p, patch, game_start, ttl)
        count += 1
    return count


# ---------------------------------------------------------------------------
# DLQ archive helpers (PRIN-ADM-03)
# ---------------------------------------------------------------------------


async def _dlq_archive_entries(
    r: aioredis.Redis,
) -> list[tuple[str, dict[str, str]]]:
    """Return (stream_entry_id, fields) pairs from stream:dlq:archive.

    Similar to _dlq_entries but for the archive stream; returns raw field dicts
    because archive entries may be corrupt / partially formed.
    """
    from lol_pipeline.constants import STREAM_DLQ_ARCHIVE

    raw: list[Any] = await r.xrange(STREAM_DLQ_ARCHIVE, "-", "+")
    return [(entry_id, fields) for entry_id, fields in raw]
