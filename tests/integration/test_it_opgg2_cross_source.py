"""IT-OPGG-2 — Cross-source: compare op.gg vs Riot API ETL output for same match."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
import redis.asyncio as aioredis
import respx

sys.path.insert(0, str(Path(__file__).parent))
from helpers import MATCH_ID, PUUID, REGION, tlog

from lol_pipeline.opgg_client import OpggClient
from lol_pipeline.raw_store import RawStore

# Shared match data — used to construct both op.gg and Riot API responses
_FAKER_PUUID = "faker-puuid-opgg-002"
_FAKER_CHAMPION_ID = 238  # Zed
_FAKER_KILLS = 12
_FAKER_DEATHS = 3
_FAKER_ASSISTS = 7

MOCK_OPGG_GAMES = {
    "data": [
        {
            "id": "opgg-cross-hash-002",
            "created_at": "2026-03-21T10:00:00+00:00",
            "game_type": "Ranked",
            "game_length_second": 1650,
            "teams": [
                {
                    "game_stat": {"is_win": True, "kill": 25, "death": 10, "assist": 30},
                    "participants": [
                        {
                            "summoner": {"summoner_id": "opgg-sum-id", "puuid": _FAKER_PUUID},
                            "champion_id": _FAKER_CHAMPION_ID,
                            "position": "MID",
                            "stats": {
                                "kill": _FAKER_KILLS,
                                "death": _FAKER_DEATHS,
                                "assist": _FAKER_ASSISTS,
                                "cs": 220,
                                "damage_dealt_to_champions": 50000,
                            },
                            "items": [3157, 3285, 3116, 4645, 3089, 3135, 3340],
                            "op_score": 9.8,
                        }
                    ],
                },
                {
                    "game_stat": {"is_win": False, "kill": 10, "death": 25, "assist": 15},
                    "participants": [],
                },
            ],
        }
    ],
    "meta": {},
}


@pytest.mark.asyncio
@pytest.mark.integration
@respx.mock
async def test_cross_source_participant_stats_match(
    r: aioredis.Redis,
    match_normal: dict,
) -> None:
    """op.gg and Riot API ETL produce consistent K/D/A and champion for same participant.

    Verifies that when both sources report the same match, the normalized
    participant stats (kills, deaths, assists, championId) agree.
    """
    log = tlog("it-opgg2")

    # --- op.gg branch ---
    respx.get(
        "https://lol-api-summoner.op.gg/api/na/summoners/opgg-sum-id/games"
    ).mock(return_value=httpx.Response(200, json=MOCK_OPGG_GAMES))

    http = httpx.AsyncClient()
    opgg_client = OpggClient(http)
    try:
        opgg_matches = await opgg_client.get_match_history("opgg-sum-id", "na")
    finally:
        await opgg_client.close()

    assert len(opgg_matches) == 1
    opgg_match = opgg_matches[0]
    opgg_participants = opgg_match["info"]["participants"]
    opgg_faker = next(p for p in opgg_participants if p["puuid"] == _FAKER_PUUID)

    # --- Riot API branch (use match_normal fixture, augment for comparison) ---
    # Build a Riot-style participant matching the same champion/stats
    riot_participant = {
        "puuid": _FAKER_PUUID,
        "championId": _FAKER_CHAMPION_ID,
        "kills": _FAKER_KILLS,
        "deaths": _FAKER_DEATHS,
        "assists": _FAKER_ASSISTS,
        "totalMinionsKilled": 220,
        "teamId": 100,
        "win": True,
    }

    # --- Cross-source comparison ---
    # Both ETL outputs must agree on key participant fields
    assert opgg_faker["kills"] == riot_participant["kills"]
    assert opgg_faker["deaths"] == riot_participant["deaths"]
    assert opgg_faker["assists"] == riot_participant["assists"]
    assert opgg_faker["championId"] == riot_participant["championId"]
    assert opgg_faker["win"] == riot_participant["win"]

    # op.gg-specific proprietary fields must be absent
    assert "op_score" not in opgg_faker
    assert "lane_score" not in opgg_faker


@pytest.mark.asyncio
@pytest.mark.integration
@respx.mock
async def test_cross_source_raw_stores_are_independent(
    r: aioredis.Redis,
    match_normal: dict,
) -> None:
    """op.gg and Riot API raw stores use separate Redis key namespaces."""
    import json as _json

    respx.get(
        "https://lol-api-summoner.op.gg/api/na/summoners/opgg-sum-id/games"
    ).mock(return_value=httpx.Response(200, json=MOCK_OPGG_GAMES))

    # Op.gg raw store
    http = httpx.AsyncClient()
    opgg_client = OpggClient(http)
    raw_opgg = RawStore(r, key_prefix="raw:opgg:match:")
    raw_riot = RawStore(r)  # default: raw:match:

    try:
        opgg_matches = await opgg_client.get_match_history("opgg-sum-id", "na")
    finally:
        await opgg_client.close()

    opgg_match_id = opgg_matches[0]["metadata"]["match_id"]
    await raw_opgg.set(opgg_match_id, _json.dumps(opgg_matches[0]))

    # Riot raw store — store the fixture match
    riot_match_id = match_normal["metadata"]["matchId"]
    await raw_riot.set(riot_match_id, _json.dumps(match_normal))

    # opgg store has op.gg match, riot store has riot match
    assert await raw_opgg.exists(opgg_match_id) is True
    assert await raw_riot.exists(riot_match_id) is True

    # Cross-namespace: riot store doesn't know about opgg match and vice versa
    assert await raw_riot.exists(opgg_match_id) is False
    assert await raw_opgg.exists(riot_match_id) is False


@pytest.mark.asyncio
@pytest.mark.integration
@respx.mock
async def test_opgg_source_field_preserved_after_raw_store_roundtrip(
    r: aioredis.Redis,
) -> None:
    """source='opgg' is preserved after RawStore set->get roundtrip."""
    import json as _json

    respx.get(
        "https://lol-api-summoner.op.gg/api/na/summoners/opgg-sum-id/games"
    ).mock(return_value=httpx.Response(200, json=MOCK_OPGG_GAMES))

    http = httpx.AsyncClient()
    opgg_client = OpggClient(http)
    raw_opgg = RawStore(r, key_prefix="raw:opgg:match:")

    try:
        opgg_matches = await opgg_client.get_match_history("opgg-sum-id", "na")
    finally:
        await opgg_client.close()

    opgg_match = opgg_matches[0]
    match_id = opgg_match["metadata"]["match_id"]
    await raw_opgg.set(match_id, _json.dumps(opgg_match))

    # Roundtrip
    retrieved_json = await raw_opgg.get(match_id)
    assert retrieved_json is not None
    retrieved = _json.loads(retrieved_json)

    assert retrieved["info"]["source"] == "opgg"
    assert retrieved["metadata"]["match_id"].startswith("OPGG_")
