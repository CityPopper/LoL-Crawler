"""Unit tests for lol_pipeline.sources.opgg — OpggSource, OpggMatchExtractor, and transformer.

Tests written against the design spec (Sections 3.6, 3.8) and the existing
implementation in sources/opgg/.

OpggSource wraps OpggClient but always returns UNAVAILABLE because op.gg
has no match-by-Riot-ID endpoint. Its value comes from BlobStore cache hits.

OpggMatchExtractor validates op.gg blobs and extracts them to canonical
Riot match-v5 shape via the transformer layer.

The transformer (patch_riot_shape) patches the output of _opgg_etl.normalize_game():
- gameStartTimestamp (same value as gameCreation -- required by parser)
- gameVersion (defaults to "" if op.gg blob lacks it)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from lol_pipeline._opgg_etl import normalize_game
from lol_pipeline.opgg_client import OpggClient
from lol_pipeline.sources.base import (
    BUILD,
    MATCH,
    ExtractionError,
    FetchContext,
    FetchResult,
)
from lol_pipeline.sources.opgg.extractors import OpggMatchExtractor
from lol_pipeline.sources.opgg.source import OpggSource
from lol_pipeline.sources.opgg.transformers import patch_riot_shape


# ---------------------------------------------------------------------------
# Fixtures — minimal but realistic op.gg blob
# ---------------------------------------------------------------------------

# This matches the shape that _opgg_etl.normalize_game() expects:
#   - top-level "id" (required, KeyError if missing)
#   - "created_at" (ISO timestamp for gameCreation)
#   - "teams" (list of team dicts with "participants" and "game_stat")
#   - "game_length_second", "game_type", "queue_info"

MINIMAL_OPGG_GAME_BLOB: dict = {
    "id": "abc123",
    "created_at": "2024-06-15T12:30:00+00:00",
    "game_length_second": 1800,
    "game_type": "CLASSIC",
    "queue_info": {"queue_id": 420},
    "teams": [
        {
            "game_stat": {"is_win": True, "kill": 15, "death": 8},
            "participants": [
                {
                    "summoner": {"puuid": "puuid-aaa", "summoner_id": "sid-1"},
                    "champion_id": 1,
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
            ],
        },
        {
            "game_stat": {"is_win": False, "kill": 8, "death": 15},
            "participants": [
                {
                    "summoner": {"puuid": "puuid-bbb", "summoner_id": "sid-2"},
                    "champion_id": 2,
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
        },
    ],
}


@pytest.fixture()
def context() -> FetchContext:
    """Standard FetchContext for an NA match."""
    return FetchContext(match_id="OPGG_NA1_abc123", puuid="puuid-aaa", region="na")


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
# 2. OpggSource.fetch — Always UNAVAILABLE
# ===========================================================================


class TestOpggSourceFetch:
    async def test_fetch__always_returns_unavailable(
        self, opgg_source: OpggSource, context: FetchContext
    ) -> None:
        """For any FetchContext and any DataType, fetch() returns UNAVAILABLE."""
        for data_type in (MATCH, BUILD, "timeline", "unknown_type"):
            response = await opgg_source.fetch(context, data_type)
            assert response.result == FetchResult.UNAVAILABLE

    async def test_fetch__match_unavailable(
        self, opgg_source: OpggSource, context: FetchContext
    ) -> None:
        """Specifically for MATCH data type, returns UNAVAILABLE."""
        response = await opgg_source.fetch(context, MATCH)
        assert response.result == FetchResult.UNAVAILABLE
        assert response.raw_blob is None
        assert response.data is None

    async def test_fetch__build_unavailable(
        self, opgg_source: OpggSource, context: FetchContext
    ) -> None:
        """Specifically for BUILD data type, returns UNAVAILABLE."""
        response = await opgg_source.fetch(context, BUILD)
        assert response.result == FetchResult.UNAVAILABLE
        assert response.raw_blob is None
        assert response.data is None


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
            MINIMAL_OPGG_GAME_BLOB, match_id="OPGG_NA1_abc123", region="na"
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
            MINIMAL_OPGG_GAME_BLOB, match_id="OPGG_NA1_abc123", region="na"
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
            MINIMAL_OPGG_GAME_BLOB, match_id="OPGG_NA1_abc123", region="na"
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
        bad_blob: dict = {"id": "bad-game", "teams": [None, "not-a-dict"]}
        # Sanity: can_extract passes for this blob
        assert extractor.can_extract(bad_blob) is True
        with pytest.raises(ExtractionError):
            extractor.extract(bad_blob, match_id="OPGG_NA1_bad", region="na")


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
