"""Admin CLI — operational tooling for the LoL pipeline.

Thin entry point: CLI parser + dispatch. Command implementations live in
``cmd_*.py`` modules; shared helpers in ``_helpers.py`` and ``_formatting.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import redis.asyncio as aioredis
from lol_pipeline.config import Config

# Re-export get_redis so mocks in tests still patch "lol_admin.main.get_redis"
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import RiotClient
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from lol_admin._formatting import (  # noqa: F401
    _format_dlq_table,
    _format_stat_value,
    _format_stats_output,
)

# Re-export everything tests import from main --------------------------------
# This shim keeps ``from lol_admin.main import X`` working for every symbol
# that existing tests reference.  New code should import from the module that
# owns the symbol.
from lol_admin._helpers import (  # noqa: F401
    _confirm,
    _dlq_entries,
    _get_log,
    _make_replay_envelope,
    _maxlen_for_stream,
    _print_error,
    _region_from_match_id,
    _resolve_puuid,
    _sanitize,
)
from lol_admin.cmd_backfill import cmd_backfill_champions
from lol_admin.cmd_delayed import cmd_delayed_flush, cmd_delayed_list
from lol_admin.cmd_dlq import (
    cmd_dlq_archive_clear,
    cmd_dlq_archive_list,
    cmd_dlq_clear,
    cmd_dlq_list,
    cmd_dlq_replay,
)
from lol_admin.cmd_player import (
    cmd_clear_priority,
    cmd_recalc_players,
    cmd_recalc_priority,
    cmd_reseed,
    cmd_reset_stats,
)
from lol_admin.cmd_replay import cmd_replay_fetch, cmd_replay_parse
from lol_admin.cmd_stats import cmd_stats
from lol_admin.cmd_system import cmd_system_halt, cmd_system_resume

_log = _get_log()


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
        "--yes",
        "-y",
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

    p_rp2 = sub.add_parser(
        "recalc-priority",
        help="diagnostic: count player:priority:* keys (read-only)",
    )
    p_rp2.add_argument("--json", action="store_true", default=False)

    p_rpl = sub.add_parser(
        "recalc-players",
        help="rebuild players:all sorted set from existing player:{puuid} hashes",
    )
    p_rpl.add_argument("--json", action="store_true", default=False)

    p_dl = sub.add_parser("delayed-list", help="show entries in delayed:messages sorted set")
    p_dl.add_argument("--json", action="store_true", default=False)

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


async def _dispatch_dlq_archive(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    handler = _DLQ_ARCHIVE_DISPATCH.get(args.archive_command)
    if handler is None:
        raise AssertionError(f"unreachable archive subcommand: {args.archive_command}")
    return await handler(r, cfg, args)


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
