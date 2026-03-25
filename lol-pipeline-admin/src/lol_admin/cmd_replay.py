"""Admin CLI: replay-parse and replay-fetch commands."""

from __future__ import annotations

import argparse
import uuid

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.constants import STREAM_MATCH_ID, STREAM_PARSE
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import publish

from lol_admin._helpers import (
    _print_info,
    _print_ok,
    _region_from_match_id,
    _scan_parsed_matches,
)


async def cmd_replay_parse(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    if not args.all:
        from lol_admin._helpers import _print_error

        _print_error("--all is required")
        return 1
    match_ids = await _scan_parsed_matches(r)
    if not match_ids:
        _print_info("No parsed matches found")
        return 0
    for match_id in match_ids:
        region = _region_from_match_id(match_id)
        envelope = MessageEnvelope(
            source_stream=STREAM_PARSE,
            type="parse",
            payload={"match_id": match_id, "region": region},
            max_attempts=cfg.max_attempts,
            correlation_id=str(uuid.uuid4()),
        )
        await publish(r, STREAM_PARSE, envelope)
    _print_ok(f"replayed {len(match_ids)} entries to {STREAM_PARSE}")
    return 0


async def cmd_replay_fetch(r: aioredis.Redis, cfg: Config, args: argparse.Namespace) -> int:
    match_id: str = args.match_id
    region = _region_from_match_id(match_id)
    envelope = MessageEnvelope(
        source_stream=STREAM_MATCH_ID,
        type="match_id",
        payload={"match_id": match_id, "region": region},
        max_attempts=cfg.max_attempts,
        correlation_id=str(uuid.uuid4()),
    )
    await publish(r, STREAM_MATCH_ID, envelope)
    _print_ok(f"enqueued {match_id} \u2192 {STREAM_MATCH_ID}")
    return 0
