"""Unit tests for lol_pipeline.sources.opgg — OpggSource, OpggMatchExtractor, and transformer.

Tests written against the design spec (Sections 3.6, 3.8) and the existing
implementation in sources/opgg/.

OpggSource wraps OpggClient and fetches match data by looking up the player's
recent games via PUUID. It returns SUCCESS when the target game is found by
integer ID, THROTTLED on rate limits, and UNAVAILABLE for all other cases.

OpggMatchExtractor validates op.gg blobs and extracts them to canonical
Riot match-v5 shape via the transformer layer.

The transformer (patch_riot_shape) patches the output of _opgg_etl.normalize_game():
- gameStartTimestamp (same value as gameCreation -- required by parser)
- gameVersion (defaults to "" if op.gg blob lacks it)
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest

from lol_pipeline._opgg_etl import normalize_game
from lol_pipeline.opgg_client import OpggClient, OpggRateLimitError
from lol_pipeline.sources.base import (
    BUILD,
    MATCH,
    ExtractionError,
    FetchContext,
    FetchResult,
)
from lol_pipeline.sources.blob_store import MAX_BLOB_SIZE_BYTES
from lol_pipeline.sources.opgg.extractors import OpggMatchExtractor
from lol_pipeline.sources.opgg.source import OpggSource
from lol_pipeline.sources.opgg.transformers import patch_riot_shape


# ---------------------------------------------------------------------------
# Fixtures — minimal but realistic op.gg blob
# ---------------------------------------------------------------------------

# This matches the real op.gg API shape that _opgg_etl.normalize_game() expects:
#   - top-level "id" (integer — Riot numeric game ID)
#   - "created_at" (ISO timestamp for gameCreation)
#   - "queue_id" (top-level integer, NOT nested in "queue_info")
#   - "participants" (top-level list with "team_key" per participant)
#   - "teams" (list of team dicts with "key" and "game_stat", NO "participants")
#   - "game_length_second", "game_type"

MINIMAL_OPGG_GAME_BLOB: dict = {
    "id": 7234567890,
    "created_at": "2024-06-15T12:30:00+00:00",
    "game_length_second": 1800,
    "game_type": "CLASSIC",
    "queue_id": 420,
    "participants": [
        {
            "summoner": {"puuid": "puuid-aaa", "summoner_id": "sid-1"},
            "champion_id": 1,
            "team_key": "BLUE",
            "position": "TOP",
            "stats": {
                "kill": 5,
                "death": 2,
                "assist": 3,
                "cs": 180,
                "damage_dealt_to_champions": 15000,
            },
            "items": [3071, 3006, 3053, 3065, 3075, 3143, 3340],
        },
        {
            "summoner": {"puuid": "puuid-bbb", "summoner_id": "sid-2"},
            "champion_id": 2,
            "team_key": "RED",
            "position": "MID",
            "stats": {
                "kill": 3,
                "death": 5,
                "assist": 2,
                "cs": 160,
                "damage_dealt_to_champions": 12000,
            },
            "items": [3157, 3020, 3089, 3135, 3165, 3102, 3340],
        },
    ],
    "teams": [
        {"key": "BLUE", "game_stat": {"is_win": True, "kill": 15, "death": 8}},
        {"key": "RED", "game_stat": {"is_win": False, "kill": 8, "death": 15}},
    ],
}


@pytest.fixture()
def context() -> FetchContext:
    """Standard FetchContext for an NA match."""
    return FetchContext(match_id="NA1_7234567890", puuid="puuid-aaa", region="na")


@pytest.fixture()
def mock_opgg_client() -> AsyncMock:
    """AsyncMock standing in for OpggClient."""
    client = AsyncMock(spec=OpggClient)
    client.close = AsyncMock()
    return client


@pytest.fixture()
def opgg_source(mock_opgg_client: AsyncMock) -> OpggSource:
    """OpggSource wired to a mocked OpggClient."""
    return OpggSource(opgg_client=mock_opgg_client)


@pytest.fixture()
def extractor() -> OpggMatchExtractor:
    """An OpggMatchExtractor instance."""
    return OpggMatchExtractor()


# ===========================================================================
# 1. OpggSource — Static Properties
# ===========================================================================


class TestOpggSourceProperties:
    def test_name(self, opgg_source: OpggSource) -> None:
        """OpggSource.name is 'opgg'."""
        assert opgg_source.name == "opgg"

    def test_supported_data_types(self, opgg_source: OpggSource) -> None:
        """OpggSource supports MATCH data type only (no BUILD extractor exists)."""
        assert opgg_source.supported_data_types == frozenset({MATCH})

    def test_required_context_keys(self, opgg_source: OpggSource) -> None:
        """OpggSource needs no extra context keys (only core fields)."""
        assert opgg_source.required_context_keys == frozenset()


# ===========================================================================
# 2. OpggSource.fetch
# ===========================================================================


class TestOpggSourceFetch:
    async def test_fetch__build_unavailable(
        self, opgg_source: OpggSource, context: FetchContext
    ) -> None:
        """Specifically for BUILD data type, returns UNAVAILABLE."""
        response = await opgg_source.fetch(context, BUILD)
        assert response.result == FetchResult.UNAVAILABLE
        assert response.raw_blob is None
        assert response.data is None

    async def test_fetch__empty_puuid__returns_unavailable_with_warning(
        self,
        opgg_source: OpggSource,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Context with empty puuid returns UNAVAILABLE and logs a warning."""
        ctx = FetchContext(match_id="NA1_7234567890", puuid="", region="na")
        with caplog.at_level(logging.WARNING):
            response = await opgg_source.fetch(ctx, MATCH)
        assert response.result == FetchResult.UNAVAILABLE
        assert any("empty puuid" in r.message for r in caplog.records)

    async def test_fetch__unknown_region__returns_unavailable(
        self,
        mock_opgg_client: AsyncMock,
    ) -> None:
        """Region 'PH2' is not in any region map; returns UNAVAILABLE."""
        source = OpggSource(opgg_client=mock_opgg_client)
        ctx = FetchContext(match_id="PH2_7234567890", puuid="puuid-aaa", region="PH2")
        response = await source.fetch(ctx, MATCH)
        assert response.result == FetchResult.UNAVAILABLE

    async def test_fetch__opgg_rate_limit__returns_throttled(
        self,
        opgg_source: OpggSource,
        mock_opgg_client: AsyncMock,
    ) -> None:
        """OpggRateLimitError from get_summoner_id_by_puuid yields THROTTLED."""
        mock_opgg_client.get_summoner_id_by_puuid.side_effect = (
            OpggRateLimitError(retry_ms=5000)
        )
        ctx = FetchContext(match_id="NA1_7234567890", puuid="puuid-aaa", region="NA1")
        response = await opgg_source.fetch(ctx, MATCH)
        assert response.result == FetchResult.THROTTLED
        assert response.retry_after_ms == 5000

    async def test_fetch__httpx_timeout__returns_unavailable(
        self,
        opgg_source: OpggSource,
        mock_opgg_client: AsyncMock,
    ) -> None:
        """httpx.TimeoutException from get_summoner_id_by_puuid yields UNAVAILABLE."""
        mock_opgg_client.get_summoner_id_by_puuid.side_effect = (
            httpx.TimeoutException("timed out")
        )
        ctx = FetchContext(match_id="NA1_7234567890", puuid="puuid-aaa", region="NA1")
        response = await opgg_source.fetch(ctx, MATCH)
        assert response.result == FetchResult.UNAVAILABLE

    async def test_fetch__game_found_by_integer_id__returns_success(
        self,
        opgg_source: OpggSource,
        mock_opgg_client: AsyncMock,
    ) -> None:
        """Game found by integer ID match returns SUCCESS with raw_blob."""
        mock_opgg_client.get_summoner_id_by_puuid.return_value = "sum123"
        mock_opgg_client.get_raw_games.return_value = [
            {"id": 7234567890, "champion": {"name": "Garen"}},
        ]
        ctx = FetchContext(match_id="NA1_7234567890", puuid="puuid-aaa", region="NA1")
        response = await opgg_source.fetch(ctx, MATCH)
        assert response.result == FetchResult.SUCCESS
        assert response.raw_blob is not None
        assert len(response.raw_blob) > 0
        assert response.data is not None
        assert response.data["id"] == 7234567890

    async def test_fetch__game_not_in_list__returns_unavailable(
        self,
        opgg_source: OpggSource,
        mock_opgg_client: AsyncMock,
    ) -> None:
        """Games list doesn't contain target ID; returns UNAVAILABLE."""
        mock_opgg_client.get_summoner_id_by_puuid.return_value = "sum123"
        mock_opgg_client.get_raw_games.return_value = [
            {"id": 9999999999, "champion": {"name": "Lux"}},
            {"id": 8888888888, "champion": {"name": "Ahri"}},
        ]
        ctx = FetchContext(match_id="NA1_7234567890", puuid="puuid-aaa", region="NA1")
        response = await opgg_source.fetch(ctx, MATCH)
        assert response.result == FetchResult.UNAVAILABLE

    async def test_fetch__blob_over_size_limit__returns_unavailable(
        self,
        opgg_source: OpggSource,
        mock_opgg_client: AsyncMock,
    ) -> None:
        """Game found but blob exceeds MAX_BLOB_SIZE_BYTES; returns UNAVAILABLE."""
        oversized_game = {
            "id": 7234567890,
            "huge_field": "x" * (MAX_BLOB_SIZE_BYTES + 1),
        }
        mock_opgg_client.get_summoner_id_by_puuid.return_value = "sum123"
        mock_opgg_client.get_raw_games.return_value = [oversized_game]
        ctx = FetchContext(match_id="NA1_7234567890", puuid="puuid-aaa", region="NA1")
        response = await opgg_source.fetch(ctx, MATCH)
        assert response.result == FetchResult.UNAVAILABLE

    async def test_fetch__region_normalization__na1_lowercase(
        self,
        opgg_source: OpggSource,
        mock_opgg_client: AsyncMock,
    ) -> None:
        """Region 'na1' (lowercase Riot platform) maps to opgg_region 'na'."""
        mock_opgg_client.get_summoner_id_by_puuid.return_value = "sum123"
        mock_opgg_client.get_raw_games.return_value = [
            {"id": 7234567890, "champion": {"name": "Garen"}},
        ]
        ctx = FetchContext(match_id="NA1_7234567890", puuid="puuid-aaa", region="na1")
        response = await opgg_source.fetch(ctx, MATCH)
        assert response.result == FetchResult.SUCCESS
        # Verify the opgg_region passed to the client was correct
        mock_opgg_client.get_summoner_id_by_puuid.assert_called_once_with(
            "puuid-aaa", "na", blocking=False
        )

    async def test_fetch__region_normalization__NA1_uppercase(
        self,
        opgg_source: OpggSource,
        mock_opgg_client: AsyncMock,
    ) -> None:
        """Region 'NA1' (uppercase Riot platform) maps to opgg_region 'na'."""
        mock_opgg_client.get_summoner_id_by_puuid.return_value = "sum123"
        mock_opgg_client.get_raw_games.return_value = [
            {"id": 7234567890, "champion": {"name": "Garen"}},
        ]
        ctx = FetchContext(match_id="NA1_7234567890", puuid="puuid-aaa", region="NA1")
        response = await opgg_source.fetch(ctx, MATCH)
        assert response.result == FetchResult.SUCCESS
        mock_opgg_client.get_summoner_id_by_puuid.assert_called_once_with(
            "puuid-aaa", "na", blocking=False
        )


