"""IT-03 — 429 end-to-end recovery: fetcher → DLQ → recovery → delay → retry."""

from __future__ import annotations


import httpx
import pytest
import redis.asyncio as aioredis
import respx

from helpers import GAME_NAME, MATCH_ID, PUUID, REGION, TAG_LINE, consume_all, tlog
from lol_analyzer.main import _analyze_player
from lol_crawler.main import _crawl_player
from lol_delay_scheduler.main import _tick
from lol_fetcher.main import _fetch_match
from lol_parser.main import _parse_match
from lol_pipeline.config import Config
from lol_pipeline.models import DLQEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_recovery.main import _process as recovery_process
from lol_seed.main import seed


@pytest.mark.asyncio
@pytest.mark.integration
async def test_429_recovery(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
    account_data: dict,
) -> None:
    """Fetcher 429 → DLQ → recovery → delayed:messages → retry → success."""
    log = tlog("it03")
    raw_store = RawStore(r)

    fetch_calls = 0

    def match_side_effect(request: httpx.Request) -> httpx.Response:
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, json=match_normal)

    with respx.mock:
        respx.get(url__regex=r".*/riot/account/v1/accounts/by-riot-id/.*").mock(
            return_value=httpx.Response(200, json=account_data)
        )
        respx.get(url__regex=rf".*/lol/match/v5/matches/by-puuid/{PUUID}/ids.*").mock(
            return_value=httpx.Response(200, json=[MATCH_ID])
        )
        respx.get(url__regex=rf".*/lol/match/v5/matches/{MATCH_ID}$").mock(
            side_effect=match_side_effect
        )

        riot = RiotClient("test-api-key", r=r)
        try:
            # 1. Seed + Crawl
            assert await seed(r, riot, cfg, GAME_NAME, TAG_LINE, REGION, log) == 0
            for mid, env in await consume_all(r, "stream:puuid", "crawlers", "c"):
                await _crawl_player(r, riot, cfg, mid, env, log)

            # 2. Fetch → 429 → nack_to_dlq
            for mid, env in await consume_all(r, "stream:match_id", "fetchers", "c"):
                await _fetch_match(r, riot, raw_store, cfg, mid, env, log)

            # Verify DLQ entry exists
            dlq_entries = await r.xrange("stream:dlq")
            assert len(dlq_entries) >= 1

            # 3. Recovery → delayed:messages
            for dlq_id, fields in dlq_entries:
                dlq = DLQEnvelope.from_redis_fields(fields)
                await recovery_process(r, cfg, "test-recovery", dlq_id, dlq, log)

            # Verify delayed:messages has an entry
            delayed_count = await r.zcard("delayed:messages")
            assert delayed_count >= 1

            # 4. Fast-forward: set delayed score to 0 (immediate dispatch)
            members = await r.zrange("delayed:messages", 0, -1)
            for member in members:
                await r.zadd("delayed:messages", {member: 0})

            # 5. Delay Scheduler dispatches to stream:match_id
            await _tick(r, log)

            # 6. Fetcher retry → 200
            for mid, env in await consume_all(r, "stream:match_id", "fetchers", "c"):
                await _fetch_match(r, riot, raw_store, cfg, mid, env, log)

            # 7. Parse
            for mid, env in await consume_all(r, "stream:parse", "parsers", "c"):
                await _parse_match(r, raw_store, cfg, mid, env, log)

            # 8. Analyze
            for mid, env in await consume_all(r, "stream:analyze", "analyzers", "w"):
                await _analyze_player(r, cfg, "w", mid, env, log)
        finally:
            await riot.close()

    # --- Assertions ---
    assert await r.hget(f"match:{MATCH_ID}", "status") == "parsed"
    stats = await r.hgetall(f"player:stats:{PUUID}")
    assert int(stats["total_games"]) == 1
    assert await r.zcard("delayed:messages") == 0
