"""Admin CLI: replay-parse and replay-fetch commands."""

from __future__ import annotations

import argparse
import uuid
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope

from lol_admin._constants import (
    _DEFAULT_MAXLEN,
    _STREAM_MATCH_ID,
    _STREAM_PARSE,
)
from lol_admin._helpers import (
    _maxlen_for_stream,
    _print_info,
    _print_ok,
    _region_from_match_id,
)


async def _scan_parsed_matches(r: aioredis.Redis) -> set[str]:
    """Collect match IDs whose per-match hash has status=parsed (RDB-2)."""
    result: set[str] = set()
    async for key in r.scan_iter(match="match:*", count=200):
        key_str: str = key
        # Skip non-match keys (match:participants:*, match:status:*)
        if key_str.count(":") != 1:
            continue
        status = await r.hget(key_str, "status")
        if status == "parsed":
            result.add(key_str.removeprefix("match:"))
    return result


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
            source_stream=_STREAM_PARSE,
            type="parse",
            payload={"match_id": match_id, "region": region},
            max_attempts=cfg.max_attempts,
            correlation_id=str(uuid.uuid4()),
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
        correlation_id=str(uuid.uuid4()),
    )
    kwargs: dict[str, Any] = {}
    ml = _maxlen_for_stream(_STREAM_MATCH_ID)
    if ml is not None:
        kwargs["maxlen"] = ml
        kwargs["approximate"] = True
    await r.xadd(_STREAM_MATCH_ID, envelope.to_redis_fields(), **kwargs)  # type: ignore[arg-type]
    _print_ok(f"enqueued {match_id} \u2192 {_STREAM_MATCH_ID}")
    return 0
