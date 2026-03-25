"""IT-13 — Parser handles Riot API schema changes gracefully (missing optional fields)."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import redis.asyncio as aioredis

sys.path.insert(0, str(Path(__file__).parent))
from helpers import MATCH_ID, PUUID, consume_all, tlog

from lol_parser.main import _parse_match
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.streams import publish


def _envelope(match_id: str = MATCH_ID) -> MessageEnvelope:
    return MessageEnvelope(
        source_stream="stream:parse",
        type="parse",
        payload={"match_id": match_id, "region": "na1", "puuid": PUUID},
        max_attempts=5,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_parser_handles_missing_perks(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
) -> None:
    """Parser completes without error when 'perks' field is absent from participant."""
    log = tlog("it13")

    # Strip 'perks' from all participants
    m = copy.deepcopy(match_normal)
    for p in m["info"]["participants"]:
        p.pop("perks", None)

    raw_store = RawStore(r)
    await raw_store.set(MATCH_ID, json.dumps(m))

    env = _envelope()
    await publish(r, "stream:parse", env, maxlen=1000)

    for mid, env in await consume_all(r, "stream:parse", "parsers", "c"):
        await _parse_match(r, raw_store, cfg, mid, env, log)

    # Parser should still complete and mark match as parsed
    status = await r.hget(f"match:{MATCH_ID}", "status")
    assert status == "parsed"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_parser_handles_missing_challenges(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
) -> None:
    """Parser completes without error when 'challenges' field is absent."""
    log = tlog("it13")

    m = copy.deepcopy(match_normal)
    for p in m["info"]["participants"]:
        p.pop("challenges", None)

    raw_store = RawStore(r)
    await raw_store.set(MATCH_ID, json.dumps(m))

    env = _envelope()
    await publish(r, "stream:parse", env, maxlen=1000)

    for mid, env in await consume_all(r, "stream:parse", "parsers", "c"):
        await _parse_match(r, raw_store, cfg, mid, env, log)

    status = await r.hget(f"match:{MATCH_ID}", "status")
    assert status == "parsed"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_parser_handles_extra_unknown_fields(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
) -> None:
    """Parser completes without error when Riot API adds new unknown fields."""
    log = tlog("it13")

    m = copy.deepcopy(match_normal)
    # Add hypothetical new Riot API fields
    m["info"]["newField2026"] = "some_value"
    for p in m["info"]["participants"]:
        p["futureRiotField"] = 42

    raw_store = RawStore(r)
    await raw_store.set(MATCH_ID, json.dumps(m))

    env = _envelope()
    await publish(r, "stream:parse", env, maxlen=1000)

    for mid, env in await consume_all(r, "stream:parse", "parsers", "c"):
        await _parse_match(r, raw_store, cfg, mid, env, log)

    status = await r.hget(f"match:{MATCH_ID}", "status")
    assert status == "parsed"
    # Core stats still calculated
    assert await r.zcard(f"player:matches:{PUUID}") == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_parser_handles_missing_participant_stats(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
) -> None:
    """Parser completes when optional participant stat fields (visionScore etc.) are missing."""
    log = tlog("it13")

    m = copy.deepcopy(match_normal)
    optional_fields = ["visionScore", "goldEarned", "wardsPlaced", "wardsKilled"]
    for p in m["info"]["participants"]:
        for f in optional_fields:
            p.pop(f, None)

    raw_store = RawStore(r)
    await raw_store.set(MATCH_ID, json.dumps(m))

    env = _envelope()
    await publish(r, "stream:parse", env, maxlen=1000)

    for mid, env in await consume_all(r, "stream:parse", "parsers", "c"):
        await _parse_match(r, raw_store, cfg, mid, env, log)

    status = await r.hget(f"match:{MATCH_ID}", "status")
    assert status == "parsed"
