"""Admin CLI — operational tooling for the LoL pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import PLATFORM_TO_REGION, NotFoundError, RiotClient

_log = get_logger("admin")

_STREAM_PUUID = "stream:puuid"
_STREAM_MATCH_ID = "stream:match_id"
_STREAM_PARSE = "stream:parse"
_STREAM_DLQ = "stream:dlq"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _dlq_entries(r: aioredis.Redis) -> list[tuple[str, DLQEnvelope]]:
    """Return (stream_entry_id, DLQEnvelope) pairs from stream:dlq."""
    raw: list[Any] = await r.xrange(_STREAM_DLQ, "-", "+")
    result: list[tuple[str, DLQEnvelope]] = []
    for entry_id, fields in raw:
        result.append((entry_id, DLQEnvelope.from_redis_fields(fields)))
    return result


def _make_replay_envelope(dlq: DLQEnvelope, max_attempts: int) -> MessageEnvelope:
    original_type = dlq.original_stream.removeprefix("stream:")
    return MessageEnvelope(
        source_stream=dlq.original_stream,
        type=original_type,
        payload=dlq.payload,
        max_attempts=max_attempts,
    )


def _region_from_match_id(match_id: str) -> str:
    prefix = match_id.split("_")[0].lower()
    return prefix if prefix in PLATFORM_TO_REGION else "na1"


def _name_cache_key(game_name: str, tag_line: str) -> str:
    return f"player:name:{game_name.lower()}#{tag_line.lower()}"


async def _resolve_puuid(
    riot: RiotClient, riot_id: str, region: str, r: aioredis.Redis | None = None,
) -> str | None:
    if "#" not in riot_id:
        _log.error("invalid Riot ID — expected GameName#TagLine", extra={"riot_id": riot_id})
        return None
    game_name, tag_line = riot_id.split("#", 1)
    if r is not None:
        cached: str | None = await r.get(_name_cache_key(game_name, tag_line))
        if cached:
            return cached
    try:
        account = await riot.get_account_by_riot_id(game_name, tag_line, region)
        puuid = str(account["puuid"])
        if r is not None:
            await r.set(_name_cache_key(game_name, tag_line), puuid)
        return puuid
    except NotFoundError:
        _log.error("player not found", extra={"riot_id": riot_id})
        return None


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
        _log.error("player not found in Redis (not yet analyzed)", extra={"riot_id": args.riot_id})
        return 1
    game_name, tag_line = args.riot_id.split("#", 1)
    print(f"Stats for {game_name}#{tag_line} ({puuid[:12]}...):")
    for key in sorted(stats):
        print(f"  {key}: {stats[key]}")
    return 0


async def cmd_system_resume(r: aioredis.Redis, args: argparse.Namespace) -> int:
    await r.delete("system:halted")
    print("system resumed — system:halted cleared")
    return 0


async def cmd_dlq_list(r: aioredis.Redis, args: argparse.Namespace) -> int:
    entries = await _dlq_entries(r)
    if not entries:
        print("(empty)")
        return 0
    for entry_id, dlq in entries:
        record = {
            "entry_id": entry_id,
            "id": dlq.id,
            "failure_code": dlq.failure_code,
            "source_stream": dlq.source_stream,
            "attempts": dlq.attempts,
            "dlq_attempts": dlq.dlq_attempts,
            "enqueued_at": dlq.enqueued_at,
        }
        print(json.dumps(record))
    return 0


async def cmd_dlq_replay(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    entries = await _dlq_entries(r)
    if not entries:
        print("(empty)")
        return 0
    targets = entries if args.all else [(e, d) for e, d in entries if e == args.id]
    if not targets:
        _log.error("entry not found", extra={"id": args.id})
        return 1
    for entry_id, dlq in targets:
        envelope = _make_replay_envelope(dlq, cfg.max_attempts)
        await r.xadd(dlq.original_stream, envelope.to_redis_fields())  # type: ignore[arg-type]
        await r.xdel(_STREAM_DLQ, entry_id)
        print(f"replayed {entry_id} → {dlq.original_stream}")
    return 0


async def cmd_dlq_clear(r: aioredis.Redis, args: argparse.Namespace) -> int:
    if not args.all:
        _log.error("--all is required")
        return 1
    entries = await _dlq_entries(r)
    if not entries:
        print("(empty)")
        return 0
    ids = [e for e, _ in entries]
    await r.xdel(_STREAM_DLQ, *ids)
    print(f"cleared {len(ids)} entries from {_STREAM_DLQ}")
    return 0


async def cmd_replay_parse(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    if not args.all:
        _log.error("--all is required")
        return 1
    match_ids: set[str] = await r.smembers("match:status:parsed")  # type: ignore[misc]
    if not match_ids:
        print("(no parsed matches in match:status:parsed)")
        return 0
    for match_id in match_ids:
        region = _region_from_match_id(match_id)
        envelope = MessageEnvelope(
            source_stream=_STREAM_PARSE,
            type="parse",
            payload={"match_id": match_id, "region": region},
            max_attempts=cfg.max_attempts,
        )
        await r.xadd(_STREAM_PARSE, envelope.to_redis_fields())  # type: ignore[arg-type]
    print(f"replayed {len(match_ids)} entries to {_STREAM_PARSE}")
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
    await r.xadd(_STREAM_MATCH_ID, envelope.to_redis_fields())  # type: ignore[arg-type]
    print(f"enqueued {match_id} → {_STREAM_MATCH_ID}")
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

    # Publish directly to stream:puuid
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
    )
    entry_id = await r.xadd(_STREAM_PUUID, envelope.to_redis_fields())  # type: ignore[arg-type]
    print(f"reseeded {args.riot_id} → {_STREAM_PUUID} ({entry_id})")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lol_admin", description="LoL pipeline admin CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("stats", help="show player stats")
    p.add_argument("riot_id", metavar="GameName#TagLine")
    p.add_argument("--region", default="na1")

    sub.add_parser("system-resume", help="clear system:halted")

    p_dlq = sub.add_parser("dlq", help="DLQ operations")
    dlq_sub = p_dlq.add_subparsers(dest="dlq_command", required=True)
    dlq_sub.add_parser("list", help="list DLQ entries")

    p_replay = dlq_sub.add_parser("replay", help="replay DLQ entries to source stream")
    p_replay.add_argument("id", nargs="?", help="stream entry ID to replay")
    p_replay.add_argument("--all", action="store_true", help="replay all entries")

    p_clear = dlq_sub.add_parser("clear", help="delete DLQ entries")
    p_clear.add_argument("--all", action="store_true", required=True)

    p_rp = sub.add_parser("replay-parse", help="re-enqueue parsed matches to stream:parse")
    p_rp.add_argument("--all", action="store_true", required=True)

    p_rf = sub.add_parser("replay-fetch", help="re-enqueue a match_id to stream:match_id")
    p_rf.add_argument("match_id")

    p_rs = sub.add_parser("reseed", help="clear cooldown and re-enqueue player to stream:puuid")
    p_rs.add_argument("riot_id", metavar="GameName#TagLine")
    p_rs.add_argument("--region", default="na1")

    return parser


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def _dispatch(
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    args: argparse.Namespace,
) -> int:
    if args.command == "stats":
        return await cmd_stats(r, riot, cfg, args)
    if args.command == "system-resume":
        return await cmd_system_resume(r, args)
    if args.command == "dlq":
        return await _dispatch_dlq(r, cfg, args)
    if args.command == "replay-parse":
        return await cmd_replay_parse(r, cfg, args)
    if args.command == "replay-fetch":
        return await cmd_replay_fetch(r, cfg, args)
    if args.command == "reseed":
        return await cmd_reseed(r, riot, cfg, args)
    raise AssertionError(f"unreachable command: {args.command}")


async def _dispatch_dlq(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    if args.dlq_command == "list":
        return await cmd_dlq_list(r, args)
    if args.dlq_command == "replay":
        return await cmd_dlq_replay(r, cfg, args)
    if args.dlq_command == "clear":
        return await cmd_dlq_clear(r, args)
    raise AssertionError(f"unreachable dlq subcommand: {args.dlq_command}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main(argv: list[str]) -> int:
    """Parse argv and run the requested admin command."""
    parser = _build_parser()
    args = parser.parse_args(argv[1:])

    cfg = Config()
    r = get_redis(cfg.redis_url)
    riot = RiotClient(cfg.riot_api_key)

    try:
        return await _dispatch(r, riot, cfg, args)
    finally:
        await r.aclose()
        await riot.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
