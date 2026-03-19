"""IT-01 — Happy path: seed → crawler → fetcher → parser → analyzer."""

from __future__ import annotations

import httpx
import pytest
import redis.asyncio as aioredis
import respx

from helpers import GAME_NAME, MATCH_ID, PUUID, REGION, TAG_LINE, consume_all, tlog
from lol_analyzer.main import _analyze_player
from lol_crawler.main import _crawl_player
from lol_fetcher.main import _fetch_match
from lol_parser.main import _parse_match
from lol_pipeline.config import Config
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_seed.main import seed


@pytest.mark.asyncio
@pytest.mark.integration
async def test_happy_path(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
    account_data: dict,
) -> None:
    """Full pipeline: seed → crawl → fetch → parse → analyze → stats in Redis."""
    log = tlog("it01")
    raw_store = RawStore(r)

    with respx.mock:
        respx.get(url__regex=r".*/riot/account/v1/accounts/by-riot-id/.*").mock(
            return_value=httpx.Response(200, json=account_data)
        )
        respx.get(url__regex=rf".*/lol/match/v5/matches/by-puuid/{PUUID}/ids.*").mock(
            return_value=httpx.Response(200, json=[MATCH_ID])
        )
        respx.get(url__regex=rf".*/lol/match/v5/matches/{MATCH_ID}$").mock(
            return_value=httpx.Response(200, json=match_normal)
        )

        riot = RiotClient("test-api-key", r=r)
        try:
            # 1. Seed
            assert await seed(r, riot, cfg, GAME_NAME, TAG_LINE, REGION, log) == 0

            # 2. Crawl
            for mid, env in await consume_all(r, "stream:puuid", "crawlers", "c"):
                await _crawl_player(r, riot, cfg, mid, env, log)

            # 3. Fetch
            for mid, env in await consume_all(r, "stream:match_id", "fetchers", "c"):
                await _fetch_match(r, riot, raw_store, cfg, mid, env, log)

            # 4. Parse
            for mid, env in await consume_all(r, "stream:parse", "parsers", "c"):
                await _parse_match(r, raw_store, cfg, mid, env, log)

            # 5. Analyze (10 participants → 10 messages)
            for mid, env in await consume_all(r, "stream:analyze", "analyzers", "w"):
                await _analyze_player(r, cfg, "w", mid, env, log)
        finally:
            await riot.close()

    # --- Assertions ---
    stats = await r.hgetall(f"player:stats:{PUUID}")
    assert int(stats["total_games"]) == 1
    assert int(stats["total_wins"]) == 1
    assert stats["kda"] == "7.5000"
    assert stats["win_rate"] == "1.0000"
    assert stats["avg_kills"] == "10.0000"
    assert stats["avg_deaths"] == "2.0000"
    assert stats["avg_assists"] == "5.0000"

    assert await r.hget(f"match:{MATCH_ID}", "status") == "parsed"
    assert await r.sismember("match:status:parsed", MATCH_ID)
    assert await r.zcard(f"player:matches:{PUUID}") == 1
