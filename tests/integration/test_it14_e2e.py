"""IT-14 — End-to-end: seed -> crawl -> fetch -> parse -> analyze -> UI displays stats."""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the UI package is importable alongside other services
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent.parent
_ui_src = _ROOT / "lol-pipeline-ui" / "src"
if _ui_src.exists() and str(_ui_src) not in sys.path:
    sys.path.insert(0, str(_ui_src))

import httpx
import pytest
import redis.asyncio as aioredis
import respx
from httpx import ASGITransport, AsyncClient

from helpers import GAME_NAME, MATCH_ID, PUUID, REGION, TAG_LINE, consume_all, tlog
from lol_player_stats.main import handle_player_stats
from lol_crawler.main import _crawl_player
from lol_fetcher.main import _fetch_match
from lol_parser.main import _parse_match
from lol_pipeline.config import Config
from lol_pipeline._helpers import name_cache_key
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_admin.cmd_track import seed
from lol_ui.main import app


@pytest.mark.asyncio
@pytest.mark.integration
async def test_e2e_pipeline_to_ui(
    r: aioredis.Redis,
    cfg: Config,
    match_normal: dict,
    account_data: dict,
) -> None:
    """Full pipeline then verify the FastAPI UI renders player stats."""
    log = tlog("it14")
    raw_store = RawStore(r)

    # ------------------------------------------------------------------
    # Phase 1: Run the full pipeline (identical to IT-01)
    # ------------------------------------------------------------------
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

            # 5. Analyze (10 participants -> 10 messages)
            for mid, env in await consume_all(r, "stream:analyze", "analyzers", "w"):
                await handle_player_stats(r, cfg, "w", mid, env, log)
        finally:
            await riot.close()

    # ------------------------------------------------------------------
    # Phase 2: Verify pipeline results in Redis (same as IT-01)
    # ------------------------------------------------------------------
    stats = await r.hgetall(f"player:stats:{PUUID}")
    assert int(stats["total_games"]) == 1
    assert int(stats["total_wins"]) == 1
    assert stats["kda"] == "7.5000"
    assert stats["win_rate"] == "1.0000"
    assert stats["avg_kills"] == "10.0000"
    assert stats["avg_deaths"] == "2.0000"
    assert stats["avg_assists"] == "5.0000"

    # ------------------------------------------------------------------
    # Phase 3: Verify the UI serves stats correctly
    # ------------------------------------------------------------------

    # Pre-seed the name cache so the stats route resolves PUUID from Redis
    # without calling the Riot API (avoids needing respx for UI requests).
    cache_key = name_cache_key(GAME_NAME, TAG_LINE)
    await r.set(cache_key, PUUID, ex=3600)

    # Inject the test Redis and config into the FastAPI app state so the
    # route handlers read from the testcontainers Redis instance.
    app.state.r = r
    app.state.cfg = cfg
    # Create a dummy RiotClient; won't be called because cache_key is set.
    app.state.riot = RiotClient("test-api-key", r=r)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # --- Stats page: should return 200 with player statistics ---
            riot_id = f"{GAME_NAME}#{TAG_LINE}"
            resp = await client.get(
                "/stats",
                params={"riot_id": riot_id, "region": REGION},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

            body = resp.text
            # The stats table renders key-value rows; verify key stats appear
            assert "total_games" in body
            assert "win_rate" in body
            assert "kda" in body
            assert "avg_kills" in body
            # The heading contains the player name
            assert GAME_NAME in body

            # --- Players page: should list the seeded player ---
            resp_players = await client.get("/players")
            assert resp_players.status_code == 200
            assert GAME_NAME in resp_players.text

            # --- Dashboard: should return 200 ---
            resp_dash = await client.get("/")
            assert resp_dash.status_code == 200
    finally:
        await app.state.riot.close()
