"""Admin CLI dispatch — routes commands to their handler functions."""

from __future__ import annotations

import argparse

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.riot_api import RiotClient

from lol_admin.cmd_backfill import cmd_backfill_champions
from lol_admin.cmd_delayed import cmd_delayed_flush, cmd_delayed_list
from lol_admin.cmd_dlq import (
    cmd_dlq_archive_clear,
    cmd_dlq_archive_list,
    cmd_dlq_clear,
    cmd_dlq_list,
    cmd_dlq_replay,
)
from lol_admin.cmd_opgg import cmd_opgg_status
from lol_admin.cmd_player_ops import (
    cmd_clear_priority,
    cmd_reseed,
    cmd_reset_stats,
)
from lol_admin.cmd_player_scans import (
    cmd_recalc_players,
    cmd_recalc_priority,
)
from lol_admin.cmd_replay import cmd_replay_fetch, cmd_replay_parse
from lol_admin.cmd_stats import cmd_stats
from lol_admin.cmd_system import cmd_system_halt, cmd_system_resume
from lol_admin.cmd_track import cmd_track

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

# IMP-022: Commands that require a RiotClient (Riot API calls).
_RIOT_COMMANDS: frozenset[str] = frozenset(
    {"stats", "track", "reseed", "reset-stats", "clear-priority"}
)

_CMD_DISPATCH = {
    "stats": cmd_stats,
    "system-halt": lambda r, riot, cfg, args: cmd_system_halt(r, args),
    "system-resume": lambda r, riot, cfg, args: cmd_system_resume(r, args),
    "dlq": lambda r, riot, cfg, args: _dispatch_dlq(r, cfg, args),
    "replay-parse": lambda r, riot, cfg, args: cmd_replay_parse(r, cfg, args),
    "replay-fetch": lambda r, riot, cfg, args: cmd_replay_fetch(r, cfg, args),
    "track": cmd_track,
    "reseed": cmd_reseed,
    "reset-stats": cmd_reset_stats,
    "clear-priority": lambda r, riot, cfg, args: cmd_clear_priority(r, riot, args),
    "recalc-priority": lambda r, riot, cfg, args: cmd_recalc_priority(r, args),
    "recalc-players": lambda r, riot, cfg, args: cmd_recalc_players(r, args),
    "delayed-list": lambda r, riot, cfg, args: cmd_delayed_list(r, args),
    "delayed-flush": lambda r, riot, cfg, args: cmd_delayed_flush(r, args),
    "backfill-champions": lambda r, riot, cfg, args: cmd_backfill_champions(r, cfg, args),
    "opgg-status": lambda r, riot, cfg, args: cmd_opgg_status(r, cfg, args),
}


async def _dispatch(
    r: aioredis.Redis,
    riot: RiotClient | None,
    cfg: Config,
    args: argparse.Namespace,
) -> int:
    handler = _CMD_DISPATCH.get(args.command)
    if handler is None:
        raise AssertionError(f"unreachable command: {args.command}")
    # IMP-022: Lazily instantiate RiotClient only for commands that need it.
    owned_riot = False
    if riot is None and args.command in _RIOT_COMMANDS:
        riot = RiotClient(cfg.riot_api_key)
        owned_riot = True
    try:
        return await handler(r, riot, cfg, args)  # type: ignore[no-untyped-call]
    finally:
        if owned_riot and riot is not None:
            await riot.close()


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
