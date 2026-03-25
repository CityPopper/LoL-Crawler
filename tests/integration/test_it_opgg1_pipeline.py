"""IT-OPGG-1 — op.gg data through OpggClient -> ETL -> RawStore -> Redis."""
from __future__ import annotations

import pytest
import httpx
import respx
import redis.asyncio as aioredis
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from helpers import tlog

from lol_pipeline.opgg_client import OpggClient
from lol_pipeline.raw_store import RawStore

SUMMONER_ID = "test-opgg-summoner-abc"
REGION = "na"

MOCK_SUMMONER_RESP = {
    "data": {"summoner_id": SUMMONER_ID, "game_name": "Faker", "tagline": "NA1", "level": 500}
}

MOCK_GAMES_RESP = {
    "data": [
        {
            "id": "opgg-internal-hash-001",
            "created_at": "2026-03-20T15:00:00+00:00",
            "game_type": "Ranked",
            "game_length_second": 1820,
            "teams": [
                {
                    "game_stat": {"is_win": True, "kill": 20, "death": 8, "assist": 25},
                    "participants": [
                        {
                            "summoner": {"summoner_id": SUMMONER_ID, "puuid": "faker-puuid-001"},
                            "champion_id": 238,
                            "position": "MID",
                            "stats": {"kill": 10, "death": 2, "assist": 8, "cs": 210,
                                      "damage_dealt_to_champions": 45000},
                            "items": [3285, 3116, 4645, 3089, 3135, 3157, 3340],
                            "op_score": 9.9,
                        }
                    ],
                },
                {
                    "game_stat": {"is_win": False, "kill": 8, "death": 20, "assist": 10},
                    "participants": [
                        {
                            "summoner": {"summoner_id": "opp-id", "puuid": "opp-puuid-001"},
                            "champion_id": 157,
                            "position": "ADC",
                            "stats": {"kill": 3, "death": 8, "assist": 2, "cs": 180,
                                      "damage_dealt_to_champions": 22000},
                            "items": [3031, 3094, 0, 0, 0, 0, 3363],
                            "op_score": 4.1,
                        }
                    ],
                },
            ],
        }
    ],
    "meta": {"last_game_created_at": "2026-03-20T15:00:00+00:00"},
}


@pytest.mark.asyncio
@pytest.mark.integration
@respx.mock
async def test_opgg_match_history_stored_in_raw_store(r: aioredis.Redis) -> None:
    """OpggClient fetches match history, ETL normalizes it, RawStore stores under opgg prefix."""
    log = tlog("it-opgg1")

    # Mock op.gg HTTP endpoints
    respx.get("https://lol-api-summoner.op.gg/api/na/summoners/test-opgg-summoner-abc/games").mock(
        return_value=httpx.Response(200, json=MOCK_GAMES_RESP)
    )

    http = httpx.AsyncClient()
    client = OpggClient(http)
    raw_store = RawStore(r, key_prefix="raw:opgg:match:")

    try:
        matches = await client.get_match_history(SUMMONER_ID, REGION)
    finally:
        await client.close()

    assert len(matches) == 1
    match = matches[0]

    # Store the normalized match
    match_id = match["metadata"]["match_id"]
    import json
    await raw_store.set(match_id, json.dumps(match))

    # --- Assertions ---
    # 1. Stored under opgg prefix, NOT riot prefix
    assert await r.exists(f"raw:opgg:match:{match_id}") == 1
    assert await r.exists(f"raw:match:{match_id}") == 0

    # 2. match_id starts with OPGG_
    assert match_id.startswith("OPGG_")

    # 3. ETL drops op_score
    participants = match["info"]["participants"]
    for p in participants:
        assert "op_score" not in p

    # 4. source field set to "opgg"
    assert match["info"]["source"] == "opgg"

    # 5. Participant stats normalized correctly
    faker = next(p for p in participants if p["puuid"] == "faker-puuid-001")
    assert faker["kills"] == 10
    assert faker["deaths"] == 2
    assert faker["assists"] == 8
    assert faker["totalMinionsKilled"] == 210
    assert faker["championId"] == 238


@pytest.mark.asyncio
@pytest.mark.integration
@respx.mock
async def test_opgg_summoner_lookup_then_match_history(r: aioredis.Redis) -> None:
    """get_summoner_id -> get_match_history full round-trip with mocked op.gg API."""
    respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
        return_value=httpx.Response(200, json=MOCK_SUMMONER_RESP)
    )
    respx.get("https://lol-api-summoner.op.gg/api/na/summoners/test-opgg-summoner-abc/games").mock(
        return_value=httpx.Response(200, json=MOCK_GAMES_RESP)
    )

    http = httpx.AsyncClient()
    client = OpggClient(http)
    try:
        summoner_id = await client.get_summoner_id("Faker", "NA1", "na")
        assert summoner_id == SUMMONER_ID

        matches = await client.get_match_history(summoner_id, REGION)
        assert len(matches) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.integration
@respx.mock
async def test_opgg_raw_store_default_prefix_unaffected(r: aioredis.Redis) -> None:
    """Storing op.gg data with opgg prefix does not affect the default riot prefix."""
    import json

    respx.get("https://lol-api-summoner.op.gg/api/na/summoners/test-opgg-summoner-abc/games").mock(
        return_value=httpx.Response(200, json=MOCK_GAMES_RESP)
    )

    http = httpx.AsyncClient()
    client = OpggClient(http)
    raw_store_opgg = RawStore(r, key_prefix="raw:opgg:match:")
    raw_store_riot = RawStore(r)  # default prefix "raw:match:"

    try:
        matches = await client.get_match_history(SUMMONER_ID, REGION)
    finally:
        await client.close()

    match_id = matches[0]["metadata"]["match_id"]
    await raw_store_opgg.set(match_id, json.dumps(matches[0]))

    # opgg prefix has data
    assert await raw_store_opgg.exists(match_id) is True
    # riot prefix does NOT have the same key
    assert await raw_store_riot.exists(match_id) is False