# ===========================================================================
# 3. OpggMatchExtractor — Properties
# ===========================================================================


class TestOpggExtractorProperties:
    def test_extractor_source_name(self, extractor: OpggMatchExtractor) -> None:
        """OpggMatchExtractor.source_name matches OpggSource.name."""
        assert extractor.source_name == "opgg"

    def test_extractor_data_types(self, extractor: OpggMatchExtractor) -> None:
        """OpggMatchExtractor handles the MATCH data type."""
        assert extractor.data_types == frozenset({MATCH})


# ===========================================================================
# 4. OpggMatchExtractor — can_extract
# ===========================================================================


class TestOpggExtractorCanExtract:
    def test_can_extract__valid_blob(self, extractor: OpggMatchExtractor) -> None:
        """Blob with required op.gg keys ('id' and 'teams') is extractable."""
        assert extractor.can_extract(MINIMAL_OPGG_GAME_BLOB) is True

    def test_can_extract__invalid_blob_empty_dict(
        self, extractor: OpggMatchExtractor
    ) -> None:
        """Empty dict is not extractable."""
        assert extractor.can_extract({}) is False

    def test_can_extract__invalid_blob_missing_id(
        self, extractor: OpggMatchExtractor
    ) -> None:
        """Blob missing the 'id' key (required by normalize_game) is not extractable."""
        blob = {k: v for k, v in MINIMAL_OPGG_GAME_BLOB.items() if k != "id"}
        assert extractor.can_extract(blob) is False

    def test_can_extract__invalid_blob_missing_teams(
        self, extractor: OpggMatchExtractor
    ) -> None:
        """Blob missing 'teams' key is not extractable."""
        blob = {k: v for k, v in MINIMAL_OPGG_GAME_BLOB.items() if k != "teams"}
        assert extractor.can_extract(blob) is False


