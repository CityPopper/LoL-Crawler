"""IT-15 — op.gg fixture-driven ETL pipeline: load fixtures -> mock HTTP -> ETL -> Redis."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
import respx

sys.path.insert(0, str(Path(__file__).parent))
from helpers import FIXTURES  # noqa: E402

from lol_pipeline._opgg_etl import normalize_game  # noqa: E402
from lol_pipeline.opgg_client import OpggClient  # noqa: E402
from lol_pipeline.raw_store import RawStore  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture data loaded from static JSON files
# ---------------------------------------------------------------------------
_OPGG_SUMMONER: dict[str, Any] = json.loads(
    (FIXTURES / "opgg_summoner.json").read_text()
)
_OPGG_MATCH: dict[str, Any] = json.loads((FIXTURES / "opgg_match.json").read_text())

_SUMMONER_ID: str = _OPGG_SUMMONER["data"]["summoner_id"]
_REGION = "na"
_TEST_PUUID = "test-puuid-0001"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
@respx.mock
async def test_fixture_etl_field_level_output(r: aioredis.Redis) -> None:
    """Load opgg_match.json, run through OpggClient ETL, assert every field."""
    respx.get(
        f"https://lol-api-summoner.op.gg/api/{_REGION}/summoners/{_SUMMONER_ID}/games"
    ).mock(return_value=httpx.Response(200, json=_OPGG_MATCH))

    http = httpx.AsyncClient()
    client = OpggClient(http)
    try:
        matches = await client.get_match_history(_SUMMONER_ID, _REGION)
    finally:
        await client.close()

    assert len(matches) == 1
    match = matches[0]

    # --- metadata ---
    assert match["metadata"]["data_version"] == "2"
    assert match["metadata"]["match_id"].startswith("OPGG_NA1_")
    assert match["metadata"]["match_id"] == "OPGG_NA1_7234567890"
    assert len(match["metadata"]["participants"]) == 10

    # --- info top-level ---
    info = match["info"]
    assert info["source"] == "opgg"
    assert info["gameDuration"] == 1945
    assert info["gameCreation"] == 1742078400000  # 2026-03-15T22:30:00+00:00
    assert info["platformId"] == "NA1"
    assert info["queueId"] == 420
    assert info["gameMode"] == "Ranked"

    # --- teams ---
    assert len(info["teams"]) == 2
    assert info["teams"][0]["teamId"] == 100
    assert info["teams"][0]["win"] is True
    assert info["teams"][1]["teamId"] == 200
    assert info["teams"][1]["win"] is False

    # --- participants ---
    participants = info["participants"]
    assert len(participants) == 10

    # Find the test player (Pwnerer)
    pwnerer = next(p for p in participants if p["puuid"] == _TEST_PUUID)
    assert pwnerer["kills"] == 14
    assert pwnerer["deaths"] == 3
    assert pwnerer["assists"] == 9
    assert pwnerer["championId"] == 236  # Lucian
    assert pwnerer["teamPosition"] == "BOTTOM"
    assert pwnerer["totalMinionsKilled"] == 245
    assert pwnerer["totalDamageDealtToChampions"] == 38200
    assert pwnerer["teamId"] == 100
    assert pwnerer["win"] is True
    assert pwnerer["item0"] == 3031
    assert pwnerer["item6"] == 3340

    # Proprietary fields must be absent
    assert "op_score" not in pwnerer
    assert "lane_score" not in pwnerer

    # Verify an enemy participant
    enemy_adc = next(
        p for p in participants if p["puuid"] == "test-puuid-opgg-enemy-adc"
    )
    assert enemy_adc["kills"] == 5
    assert enemy_adc["deaths"] == 7
    assert enemy_adc["teamId"] == 200
    assert enemy_adc["win"] is False
    assert "op_score" not in enemy_adc


@pytest.mark.asyncio
@pytest.mark.integration
@respx.mock
async def test_fixture_summoner_then_match_roundtrip(r: aioredis.Redis) -> None:
    """Summoner lookup -> match history using fixture files, full round-trip."""
    respx.get(f"https://lol-api-summoner.op.gg/api/v3/{_REGION}/summoners").mock(
        return_value=httpx.Response(200, json=_OPGG_SUMMONER)
    )
    respx.get(
        f"https://lol-api-summoner.op.gg/api/{_REGION}/summoners/{_SUMMONER_ID}/games"
    ).mock(return_value=httpx.Response(200, json=_OPGG_MATCH))

    http = httpx.AsyncClient()
    client = OpggClient(http)
    try:
        summoner_id = await client.get_summoner_id("Pwnerer", "1337", _REGION)
        assert summoner_id == _SUMMONER_ID

        matches = await client.get_match_history(summoner_id, _REGION)
        assert len(matches) == 1
        assert matches[0]["metadata"]["match_id"].startswith("OPGG_")
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.integration
@respx.mock
async def test_fixture_raw_store_roundtrip(r: aioredis.Redis) -> None:
    """ETL output stored in RawStore survives a set->get roundtrip with correct prefix."""
    respx.get(
        f"https://lol-api-summoner.op.gg/api/{_REGION}/summoners/{_SUMMONER_ID}/games"
    ).mock(return_value=httpx.Response(200, json=_OPGG_MATCH))

    http = httpx.AsyncClient()
    client = OpggClient(http)
    raw_opgg = RawStore(r, key_prefix="raw:opgg:match:")
    raw_riot = RawStore(r)

    try:
        matches = await client.get_match_history(_SUMMONER_ID, _REGION)
    finally:
        await client.close()

    match = matches[0]
    match_id = match["metadata"]["match_id"]
    await raw_opgg.set(match_id, json.dumps(match))

    # Verify stored under opgg prefix
    assert await raw_opgg.exists(match_id) is True
    assert await raw_riot.exists(match_id) is False
    assert await r.exists(f"raw:opgg:match:{match_id}") == 1
    assert await r.exists(f"raw:match:{match_id}") == 0

    # Roundtrip: retrieve and verify source field
    retrieved = json.loads(await raw_opgg.get(match_id))  # type: ignore[arg-type]
    assert retrieved["info"]["source"] == "opgg"
    assert retrieved["metadata"]["match_id"] == match_id

    # Verify participant count survives roundtrip
    assert len(retrieved["info"]["participants"]) == 10


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fixture_etl_normalize_game_directly() -> None:
    """Call normalize_game directly with fixture data — no HTTP, no Redis."""
    raw_game = _OPGG_MATCH["data"][0]
    result = normalize_game(raw_game, _REGION)

    assert result["metadata"]["match_id"] == "OPGG_NA1_7234567890"
    assert result["info"]["source"] == "opgg"
    assert len(result["info"]["participants"]) == 10
    assert len(result["info"]["teams"]) == 2

    # Verify all 10 participants have the required match-v5 fields
    required_fields = {
        "puuid",
        "championId",
        "teamPosition",
        "kills",
        "deaths",
        "assists",
        "totalMinionsKilled",
        "totalDamageDealtToChampions",
        "teamId",
        "win",
        "item0",
        "item1",
        "item2",
        "item3",
        "item4",
        "item5",
        "item6",
    }
    for p in result["info"]["participants"]:
        missing = required_fields - set(p.keys())
        assert not missing, f"Participant {p['puuid']} missing fields: {missing}"

    # Verify team assignment: first 5 on team 100, last 5 on team 200
    team_100 = [p for p in result["info"]["participants"] if p["teamId"] == 100]
    team_200 = [p for p in result["info"]["participants"] if p["teamId"] == 200]
    assert len(team_100) == 5
    assert len(team_200) == 5
    assert all(p["win"] is True for p in team_100)
    assert all(p["win"] is False for p in team_200)
