"""IT-06 — Concurrent workers: 2 parsers + 2 analyzers, no data corruption."""

from __future__ import annotations

import asyncio
import json

import pytest
import redis.asyncio as aioredis

from helpers import PUUID, make_match, tlog
from lol_player_stats.main import handle_player_stats
from lol_parser.main import _parse_match
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.streams import consume, publish

_NUM_MATCHES = 10


async def _parser_loop(
    r: aioredis.Redis,
    raw_store: RawStore,
    cfg: Config,
    consumer: str,
) -> int:
    """Consume and parse until idle."""
    log = tlog(f"it06-parser-{consumer}")
    processed = 0
    idle = 0
    while idle < 3:
        msgs = await consume(r, "stream:parse", "parsers", consumer, count=5, block=500)
        if not msgs:
            idle += 1
            continue
        idle = 0
        for mid, env in msgs:
            await _parse_match(r, raw_store, cfg, mid, env, log)
            processed += 1
    return processed


async def _analyzer_loop(
    r: aioredis.Redis,
    cfg: Config,
    worker_id: str,
) -> int:
    """Consume and analyze until idle."""
    log = tlog(f"it06-analyzer-{worker_id}")
    processed = 0
    idle = 0
    while idle < 3:
        msgs = await consume(
            r, "stream:analyze", "analyzers", worker_id, count=10, block=500
        )
        if not msgs:
            idle += 1
            continue
        idle = 0
        for mid, env in msgs:
            await handle_player_stats(r, cfg, worker_id, mid, env, log)
            processed += 1
    return processed


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_workers(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
) -> None:
    """2 parsers + 2 analyzers process 10 matches concurrently without corruption."""
    raw_store = RawStore(r)

    # Pre-populate 10 match fixtures in raw store and publish parse messages
    # Each match gets a unique gameStartTimestamp so the analyzer cursor advances
    for i in range(1, _NUM_MATCHES + 1):
        mid = f"NA1_{i:04d}"
        match_data = make_match(match_normal, mid, game_start_offset=i * 1000)
        await raw_store.set(mid, json.dumps(match_data))
        env = MessageEnvelope(
            source_stream="stream:parse",
            type="parse",
            payload={"match_id": mid, "region": "na1"},
            max_attempts=cfg.max_attempts,
        )
        await publish(r, "stream:parse", env)

    # Phase 1: 2 concurrent parsers process all 10 matches
    p1, p2 = await asyncio.gather(
        _parser_loop(r, raw_store, cfg, "p1"),
        _parser_loop(r, raw_store, cfg, "p2"),
    )
    assert p1 + p2 == _NUM_MATCHES

    # Phase 2: 2 concurrent analyzers process all analyze messages
    await asyncio.gather(
        _analyzer_loop(r, cfg, "a1"),
        _analyzer_loop(r, cfg, "a2"),
    )

    # --- Assertions ---
    stats = await r.hgetall(f"player:stats:{PUUID}")
    assert int(stats["total_games"]) == _NUM_MATCHES

    assert await r.scard("match:status:parsed") == _NUM_MATCHES
    assert await r.zcard(f"player:matches:{PUUID}") == _NUM_MATCHES
