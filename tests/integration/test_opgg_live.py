"""Live op.gg integration tests -- real HTTP calls to op.gg API.

Gated behind ``OPGG_LIVE_TESTS=1``.  These tests validate that the pipeline's
extractor, transformer, and blob store work against the *actual* op.gg API
response shape.  They are NOT run in CI; they run manually before releases
or when the op.gg ETL code changes.

Environment variables:
    OPGG_LIVE_TESTS       -- set to "1" to enable these tests
    OPGG_TEST_SUMMONER_ID -- op.gg summoner_id to query (default: well-known test value)
    OPGG_TEST_PUUID       -- PUUID for on-demand fetch test (default: resolved from summoner)
    OPGG_TEST_REGION      -- op.gg region slug (default: "na")
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import pytest

from lol_pipeline.opgg_client import OpggClient, OpggRateLimitError
from lol_pipeline.sources.blob_store import BlobStore
from lol_pipeline.sources.opgg.extractors import OpggMatchExtractor

pytestmark = pytest.mark.skipif(
    os.environ.get("OPGG_LIVE_TESTS") != "1",
    reason="Set OPGG_LIVE_TESTS=1 to run live op.gg tests",
)

# ---------------------------------------------------------------------------
# Configuration -- override via env vars
# ---------------------------------------------------------------------------
_SUMMONER_ID = os.environ.get("OPGG_TEST_SUMMONER_ID", "")
_PUUID = os.environ.get("OPGG_TEST_PUUID", "")
_REGION = os.environ.get("OPGG_TEST_REGION", "na")

_BASE_URL = "https://lol-api-summoner.op.gg/api"
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.op.gg",
    "Referer": "https://www.op.gg/",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_summoner_id(http: httpx.AsyncClient) -> str:
    """Resolve a summoner_id for testing.

    If OPGG_TEST_SUMMONER_ID is set, use it directly.  Otherwise, look up
    a well-known player (Faker on KR) to get a valid summoner_id.
    """
    if _SUMMONER_ID:
        return _SUMMONER_ID

    # Fallback: look up "Hide on bush#KR1" (Faker) on KR
    region = "kr"
    url = f"{_BASE_URL}/v3/{region}/summoners"
    params = {"riot_id": "Hide on bush#KR1", "hl": "en_US"}
    resp = await http.get(url, params=params)
    resp.raise_for_status()
    return str(resp.json()["data"][0]["summoner_id"])


async def _fetch_raw_games(
    http: httpx.AsyncClient,
    summoner_id: str,
    region: str,
) -> list[dict]:
    """Fetch raw op.gg game blobs (un-normalized ``data[]`` items).

    Returns the raw list from the op.gg API before any ETL normalization.
    Retries once on 429.
    """
    url = f"{_BASE_URL}/{region}/summoners/{summoner_id}/games"
    params = {"limit": 5, "game_type": "total", "hl": "en_US"}
    try:
        resp = await http.get(url, params=params)
        if resp.status_code == 429:
            raise OpggRateLimitError()
        resp.raise_for_status()
    except (OpggRateLimitError, httpx.HTTPStatusError) as exc:
        if "429" in str(exc) or "rate" in str(exc).lower():
            await asyncio.sleep(2)
            resp = await http.get(url, params=params)
            resp.raise_for_status()
        else:
            raise
    body = resp.json()
    games = body.get("data")
    if not games:
        pytest.skip("op.gg returned no games for the test summoner")
    return games


async def _fetch_normalized_with_retry(
    client: OpggClient,
    summoner_id: str,
    region: str,
) -> list[dict]:
    """Fetch normalized match history via OpggClient, retrying once on 429."""
    try:
        return await client.get_match_history(summoner_id, region, limit=5)
    except (OpggRateLimitError, Exception) as exc:
        if "429" in str(exc) or "rate" in str(exc).lower():
            await asyncio.sleep(2)
            return await client.get_match_history(summoner_id, region, limit=5)
        raise


def _effective_region() -> str:
    """Return the region to use for testing.

    If the user set OPGG_TEST_SUMMONER_ID without OPGG_TEST_REGION, default
    to "na".  If no summoner_id is set, we fall back to "kr" (Faker lookup).
    """
    if _SUMMONER_ID:
        return _REGION
    return "kr"


async def _resolve_puuid(http: httpx.AsyncClient, region: str) -> str:
    """Return a PUUID for testing.

    If OPGG_TEST_PUUID is set, use it directly.  Otherwise, fetch the
    summoner's recent games and extract the PUUID of the first participant.
    """
    if _PUUID:
        return _PUUID

    summoner_id = await _resolve_summoner_id(http)
    raw_games = await _fetch_raw_games(http, summoner_id, region)
    # Extract a participant PUUID from the first game
    participants = raw_games[0].get("participants", [])
    for p in participants:
        puuid = p.get("summoner", {}).get("puuid", "")
        if puuid:
            return puuid
    pytest.skip("Could not resolve a test PUUID from game participants")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_real_api_shape_matches_extractor() -> None:
    """Fetch a real op.gg match blob and confirm the extractor recognizes it.

    This catches undocumented API changes (field renames, new envelope
    wrappers) that would break the extractor without mocked tests noticing.
    """
    http = httpx.AsyncClient(headers=_DEFAULT_HEADERS, timeout=httpx.Timeout(15.0))
    try:
        region = _effective_region()
        summoner_id = await _resolve_summoner_id(http)
        raw_games = await _fetch_raw_games(http, summoner_id, region)
    finally:
        await http.aclose()

    extractor = OpggMatchExtractor()

    # Every raw game blob must be recognized by the extractor
    for game in raw_games:
        assert extractor.can_extract(game), (
            f"OpggMatchExtractor.can_extract() returned False for game id={game.get('id')!r}. "
            f"Top-level keys: {sorted(game.keys())}"
        )


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_real_rate_limit_enforcement() -> None:
    """Make 3 consecutive calls and verify no 429 errors occur.

    Also asserts that calls were spaced by at least 0.8s each (allowing
    some tolerance below the 1 req/s target).
    """
    http = httpx.AsyncClient(headers=_DEFAULT_HEADERS, timeout=httpx.Timeout(15.0))
    timestamps: list[float] = []

    try:
        region = _effective_region()
        summoner_id = await _resolve_summoner_id(http)

        # Three consecutive calls with explicit 1s spacing
        for _ in range(3):
            if timestamps:
                # Respect op.gg's 1 req/s rate limit
                elapsed = time.monotonic() - timestamps[-1]
                if elapsed < 1.0:
                    await asyncio.sleep(1.0 - elapsed)

            url = f"{_BASE_URL}/{region}/summoners/{summoner_id}/games"
            params = {"limit": 1, "game_type": "total", "hl": "en_US"}
            resp = await http.get(url, params=params)
            timestamps.append(time.monotonic())

            assert resp.status_code != 429, (
                f"Got 429 on call {len(timestamps)} -- rate limiter is not spacing correctly"
            )
            resp.raise_for_status()
    finally:
        await http.aclose()

    # Verify spacing: each gap should be >= 0.8s (allowing some network jitter)
    for i in range(1, len(timestamps)):
        gap = timestamps[i] - timestamps[i - 1]
        assert gap >= 0.8, (
            f"Gap between call {i} and {i + 1} was {gap:.3f}s, expected >= 0.8s"
        )


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_real_blob_disk_write_and_roundtrip(tmp_path) -> None:
    """Fetch a real op.gg blob, write to BlobStore, and round-trip it."""
    http = httpx.AsyncClient(headers=_DEFAULT_HEADERS, timeout=httpx.Timeout(15.0))
    try:
        region = _effective_region()
        summoner_id = await _resolve_summoner_id(http)
        raw_games = await _fetch_raw_games(http, summoner_id, region)
    finally:
        await http.aclose()

    raw_game = raw_games[0]
    game_id = raw_game["id"]

    # Use the OPGG_ prefix format that the ETL produces, with a valid platform prefix
    platform = region.upper() if len(region) <= 4 else region.upper()
    # Map common op.gg region slugs to Riot platform IDs for BlobStore path validation
    platform_map = {
        "na": "NA1",
        "kr": "KR",
        "euw": "EUW1",
        "eune": "EUN1",
        "br": "BR1",
        "jp": "JP1",
        "oce": "OC1",
    }
    platform = platform_map.get(region.lower(), region.upper())
    match_id = f"{platform}_{game_id}"

    store = BlobStore(str(tmp_path))
    blob_json = json.dumps(raw_game)

    # Write
    await store.write("opgg", match_id, blob_json)

    # Assert file exists on disk
    blob_path = tmp_path / "opgg" / platform / f"{match_id}.json"
    assert blob_path.exists(), f"Blob file not found at {blob_path}"

    # Read back and verify round-trip
    read_back = await store.read("opgg", match_id)
    assert read_back is not None, "BlobStore.read() returned None"
    assert isinstance(read_back, dict), f"Expected dict, got {type(read_back)}"

    # Verify the raw game structure survived the round-trip
    assert read_back.get("id") == game_id
    assert isinstance(read_back.get("teams"), list)


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_real_canonical_output_matches_schema() -> None:
    """Fetch a real op.gg blob, run full extract pipeline, validate output fields.

    Asserts that the extracted dict contains ``gameStartTimestamp`` and
    ``gameVersion`` (added by the transformer).
    """
    http = httpx.AsyncClient(headers=_DEFAULT_HEADERS, timeout=httpx.Timeout(15.0))
    try:
        region = _effective_region()
        summoner_id = await _resolve_summoner_id(http)
        raw_games = await _fetch_raw_games(http, summoner_id, region)
    finally:
        await http.aclose()

    raw_game = raw_games[0]
    game_id = raw_game["id"]
    platform_map = {
        "na": "NA1",
        "kr": "KR",
        "euw": "EUW1",
        "eune": "EUN1",
        "br": "BR1",
        "jp": "JP1",
        "oce": "OC1",
    }
    platform = platform_map.get(region.lower(), region.upper())

    # Use Riot-format match_id (e.g. "NA1_7234567890") — the coordinator passes
    # this to extract(), which must override the ETL-generated OPGG_ prefix.
    match_id = f"{platform}_{game_id}"

    extractor = OpggMatchExtractor()

    # Full extraction pipeline: normalize + patch
    result = extractor.extract(raw_game, match_id, region)

    # --- Structural validation ---
    assert "metadata" in result
    assert "info" in result

    # metadata — regression guard: extract() must use the Riot-format match_id
    # passed by the coordinator, NOT the OPGG_ prefix generated internally.
    meta = result["metadata"]
    assert meta["match_id"] == f"{platform}_{game_id}", (
        f"metadata.match_id should be Riot-format '{platform}_{game_id}', "
        f"got {meta['match_id']!r} — extract() override may have been removed"
    )
    assert isinstance(meta.get("participants"), list)
    assert len(meta["participants"]) > 0

    # info -- critical fields added by transformer
    info = result["info"]
    assert "gameStartTimestamp" in info, (
        "patch_riot_shape() did not add gameStartTimestamp. "
        f"info keys: {sorted(info.keys())}"
    )
    assert "gameVersion" in info, (
        f"patch_riot_shape() did not add gameVersion. info keys: {sorted(info.keys())}"
    )

    # info -- standard match-v5 fields from ETL
    assert "gameDuration" in info
    assert "platformId" in info
    assert "queueId" in info
    assert info.get("source") == "opgg"

    # participants must have match-v5 required fields
    participants = info.get("participants", [])
    assert len(participants) > 0, "No participants in extracted match"

    required_participant_fields = {
        "puuid",
        "championId",
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
    for participant in participants:
        missing = required_participant_fields - set(participant.keys())
        assert not missing, (
            f"Participant {participant.get('puuid', '?')} missing fields: {missing}"
        )

    # teams
    teams = info.get("teams", [])
    assert len(teams) >= 2, f"Expected at least 2 teams, got {len(teams)}"
    for team in teams:
        assert "teamId" in team
        assert "win" in team


@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_real_on_demand_fetch_by_puuid() -> None:
    """Validate the PUUID-based summoner lookup and raw games fetch.

    This exercises the on-demand fetch path: resolve a summoner_id from a
    PUUID, then fetch raw games and verify that game IDs are integers that
    form valid Riot-format match IDs.
    """
    http = httpx.AsyncClient(headers=_DEFAULT_HEADERS, timeout=httpx.Timeout(15.0))
    try:
        region = _effective_region()
        puuid = await _resolve_puuid(http, region)
    finally:
        await http.aclose()

    # Use OpggClient (without Redis, without rate limiting) for the real calls
    client = OpggClient(httpx.AsyncClient(
        headers=_DEFAULT_HEADERS, timeout=httpx.Timeout(15.0),
    ))
    try:
        # Step 1: Resolve summoner_id from PUUID
        summoner_id = await client.get_summoner_id_by_puuid(
            puuid, region, blocking=True
        )
        assert isinstance(summoner_id, str), (
            f"Expected summoner_id to be str, got {type(summoner_id)}"
        )
        assert len(summoner_id) > 0, "summoner_id must be non-empty"

        # Respect op.gg rate limit between calls
        await asyncio.sleep(1.0)

        # Step 2: Fetch raw games
        raw_games = await client.get_raw_games(
            summoner_id, region, limit=3, blocking=True
        )
        assert isinstance(raw_games, list), (
            f"Expected list, got {type(raw_games)}"
        )
        assert len(raw_games) > 0, "Expected at least one raw game"

        # Step 3: Validate game IDs are integers
        platform_map = {
            "na": "NA1",
            "kr": "KR",
            "euw": "EUW1",
            "eune": "EUN1",
            "br": "BR1",
            "jp": "JP1",
            "oce": "OC1",
        }
        platform = platform_map.get(region.lower(), region.upper())

        for game in raw_games:
            game_id = game["id"]
            assert isinstance(game_id, int), (
                f"Expected game['id'] to be int, got {type(game_id)}: {game_id!r}"
            )
            # Riot-format match_id: platform prefix + underscore + numeric suffix
            riot_match_id = f"{platform}_{game_id}"
            assert riot_match_id.count("_") == 1, (
                f"Malformed match_id: {riot_match_id!r}"
            )
            prefix, suffix = riot_match_id.split("_", 1)
            assert prefix.isalpha() or prefix[-1].isdigit(), (
                f"Platform prefix should look like NA1/KR/EUW1, got {prefix!r}"
            )
            assert suffix.isdigit(), (
                f"Numeric suffix should be all digits, got {suffix!r}"
            )
    finally:
        await client.close()