# ===========================================================================
# 5. OpggMatchExtractor — extract
# ===========================================================================


class TestOpggExtractorExtract:
    def test_extract__produces_game_start_timestamp(
        self, extractor: OpggMatchExtractor
    ) -> None:
        """Extracted dict has 'gameStartTimestamp' in info (required by parser)."""
        result = extractor.extract(
            MINIMAL_OPGG_GAME_BLOB, match_id="NA1_7234567890", region="na"
        )
        assert "gameStartTimestamp" in result["info"]

    def test_extract__game_start_timestamp_equals_game_creation(
        self, extractor: OpggMatchExtractor
    ) -> None:
        """gameStartTimestamp and gameCreation have the same value.

        Op.gg does not distinguish between creation time and start time,
        so the transformer sets both to the same epoch millisecond value.
        """
        result = extractor.extract(
            MINIMAL_OPGG_GAME_BLOB, match_id="NA1_7234567890", region="na"
        )
        info = result["info"]
        assert info["gameStartTimestamp"] == info["gameCreation"]
        # Verify the value is a positive epoch ms (not zero, not a string)
        assert isinstance(info["gameStartTimestamp"], int)
        assert info["gameStartTimestamp"] > 0

    def test_extract__produces_game_version(
        self, extractor: OpggMatchExtractor
    ) -> None:
        """Extracted dict has 'gameVersion' key in info (even if empty string)."""
        result = extractor.extract(
            MINIMAL_OPGG_GAME_BLOB, match_id="NA1_7234567890", region="na"
        )
        assert "gameVersion" in result["info"]

    def test_extract__raises_extraction_error_on_bad_blob(
        self, extractor: OpggMatchExtractor
    ) -> None:
        """Malformed blob that passes can_extract but fails normalize raises ExtractionError.

        The blob has 'id' and 'teams' (a list) so can_extract() returns True,
        but the list entries are not dicts, causing normalize_game() to fail
        with a TypeError when it tries to call .get() on them.
        """
        bad_blob: dict = {"id": 9999, "teams": [None, "not-a-dict"]}
        # Sanity: can_extract passes for this blob
        assert extractor.can_extract(bad_blob) is True
        with pytest.raises(ExtractionError):
            extractor.extract(bad_blob, match_id="NA1_9999", region="na")

    def test_extract__metadata_match_id_uses_riot_format(
        self, extractor: OpggMatchExtractor
    ) -> None:
        """Regression: metadata.match_id uses the Riot-format match_id passed in,
        not the OPGG-prefixed ID generated by normalize_game().
        """
        result = extractor.extract(
            MINIMAL_OPGG_GAME_BLOB, match_id="NA1_7234567890", region="na"
        )
        assert result["metadata"]["match_id"] == "NA1_7234567890"


