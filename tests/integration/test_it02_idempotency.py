"""IT-02 — Idempotency: re-seed same player, stats unchanged."""

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


async def _run_full_pipeline(
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    raw_store: RawStore,
) -> None:
    """Seed + crawl + fetch + parse + analyze in one shot."""
    log = tlog("it02-pipe")

    assert await seed(r, riot, cfg, GAME_NAME, TAG_LINE, REGION, log) == 0

    for mid, env in await consume_all(r, "stream:puuid", "crawlers", "c"):
        await _crawl_player(r, riot, cfg, mid, env, log)

    for mid, env in await consume_all(r, "stream:match_id", "fetchers", "c"):
        await _fetch_match(r, riot, raw_store, cfg, mid, env, log)

    for mid, env in await consume_all(r, "stream:parse", "parsers", "c"):
        await _parse_match(r, raw_store, cfg, mid, env, log)

    for mid, env in await consume_all(r, "stream:analyze", "analyzers", "w"):
        await _analyze_player(r, cfg, "w", mid, env, log)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_idempotency(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
    account_data: dict,
) -> None:
    """Re-seeding same player does not duplicate stats."""
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
            # First run — full pipeline
            await _run_full_pipeline(r, riot, cfg, raw_store)

            games_after_first = int(
                await r.hget(f"player:stats:{PUUID}", "total_games")
            )
            matches_after_first = await r.zcard(f"player:matches:{PUUID}")

            # Bypass cooldown by deleting timestamps
            await r.hdel(f"player:{PUUID}", "seeded_at", "last_crawled_at")

            # Second run — same player
            await _run_full_pipeline(r, riot, cfg, raw_store)
        finally:
            await riot.close()

    # Stats and match count unchanged
    assert (
        int(await r.hget(f"player:stats:{PUUID}", "total_games")) == games_after_first
    )
    assert await r.zcard(f"player:matches:{PUUID}") == matches_after_first
