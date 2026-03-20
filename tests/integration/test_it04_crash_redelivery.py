"""IT-04 — Worker crash and redelivery via XAUTOCLAIM."""

from __future__ import annotations

import asyncio
import json

import pytest
import redis.asyncio as aioredis

from helpers import MATCH_ID, PUUID, consume_all, tlog
from lol_analyzer.main import _analyze_player
from lol_parser.main import _parse_match
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.streams import consume, publish


@pytest.mark.asyncio
@pytest.mark.integration
async def test_crash_redelivery(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
) -> None:
    """Dequeue → crash (no ACK) → XAUTOCLAIM → redelivery → success."""
    log = tlog("it04")
    raw_store = RawStore(r)

    # Pre-populate raw store so parser can process
    await raw_store.set(MATCH_ID, json.dumps(match_normal))

    # Publish parse message
    env = MessageEnvelope(
        source_stream="stream:parse",
        type="parse",
        payload={"match_id": MATCH_ID, "region": "na1"},
        max_attempts=cfg.max_attempts,
    )
    await publish(r, "stream:parse", env)

    # Consumer c1 reads the message (simulating a worker)
    msgs = await consume(r, "stream:parse", "parsers", "c1", count=1, block=500)
    assert len(msgs) == 1
    # c1 crashes — no ACK, no processing

    # Wait for the message to become idle (3 seconds > 2s autoclaim threshold)
    await asyncio.sleep(3)

    # Consumer c2 picks up via XAUTOCLAIM (autoclaim_min_idle_ms=2000)
    msgs = await consume(
        r,
        "stream:parse",
        "parsers",
        "c2",
        count=1,
        block=500,
        autoclaim_min_idle_ms=2000,
    )
    assert len(msgs) == 1, "message should be autoclaimed by c2"

    # c2 processes the message
    for mid, envelope in msgs:
        await _parse_match(r, raw_store, cfg, mid, envelope, log)

    # Analyze all participants
    for mid, envelope in await consume_all(r, "stream:analyze", "analyzers", "w"):
        await _analyze_player(r, cfg, "w", mid, envelope, log)

    # --- Assertions ---
    assert await r.hget(f"match:{MATCH_ID}", "status") == "parsed"
    assert await r.sismember("match:status:parsed", MATCH_ID)
    assert await r.zcard(f"player:matches:{PUUID}") == 1

    # Verify analyzer processed the match (player stats populated)
    stats = await r.hgetall(f"player:stats:{PUUID}")
    assert stats, "player:stats should be populated after analysis"
    assert int(stats["total_games"]) >= 1

    # Verify no duplicates in stream:analyze (only 10 messages total)
    analyze_len = await r.xlen("stream:analyze")
    assert analyze_len == 10