# ===========================================================================
# 6. Transformer — patch_riot_shape
# ===========================================================================


class TestOpggTransformer:
    """Tests for patch_riot_shape, which patches normalize_game() output.

    patch_riot_shape operates on the already-normalized dict (the output
    of _opgg_etl.normalize_game()), NOT on the raw op.gg blob. Tests
    first call normalize_game() to produce the input, then patch it.
    """

    def test_transformer__adds_game_start_timestamp(self) -> None:
        """patch_riot_shape output includes gameStartTimestamp in info."""
        normalized = normalize_game(MINIMAL_OPGG_GAME_BLOB, region="na")
        result = patch_riot_shape(normalized)
        assert "gameStartTimestamp" in result["info"]

    def test_transformer__game_start_timestamp_matches_game_creation(self) -> None:
        """gameStartTimestamp has the same value as gameCreation."""
        normalized = normalize_game(MINIMAL_OPGG_GAME_BLOB, region="na")
        result = patch_riot_shape(normalized)
        info = result["info"]
        assert info["gameStartTimestamp"] == info["gameCreation"]

    def test_transformer__adds_game_version_when_missing(self) -> None:
        """gameVersion defaults to '' when op.gg blob has no version info."""
        normalized = normalize_game(MINIMAL_OPGG_GAME_BLOB, region="na")
        # normalize_game does not produce gameVersion, so patch should add it
        assert "gameVersion" not in normalized["info"]
        result = patch_riot_shape(normalized)
        assert "gameVersion" in result["info"]
        assert isinstance(result["info"]["gameVersion"], str)
