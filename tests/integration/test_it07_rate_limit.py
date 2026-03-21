"""IT-07 — Rate limit enforcement: 3 fetchers never exceed 20 req/s."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import redis.asyncio as aioredis
import respx

from helpers import PUUID, REGION, tlog
from lol_fetcher.main import _fetch_match
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.streams import consume, publish

_NUM_MESSAGES = 200
_RATE_LIMIT = 20
_MONITOR_SECONDS = 10
_MONITOR_INTERVAL = 0.1


async def _fetcher_loop(
    r: aioredis.Redis,
    riot: RiotClient,
    raw_store: RawStore,
    cfg: Config,
    consumer: str,
) -> int:
    """Fetch until idle."""
    log = tlog(f"it07-fetcher-{consumer}")
    processed = 0
    idle = 0
    while idle < 3:
        msgs = await consume(
            r, "stream:match_id", "fetchers", consumer, count=5, block=500
        )
        if not msgs:
            idle += 1
            continue
        idle = 0
        for mid, env in msgs:
            await _fetch_match(r, riot, raw_store, cfg, mid, env, log)
            processed += 1
    return processed


async def _rate_monitor(r: aioredis.Redis) -> list[int]:
    """Sample ratelimit:short ZCARD at intervals, return all samples."""
    samples: list[int] = []
    deadline = time.time() + _MONITOR_SECONDS
    while time.time() < deadline:
        # Clean up expired entries before measuring
        now_ms = int(time.time() * 1000)
        await r.zremrangebyscore("ratelimit:short", "-inf", now_ms - 1000)
        count: int = await r.zcard("ratelimit:short")
        samples.append(count)
        await asyncio.sleep(_MONITOR_INTERVAL)
    return samples


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rate_limit_enforcement(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
) -> None:
    """3 fetchers sharing rate limiter never exceed 20 req/s."""
    raw_store = RawStore(r)

    # Disable long-window limit for faster test completion
    await r.set("ratelimit:limits:long", "1000")

    # Publish 200 match_id messages
    for i in range(1, _NUM_MESSAGES + 1):
        mid = f"NA1_{i:04d}"
        env = MessageEnvelope(
            source_stream="stream:match_id",
            type="match_id",
            payload={"match_id": mid, "puuid": PUUID, "region": REGION},
            max_attempts=cfg.max_attempts,
        )
        await publish(r, "stream:match_id", env)

    with respx.mock:
        # All match fetches return 200
        respx.get(url__regex=r".*/lol/match/v5/matches/NA1_\d+$").mock(
            return_value=httpx.Response(200, json=match_normal)
        )

        riot = RiotClient("test-api-key", r=r)
        try:
            # Run 3 fetchers + rate monitor concurrently
            results = await asyncio.wait_for(
                asyncio.gather(
                    _rate_monitor(r),
                    _fetcher_loop(r, riot, raw_store, cfg, "f1"),
                    _fetcher_loop(r, riot, raw_store, cfg, "f2"),
                    _fetcher_loop(r, riot, raw_store, cfg, "f3"),
                ),
                timeout=30,
            )
        finally:
            await riot.close()

    samples = results[0]
    total_fetched = results[1] + results[2] + results[3]

    # --- Assertions ---
    max_observed = max(samples) if samples else 0
    assert max_observed <= _RATE_LIMIT, (
        f"Rate limit exceeded: max ZCARD was {max_observed} (limit {_RATE_LIMIT})"
    )
    assert total_fetched == _NUM_MESSAGES
