"""RDB-1 — seen:matches should use daily-bucketed sets, not a single global SET.

These tests are RED until RDB-1 is implemented.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import redis.asyncio as aioredis
import respx

sys.path.insert(0, str(Path(__file__).parent))
from helpers import GAME_NAME, MATCH_ID, PUUID, REGION, TAG_LINE, consume_all, tlog

from lol_crawler.main import _crawl_player
from lol_fetcher.main import _fetch_match
from lol_pipeline.config import Config
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_seed.main import seed


@pytest.mark.asyncio
@pytest.mark.integration
async def test_seen_matches_uses_daily_bucket(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
    account_data: dict,
) -> None:
    """Fetcher writes to seen:matches:{YYYY-MM-DD} bucket, NOT seen:matches."""
    from datetime import UTC, datetime
    log = tlog("rdb1")
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    daily_key = f"seen:matches:{today}"

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
        raw_store = RawStore(r)
        try:
            assert await seed(r, riot, cfg, GAME_NAME, TAG_LINE, REGION, log) == 0
            for mid, env in await consume_all(r, "stream:puuid", "crawlers", "c"):
                await _crawl_player(r, riot, cfg, mid, env, log)
            for mid, env in await consume_all(r, "stream:match_id", "fetchers", "c"):
                await _fetch_match(r, riot, raw_store, cfg, mid, env, log)
        finally:
            await riot.close()

    # RDB-1: expect daily bucket, not global SET
    assert await r.sismember(daily_key, MATCH_ID), (
        f"Expected {MATCH_ID} in {daily_key} but key does not exist or is empty"
    )
    # Global SET should NOT be used
    assert not await r.sismember("seen:matches", MATCH_ID), (
        "RDB-1: seen:matches global SET still used — daily bucketing not implemented"
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_seen_matches_daily_bucket_has_ttl(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
    account_data: dict,
) -> None:
    """Daily seen:matches bucket has an 8-day TTL."""
    from datetime import UTC, datetime
    log = tlog("rdb1-ttl")
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    daily_key = f"seen:matches:{today}"

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
        raw_store = RawStore(r)
        try:
            assert await seed(r, riot, cfg, GAME_NAME, TAG_LINE, REGION, log) == 0
            for mid, env in await consume_all(r, "stream:puuid", "crawlers", "c"):
                await _crawl_player(r, riot, cfg, mid, env, log)
            for mid, env in await consume_all(r, "stream:match_id", "fetchers", "c"):
                await _fetch_match(r, riot, raw_store, cfg, mid, env, log)
        finally:
            await riot.close()

    ttl = await r.ttl(daily_key)
    # 8 days = 691200 seconds; allow +/-60s for test execution time
    assert 691140 <= ttl <= 691260, (
        f"Expected 8-day TTL on {daily_key}, got {ttl}s"
    )
