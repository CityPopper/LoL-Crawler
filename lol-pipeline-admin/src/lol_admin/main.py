"""Admin CLI — operational tooling for the LoL pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.constants import STREAM_DLQ_ARCHIVE, VALID_REPLAY_STREAMS
from lol_pipeline.log import get_logger
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from lol_pipeline.priority import PRIORITY_MANUAL_20, set_priority
from lol_pipeline.redis_client import get_redis
from lol_pipeline.resolve import resolve_puuid
from lol_pipeline.riot_api import PLATFORM_TO_REGION, RiotClient
from lol_pipeline.streams import ANALYZE_STREAM_MAXLEN, MATCH_ID_STREAM_MAXLEN, publish, replay_from_dlq
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

_log = get_logger("admin")

_STREAM_PUUID = "stream:puuid"
_STREAM_MATCH_ID = "stream:match_id"
_STREAM_PARSE = "stream:parse"
_STREAM_ANALYZE = "stream:analyze"
_STREAM_DLQ = "stream:dlq"
_DELAYED_MESSAGES = "delayed:messages"

_VALID_REPLAY_STREAMS = VALID_REPLAY_STREAMS

_DEFAULT_MAXLEN = 10_000


def _maxlen_for_stream(stream: str) -> int | None:
    """Return the MAXLEN to use when publishing to *stream*."""
    if stream == _STREAM_MATCH_ID:
        return MATCH_ID_STREAM_MAXLEN
    if stream == "stream:analyze":
        return ANALYZE_STREAM_MAXLEN
    return _DEFAULT_MAXLEN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _dlq_entries(r: aioredis.Redis) -> list[tuple[str, DLQEnvelope]]:
    """Return (stream_entry_id, DLQEnvelope) pairs from stream:dlq.

    Corrupt entries are logged and skipped instead of crashing the caller.
    """
    raw: list[Any] = await r.xrange(_STREAM_DLQ, "-", "+")
    result: list[tuple[str, DLQEnvelope]] = []
    for entry_id, fields in raw:
        try:
            result.append((entry_id, DLQEnvelope.from_redis_fields(fields)))
        except (KeyError, ValueError, TypeError) as exc:
            _log.warning("skipping corrupt DLQ entry %s: %s", entry_id, exc)
    return result


def _make_replay_envelope(dlq: DLQEnvelope, max_attempts: int) -> MessageEnvelope:
    original_type = dlq.original_stream.removeprefix("stream:")
    return MessageEnvelope(
        source_stream=dlq.original_stream,
        type=original_type,
        payload=dlq.payload,
        max_attempts=max_attempts,
        enqueued_at=dlq.enqueued_at,
        dlq_attempts=dlq.dlq_attempts,
        priority=dlq.priority,
    )


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


# Priority stat labels for display ordering
_STAT_PRIORITY = ["win_rate", "kda", "total_games"]
_STAT_LABELS: dict[str, str] = {
    "win_rate": "Win Rate",
    "kda": "KDA",
    "total_games": "Total Games",
    "total_kills": "Total Kills",
    "total_wins": "Total Wins",
    "total_deaths": "Total Deaths",
    "total_assists": "Total Assists",
    "kills": "Kills",
    "deaths": "Deaths",
    "assists": "Assists",
    "avg_kills": "Avg Kills",
    "avg_deaths": "Avg Deaths",
    "avg_assists": "Avg Assists",
    "wins": "Wins",
}


def _format_stat_value(key: str, value: str, stats: dict[str, str]) -> str:
    """Format a single stat value for display."""
    formatters: dict[str, str | None] = {
        "win_rate": "percent",
        "kda": "float2",
        "total_games": "int",
    }
    fmt = formatters.get(key)
    if fmt is None:
        return value
    try:
        f = float(value)
        if fmt == "int":
            return str(int(f))
        if not math.isfinite(f):
            return value
        if fmt == "percent":
            return f"{f * 100:.1f}%  ({stats.get('total_games', '?')} games)"
        return f"{f:.2f}"
    except ValueError:
        return value


def _format_stats_output(
    stats: dict[str, str],
    game_name: str,
    tag_line: str,
    puuid: str,
) -> str:
    """Format player stats as a human-readable block with aligned values."""
    rule = "\u2500" * 36
    lines: list[str] = [
        f"Player: {game_name}#{tag_line}  [{puuid[:8]}\u2026]",
        rule,
    ]

    # Priority keys first, then remaining alphabetically
    ordered_keys: list[str] = [k for k in _STAT_PRIORITY if k in stats]
    remaining = sorted(k for k in stats if k not in _STAT_PRIORITY)
    ordered_keys.extend(remaining)

    for key in ordered_keys:
        label = _STAT_LABELS.get(key, key)
        value = _format_stat_value(key, stats[key], stats)
        lines.append(f"  {label:<18}{value}")

    lines.append(rule)
    return "\n".join(lines)


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


def _format_dlq_table(entries: list[tuple[str, DLQEnvelope]]) -> str:
    """Format DLQ entries as a human-readable table."""
    hdr = (
        f"{'Entry ID':<18}\u2502 {'Stream':<16}\u2502 {'Code':<14}"
        f"\u2502 {'Attempts':>8} \u2502 {'Age':<10}"
    )
    sep = (
        f"{'\u2500' * 18}\u253c{'\u2500' * 17}\u253c{'\u2500' * 15}"
        f"\u253c{'\u2500' * 10}\u253c{'\u2500' * 10}"
    )
    rows: list[str] = [hdr, sep]
    for entry_id, dlq in entries:
        stream = dlq.original_stream[:15]
        age = _relative_age(dlq.enqueued_at)
        attempts = f"{dlq.dlq_attempts} dlq"
        rows.append(
            f"{entry_id:<18}\u2502 {stream:<16}\u2502 {dlq.failure_code:<14}"
            f"\u2502 {attempts:>8} \u2502 {age:<10}"
        )
    return "\n".join(rows)


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


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------


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


async def cmd_system_halt(r: aioredis.Redis, args: argparse.Namespace) -> int:
    if not _confirm("Are you sure you want to halt the pipeline? [y/N]: ", args):
        _print_info("aborted")
        return 1
    await r.set("system:halted", "1")
    _print_ok("system halted \u2014 all consumers will stop on next message")
    return 0


async def cmd_system_resume(r: aioredis.Redis, args: argparse.Namespace) -> int:
    await r.delete("system:halted")
    _print_ok("system resumed \u2014 system:halted cleared")
    return 0


async def cmd_dlq_list(r: aioredis.Redis, args: argparse.Namespace) -> int:
    entries = await _dlq_entries(r)
    if not entries:
        _print_info("DLQ is empty — nothing to display")
        return 0
    if getattr(args, "json", False):
        for entry_id, dlq in entries:
            record = {
                "entry_id": entry_id,
                "id": dlq.id,
                "failure_code": dlq.failure_code,
                "original_stream": dlq.original_stream,
                "failed_by": dlq.failed_by,
                "attempts": dlq.attempts,
                "dlq_attempts": dlq.dlq_attempts,
                "enqueued_at": dlq.enqueued_at,
            }
            print(json.dumps(record))
    else:
        print(_format_dlq_table(entries))
    return 0


async def cmd_dlq_replay(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    if not args.all and not args.id:
        _print_error("specify a message ID or --all")
        return 1
    entries = await _dlq_entries(r)
    if not entries:
        _print_info("DLQ is empty — nothing to replay")
        return 0
    targets = entries if args.all else [(e, d) for e, d in entries if e == args.id]
    if not targets:
        _print_error(f"entry not found: {args.id}")
        return 1
    for entry_id, dlq in targets:
        if dlq.original_stream not in _VALID_REPLAY_STREAMS:
            _print_error(
                f"refusing to replay {entry_id}: "
                f"original_stream {dlq.original_stream!r} is not a valid pipeline stream"
            )
            continue
        envelope = _make_replay_envelope(dlq, cfg.max_attempts)
        await replay_from_dlq(r, entry_id, dlq.original_stream, envelope)
        _print_ok(f"replayed {entry_id} \u2192 {dlq.original_stream}")
    return 0


async def cmd_dlq_clear(r: aioredis.Redis, args: argparse.Namespace) -> int:
    if not args.all:
        _print_error("--all is required")
        return 1
    if not _confirm("Are you sure you want to clear all DLQ entries? [y/N]: ", args):
        _print_info("aborted")
        return 1
    entries = await _dlq_entries(r)
    if not entries:
        _print_info("DLQ is empty — nothing to clear")
        return 0
    ids = [e for e, _ in entries]
    await r.xdel(_STREAM_DLQ, *ids)
    _print_ok(f"cleared {len(ids)} entries from {_STREAM_DLQ}")
    return 0


async def cmd_replay_parse(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    if not args.all:
        _print_error("--all is required")
        return 1
    match_ids: set[str] = await r.smembers("match:status:parsed")  # type: ignore[misc]
    if not match_ids:
        _print_info("No parsed matches in match:status:parsed")
        return 0
    for match_id in match_ids:
        region = _region_from_match_id(match_id)
        envelope = MessageEnvelope(
            source_stream=_STREAM_PARSE,
            type="parse",
            payload={"match_id": match_id, "region": region},
            max_attempts=cfg.max_attempts,
        )
        ml = _maxlen_for_stream(_STREAM_PARSE) or _DEFAULT_MAXLEN
        await r.xadd(_STREAM_PARSE, envelope.to_redis_fields(), maxlen=ml, approximate=True)  # type: ignore[arg-type]
    _print_ok(f"replayed {len(match_ids)} entries to {_STREAM_PARSE}")
    return 0


async def cmd_replay_fetch(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    match_id: str = args.match_id
    region = _region_from_match_id(match_id)
    envelope = MessageEnvelope(
        source_stream=_STREAM_MATCH_ID,
        type="match_id",
        payload={"match_id": match_id, "region": region},
        max_attempts=cfg.max_attempts,
    )
    kwargs: dict[str, Any] = {}
    ml = _maxlen_for_stream(_STREAM_MATCH_ID)
    if ml is not None:
        kwargs["maxlen"] = ml
        kwargs["approximate"] = True
    await r.xadd(_STREAM_MATCH_ID, envelope.to_redis_fields(), **kwargs)  # type: ignore[arg-type]
    _print_ok(f"enqueued {match_id} \u2192 {_STREAM_MATCH_ID}")
    return 0


async def cmd_recalc_priority(r: aioredis.Redis, args: argparse.Namespace) -> int:
    """Diagnostic: scan player:priority:* keys and report the count.

    Since priority detection now uses SCAN (``has_priority_players``), there is
    no counter to repair.  This command is purely informational.
    """
    count = 0
    async for _key in r.scan_iter(match="player:priority:*", count=100):
        count += 1
    _print_ok(f"player:priority:* keys found: {count}  (read-only diagnostic — no changes made)")
    return 0


async def cmd_recalc_players(r: aioredis.Redis, args: argparse.Namespace) -> int:
    """Rebuild players:all sorted set from existing player:{puuid} hashes.

    One-time migration/repair tool. Uses SCAN to find all player:{puuid} keys,
    reads seeded_at from each, and populates the players:all ZSET.
    """
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
    _print_ok(f"players:all rebuilt — {count} players indexed")
    return 0


async def cmd_reseed(
    r: aioredis.Redis, riot: RiotClient, cfg: Config, args: argparse.Namespace
) -> int:
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
    )
    await publish(r, _STREAM_ANALYZE, envelope, maxlen=ANALYZE_STREAM_MAXLEN)
    safe_rid = _sanitize(args.riot_id)
    _print_ok(f"deleted {deleted} keys for {safe_rid}; enqueued re-analysis")
    return 0


async def cmd_dlq_archive_list(r: aioredis.Redis, args: argparse.Namespace) -> int:
    """List entries in stream:dlq:archive."""
    raw: list[Any] = await r.xrange(STREAM_DLQ_ARCHIVE, "-", "+")
    if not raw:
        _print_info("DLQ archive is empty")
        return 0
    for entry_id, fields in raw:
        try:
            dlq = DLQEnvelope.from_redis_fields(fields)
            stream = dlq.original_stream[:15]
            _print_info(f"{entry_id}  {stream:<16}  {dlq.failure_code}")
        except (KeyError, ValueError, TypeError):
            code = fields.get("failure_code", "?")
            stream = fields.get("original_stream", "?")[:15]
            reason = fields.get("failure_reason", "?")
            _print_info(f"{entry_id}  {stream:<16}  {code}  ({reason})")
    _print_ok(f"{len(raw)} archive entries")
    return 0


async def cmd_dlq_archive_clear(r: aioredis.Redis, args: argparse.Namespace) -> int:
    """Clear all entries from stream:dlq:archive."""
    if not args.all:
        _print_error("--all is required")
        return 1
    if not _confirm(
        "Are you sure you want to clear all DLQ archive entries? [y/N]: ", args
    ):
        _print_info("aborted")
        return 1
    length: int = await r.xlen(STREAM_DLQ_ARCHIVE)
    if length == 0:
        _print_info("DLQ archive is empty — nothing to clear")
        return 0
    await r.delete(STREAM_DLQ_ARCHIVE)
    _print_ok(f"cleared {length} entries from {STREAM_DLQ_ARCHIVE}")
    return 0


async def cmd_clear_priority(
    r: aioredis.Redis, riot: RiotClient, args: argparse.Namespace
) -> int:
    """Delete player:priority:* keys for a specific player or all players."""
    if not getattr(args, "all", False) and not getattr(args, "riot_id", None):
        _print_error("specify a Riot ID or --all")
        return 1
    if getattr(args, "all", False):
        keys = [key async for key in r.scan_iter(match="player:priority:*", count=100)]
        if keys:
            await r.delete(*keys)
        count = len(keys)
        _print_ok(f"deleted {count} player:priority:* keys")
        return 0
    # Single player
    puuid = await _resolve_puuid(riot, args.riot_id, args.region, r)
    if puuid is None:
        return 1
    deleted: int = await r.delete(f"player:priority:{puuid}")
    safe_rid = _sanitize(args.riot_id)
    if deleted:
        _print_ok(f"deleted player:priority:{puuid}")
    else:
        _print_info(f"no priority key found for {safe_rid}")
    return 0


async def cmd_delayed_list(r: aioredis.Redis, args: argparse.Namespace) -> int:
    """Show entries in delayed:messages sorted set."""
    entries: list[tuple[str, float]] = await r.zrangebyscore(  # type: ignore[misc]
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
        if delta_ms <= 0:
            eta = "ready now"
        else:
            eta = f"in {delta_ms / 1000:.0f}s"
        _print_info(f"{truncated}  ready={ready_dt}  ({eta})")
    total: int = await r.zcard(_DELAYED_MESSAGES)
    _print_ok(f"showing {len(entries)} of {total} delayed messages")
    return 0


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


async def cmd_backfill_champions(
    r: aioredis.Redis, cfg: Config, args: argparse.Namespace
) -> int:
    """Reprocess all parsed ranked matches to populate champion stats."""
    done_key = "champion:backfill:done"
    parsed: set[str] = await r.smembers("match:status:parsed")  # type: ignore[misc]
    already_done: set[str] = await r.smembers(done_key)  # type: ignore[misc]
    todo = parsed - already_done
    if not todo:
        _print_info("No matches to backfill (all already processed)")
        return 0
    count = 0
    batch: list[str] = []
    for match_id in todo:
        batch.append(match_id)
        if len(batch) >= 100:
            processed = await _backfill_batch(r, batch)
            count += processed
            await r.sadd(done_key, *batch)
            batch = []
            _print_info(f"Progress: {count} matches backfilled...")
    if batch:
        processed = await _backfill_batch(r, batch)
        count += processed
        await r.sadd(done_key, *batch)
    await r.expire(done_key, 90 * 86400)
    _print_ok(f"Backfilled champion stats from {count} ranked matches")
    return 0


async def _backfill_batch(r: aioredis.Redis, match_ids: list[str]) -> int:
    """Process a batch of matches for champion stats backfill.

    Returns count of ranked matches processed.
    """
    async with r.pipeline(transaction=False) as pipe:
        for mid in match_ids:
            pipe.hgetall(f"match:{mid}")
        metadata_list: list[dict[str, str]] = await pipe.execute()
    count = 0
    ttl = 90 * 86400  # 90 days
    for match_id, meta in zip(match_ids, metadata_list, strict=True):
        if not meta or meta.get("queue_id") != "420":
            continue
        patch = meta.get("patch", "")
        if not patch:
            continue
        participant_keys: list[str] = []
        async for key in r.scan_iter(
            match=f"participant:{match_id}:*", count=20
        ):
            participant_keys.append(key)
        if not participant_keys:
            continue
        async with r.pipeline(transaction=False) as pipe:
            for key in participant_keys:
                pipe.hgetall(key)
            participants: list[dict[str, str]] = await pipe.execute()
        for p in participants:
            if not p:
                continue
            team_position = p.get("team_position", "")
            champion_name = p.get("champion_name", "")
            if not team_position or not champion_name:
                continue
            stats_key = f"champion:stats:{champion_name}:{patch}:{team_position}"
            index_key = f"champion:index:{patch}"
            index_member = f"{champion_name}:{team_position}"
            game_start = meta.get("game_start", "0")
            await r.eval(
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
        count += 1
    return count


async def cmd_delayed_flush(r: aioredis.Redis, args: argparse.Namespace) -> int:
    """Remove all members from delayed:messages."""
    if not args.all:
        _print_error("--all is required")
        return 1
    if not _confirm(
        "Are you sure you want to flush all delayed messages? [y/N]: ", args
    ):
        _print_info("aborted")
        return 1
    count: int = await r.zcard(_DELAYED_MESSAGES)
    if count == 0:
        _print_info("delayed:messages is empty — nothing to flush")
        return 0
    await r.delete(_DELAYED_MESSAGES)
    _print_ok(f"flushed {count} entries from {_DELAYED_MESSAGES}")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lol_admin", description="LoL pipeline admin CLI")
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="output results as JSON (supported: stats, dlq list)",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        default=False,
        help="skip confirmation prompts (for scripting)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("stats", help="show player stats")
    p.add_argument("riot_id", metavar="GameName#TagLine")
    p.add_argument("--region", default="na1")

    sub.add_parser("system-halt", help="set system:halted=1 (stops all consumers)")
    sub.add_parser("system-resume", help="clear system:halted")

    p_dlq = sub.add_parser("dlq", help="DLQ operations")
    dlq_sub = p_dlq.add_subparsers(dest="dlq_command", required=True)
    dlq_sub.add_parser("list", help="list DLQ entries")

    p_replay = dlq_sub.add_parser("replay", help="replay DLQ entries to source stream")
    p_replay.add_argument("id", nargs="?", help="stream entry ID to replay")
    p_replay.add_argument("--all", action="store_true", help="replay all entries")

    p_clear = dlq_sub.add_parser("clear", help="delete DLQ entries")
    p_clear.add_argument("--all", action="store_true", required=True)

    # DLQ archive subcommands
    p_archive = dlq_sub.add_parser("archive", help="DLQ archive operations")
    archive_sub = p_archive.add_subparsers(dest="archive_command", required=True)
    archive_sub.add_parser("list", help="list DLQ archive entries")
    p_archive_clear = archive_sub.add_parser("clear", help="clear DLQ archive")
    p_archive_clear.add_argument("--all", action="store_true", required=True)

    p_rp = sub.add_parser("replay-parse", help="re-enqueue parsed matches to stream:parse")
    p_rp.add_argument("--all", action="store_true", required=True)

    p_rf = sub.add_parser("replay-fetch", help="re-enqueue a match_id to stream:match_id")
    p_rf.add_argument("match_id")

    p_rs = sub.add_parser("reseed", help="clear cooldown and re-enqueue player to stream:puuid")
    p_rs.add_argument("riot_id", metavar="GameName#TagLine")
    p_rs.add_argument("--region", default="na1")

    p_reset = sub.add_parser("reset-stats", help="wipe player stats and re-trigger analysis")
    p_reset.add_argument("riot_id", metavar="GameName#TagLine")
    p_reset.add_argument("--region", default="na1")

    p_cp = sub.add_parser("clear-priority", help="delete player:priority:* keys")
    p_cp.add_argument("riot_id", nargs="?", metavar="GameName#TagLine")
    p_cp.add_argument("--all", action="store_true", help="clear all priority keys")
    p_cp.add_argument("--region", default="na1")

    sub.add_parser(
        "recalc-priority",
        help="diagnostic: count player:priority:* keys (read-only)",
    )

    sub.add_parser(
        "recalc-players",
        help="rebuild players:all sorted set from existing player:{puuid} hashes",
    )

    sub.add_parser("delayed-list", help="show entries in delayed:messages sorted set")

    p_df = sub.add_parser("delayed-flush", help="remove all delayed messages")
    p_df.add_argument("--all", action="store_true", required=True)

    sub.add_parser(
        "backfill-champions",
        help="reprocess parsed matches to populate champion stats",
    )

    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_DLQ_ARCHIVE_DISPATCH = {
    "list": lambda r, cfg, args: cmd_dlq_archive_list(r, args),
    "clear": lambda r, cfg, args: cmd_dlq_archive_clear(r, args),
}

_DLQ_DISPATCH = {
    "list": lambda r, cfg, args: cmd_dlq_list(r, args),
    "replay": cmd_dlq_replay,
    "clear": lambda r, cfg, args: cmd_dlq_clear(r, args),
    "archive": lambda r, cfg, args: _dispatch_dlq_archive(r, cfg, args),
}

_CMD_DISPATCH = {
    "stats": cmd_stats,
    "system-halt": lambda r, riot, cfg, args: cmd_system_halt(r, args),
    "system-resume": lambda r, riot, cfg, args: cmd_system_resume(r, args),
    "dlq": lambda r, riot, cfg, args: _dispatch_dlq(r, cfg, args),
    "replay-parse": lambda r, riot, cfg, args: cmd_replay_parse(r, cfg, args),
    "replay-fetch": lambda r, riot, cfg, args: cmd_replay_fetch(r, cfg, args),
    "reseed": cmd_reseed,
    "reset-stats": cmd_reset_stats,
    "clear-priority": lambda r, riot, cfg, args: cmd_clear_priority(r, riot, args),
    "recalc-priority": lambda r, riot, cfg, args: cmd_recalc_priority(r, args),
    "recalc-players": lambda r, riot, cfg, args: cmd_recalc_players(r, args),
    "delayed-list": lambda r, riot, cfg, args: cmd_delayed_list(r, args),
    "delayed-flush": lambda r, riot, cfg, args: cmd_delayed_flush(r, args),
    "backfill-champions": lambda r, riot, cfg, args: cmd_backfill_champions(r, cfg, args),
}


async def _dispatch(
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    args: argparse.Namespace,
) -> int:
    handler = _CMD_DISPATCH.get(args.command)
    if handler is None:
        raise AssertionError(f"unreachable command: {args.command}")
    return await handler(r, riot, cfg, args)  # type: ignore[no-untyped-call]


async def _dispatch_dlq(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    handler = _DLQ_DISPATCH.get(args.dlq_command)
    if handler is None:
        raise AssertionError(f"unreachable dlq subcommand: {args.dlq_command}")
    return await handler(r, cfg, args)  # type: ignore[no-untyped-call]


async def _dispatch_dlq_archive(
    r: aioredis.Redis, cfg: Config, args: argparse.Namespace
) -> int:
    handler = _DLQ_ARCHIVE_DISPATCH.get(args.archive_command)
    if handler is None:
        raise AssertionError(f"unreachable archive subcommand: {args.archive_command}")
    return await handler(r, cfg, args)  # type: ignore[no-untyped-call]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main(argv: list[str]) -> int:
    """Parse argv and run the requested admin command."""
    parser = _build_parser()
    args = parser.parse_args(argv[1:])

    cfg = Config()
    try:
        r = get_redis(cfg.redis_url)
        riot = RiotClient(cfg.riot_api_key)
    except (RedisConnectionError, RedisError) as exc:
        _print_error(f"Cannot connect to Redis. Is the stack running? Try: just up ({exc})")
        return 1

    try:
        return await _dispatch(r, riot, cfg, args)
    except (RedisConnectionError, RedisError) as exc:
        _print_error("Cannot connect to Redis. Is the stack running? Try: just up")
        _log.debug("Redis connection error: %s", exc)
        return 1
    finally:
        await r.aclose()
        await riot.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
