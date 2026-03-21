"""IT-05 — system:halted propagation on 403."""

from __future__ import annotations

import httpx
import pytest
import redis.asyncio as aioredis
import respx

from helpers import PUUID, REGION, consume_all, tlog
from lol_fetcher.main import _fetch_match
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.streams import publish

_MATCH_A = "NA1_0001"
_MATCH_B = "NA1_0002"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_system_halted(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
) -> None:
    """403 on second fetch → system:halted=1, second message stays in PEL."""
    log = tlog("it05")
    raw_store = RawStore(r)

    fetch_calls = 0

    def match_side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            return httpx.Response(200, json=match_normal)
        return httpx.Response(403)

    with respx.mock:
        respx.get(url__regex=r".*/lol/match/v5/matches/NA1_\d+$").mock(
            side_effect=match_side_effect
        )

        riot = RiotClient("test-api-key", r=r)
        try:
            # Publish 2 match_id messages
            for mid in (_MATCH_A, _MATCH_B):
                env = MessageEnvelope(
                    source_stream="stream:match_id",
                    type="match_id",
                    payload={"match_id": mid, "puuid": PUUID, "region": REGION},
                    max_attempts=cfg.max_attempts,
                )
                await publish(r, "stream:match_id", env)

            # Fetcher processes both messages
            msgs = await consume_all(r, "stream:match_id", "fetchers", "c")
            assert len(msgs) == 2
            for mid, env in msgs:
                await _fetch_match(r, riot, raw_store, cfg, mid, env, log)
        finally:
            await riot.close()

    # --- Assertions ---
    assert await r.get("system:halted") == "1"
    assert await r.hget(f"match:{_MATCH_A}", "status") == "fetched"

    # Second message stays in PEL (not acked due to 403)
    pending_info = await r.xpending("stream:match_id", "fetchers")
    assert pending_info["pending"] >= 1
