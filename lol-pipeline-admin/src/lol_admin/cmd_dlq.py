"""Admin CLI: DLQ commands (list, replay, clear, archive)."""

from __future__ import annotations

import argparse
import json
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.constants import STREAM_DLQ_ARCHIVE, VALID_REPLAY_STREAMS
from lol_pipeline.models import DLQEnvelope
from lol_pipeline.streams import replay_from_dlq

from lol_admin._constants import _STREAM_DLQ
from lol_admin._formatting import _format_dlq_table
from lol_admin._helpers import (
    _confirm,
    _dlq_entries,
    _make_replay_envelope,
    _print_error,
    _print_info,
    _print_ok,
)


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
        if dlq.original_stream not in VALID_REPLAY_STREAMS:
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
    if not _confirm("Are you sure you want to clear all DLQ archive entries? [y/N]: ", args):
        _print_info("aborted")
        return 1
    length: int = await r.xlen(STREAM_DLQ_ARCHIVE)
    if length == 0:
        _print_info("DLQ archive is empty — nothing to clear")
        return 0
    await r.delete(STREAM_DLQ_ARCHIVE)
    _print_ok(f"cleared {length} entries from {STREAM_DLQ_ARCHIVE}")
    return 0
