"""Unit tests for lol_pipeline.sources.riot.source — RiotSource and RiotExtractor.

Black-box tests written against the design spec (Section 3.5).
The implementation is being developed concurrently. These tests define
the behavioral contract that RiotSource and RiotExtractor must satisfy.

RiotSource wraps the existing RiotClient, adding:
- Non-blocking rate limit check via try_token() before each API call
- Error mapping from RiotClient exceptions to FetchResult enum values

RiotExtractor is an identity extractor: Riot blobs are already in
canonical shape, so can_extract checks for "info" and "metadata" keys
and extract returns the blob unchanged.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    RiotClient,
    ServerError,
)
from lol_pipeline.sources.base import (
    MATCH,
    FetchContext,
    FetchResponse,
    FetchResult,
)
from lol_pipeline.sources.riot.source import RiotExtractor, RiotSource


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_MATCH_DATA: dict = {
    "metadata": {
        "matchId": "NA1_12345",
        "participants": ["puuid1", "puuid2"],
    },
    "info": {
        "gameDuration": 1800,
        "gameStartTimestamp": 1700000000000,
        "gameVersion": "14.1.1",
        "participants": [],
    },
}


@pytest.fixture()
def context() -> FetchContext:
    """Standard FetchContext for a NA1 match."""
    return FetchContext(match_id="NA1_12345", puuid="test-puuid-abc", region="na1")


@pytest.fixture()
def mock_riot_client() -> AsyncMock:
    """AsyncMock standing in for RiotClient.

    By default, get_match returns SAMPLE_MATCH_DATA.
    """
    client = AsyncMock(spec=RiotClient)
    client.get_match = AsyncMock(return_value=SAMPLE_MATCH_DATA)
    client.close = AsyncMock()
    return client


@pytest.fixture()
def riot_source(mock_riot_client: AsyncMock) -> RiotSource:
    """RiotSource wired to a mocked RiotClient."""
    return RiotSource(riot_client=mock_riot_client)


@pytest.fixture()
def extractor() -> RiotExtractor:
    """A RiotExtractor instance."""
    return RiotExtractor()


# ===========================================================================
# 1. RiotSource — Static Properties
# ===========================================================================


class TestRiotSourceProperties:
    def test_name(self, riot_source: RiotSource) -> None:
        """RiotSource.name is 'riot'."""
        assert riot_source.name == "riot"

    def test_supported_data_types(self, riot_source: RiotSource) -> None:
        """RiotSource supports only the MATCH data type."""
        assert riot_source.supported_data_types == frozenset({MATCH})

    def test_required_context_keys(self, riot_source: RiotSource) -> None:
        """RiotSource needs no extra context keys (only core fields)."""
        assert riot_source.required_context_keys == frozenset()


# ===========================================================================
# 2. RiotSource.fetch — Rate Limiting
# ===========================================================================


class TestRiotSourceRateLimit:
    async def test_fetch__try_token_denied__returns_throttled(
        self, riot_source: RiotSource, mock_riot_client: AsyncMock, context: FetchContext
    ) -> None:
        """When try_token() returns False, fetch() returns THROTTLED without making any HTTP call."""
        with patch(
            "lol_pipeline.sources.riot.source.try_token",
            new_callable=AsyncMock,
            return_value=False,
        ):
            response = await riot_source.fetch(context, MATCH)

        assert response.result == FetchResult.THROTTLED
        # RiotClient.get_match must NOT have been called
        mock_riot_client.get_match.assert_not_called()

    async def test_fetch__rate_limit_error__returns_throttled(
        self, riot_source: RiotSource, context: FetchContext, mock_riot_client: AsyncMock
    ) -> None:
        """HTTP 429 from RiotClient maps to THROTTLED."""
        mock_riot_client.get_match.side_effect = RateLimitError(retry_after_ms=5000)
        with patch(
            "lol_pipeline.sources.riot.source.try_token",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await riot_source.fetch(context, MATCH)

        assert response.result == FetchResult.THROTTLED

    async def test_fetch__rate_limit_error__propagates_retry_after_ms(
        self, riot_source: RiotSource, context: FetchContext, mock_riot_client: AsyncMock
    ) -> None:
        """retry_after_ms from RateLimitError is propagated in FetchResponse."""
        mock_riot_client.get_match.side_effect = RateLimitError(retry_after_ms=3000)
        with patch(
            "lol_pipeline.sources.riot.source.try_token",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await riot_source.fetch(context, MATCH)

        assert response.retry_after_ms == 3000


# ===========================================================================
# 3. RiotSource.fetch — Success Path
# ===========================================================================


class TestRiotSourceSuccess:
    async def test_fetch__success__returns_success(
        self, riot_source: RiotSource, context: FetchContext
    ) -> None:
        """Token granted + RiotClient returns data = SUCCESS."""
        with patch(
            "lol_pipeline.sources.riot.source.try_token",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await riot_source.fetch(context, MATCH)

        assert response.result == FetchResult.SUCCESS
        assert response.data == SAMPLE_MATCH_DATA
        assert response.raw_blob is not None

    async def test_fetch__success__raw_blob_is_json_encoded(
        self, riot_source: RiotSource, context: FetchContext
    ) -> None:
        """raw_blob is json.dumps(data).encode() — the canonical JSON bytes."""
        with patch(
            "lol_pipeline.sources.riot.source.try_token",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await riot_source.fetch(context, MATCH)

        assert response.raw_blob == json.dumps(SAMPLE_MATCH_DATA).encode()

    async def test_fetch__success__available_data_types(
        self, riot_source: RiotSource, context: FetchContext
    ) -> None:
        """SUCCESS response advertises available_data_types == frozenset({MATCH})."""
        with patch(
            "lol_pipeline.sources.riot.source.try_token",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await riot_source.fetch(context, MATCH)

        assert response.available_data_types == frozenset({MATCH})


# ===========================================================================
# 4. RiotSource.fetch — Error Mapping
# ===========================================================================


class TestRiotSourceErrorMapping:
    async def test_fetch__auth_error__returns_auth_error(
        self, riot_source: RiotSource, context: FetchContext, mock_riot_client: AsyncMock
    ) -> None:
        """HTTP 403/401 from RiotClient maps to AUTH_ERROR."""
        mock_riot_client.get_match.side_effect = AuthError("forbidden")
        with patch(
            "lol_pipeline.sources.riot.source.try_token",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await riot_source.fetch(context, MATCH)

        assert response.result == FetchResult.AUTH_ERROR

    async def test_fetch__not_found__returns_not_found(
        self, riot_source: RiotSource, context: FetchContext, mock_riot_client: AsyncMock
    ) -> None:
        """HTTP 404 from RiotClient maps to NOT_FOUND."""
        mock_riot_client.get_match.side_effect = NotFoundError("not found")
        with patch(
            "lol_pipeline.sources.riot.source.try_token",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await riot_source.fetch(context, MATCH)

        assert response.result == FetchResult.NOT_FOUND

    async def test_fetch__server_error__returns_server_error(
        self, riot_source: RiotSource, context: FetchContext, mock_riot_client: AsyncMock
    ) -> None:
        """HTTP 5xx from RiotClient maps to SERVER_ERROR."""
        mock_riot_client.get_match.side_effect = ServerError("internal error", status_code=500)
        with patch(
            "lol_pipeline.sources.riot.source.try_token",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await riot_source.fetch(context, MATCH)

        assert response.result == FetchResult.SERVER_ERROR

    async def test_fetch__timeout__returns_throttled(
        self, riot_source: RiotSource, context: FetchContext, mock_riot_client: AsyncMock
    ) -> None:
        """TimeoutError (httpx read timeout, etc.) maps to THROTTLED."""
        mock_riot_client.get_match.side_effect = TimeoutError("read timed out")
        with patch(
            "lol_pipeline.sources.riot.source.try_token",
            new_callable=AsyncMock,
            return_value=True,
        ):
            response = await riot_source.fetch(context, MATCH)

        assert response.result == FetchResult.THROTTLED


# ===========================================================================
# 5. RiotExtractor — can_extract / extract
# ===========================================================================


class TestRiotExtractorCanExtract:
    def test_can_extract__valid_blob(self, extractor: RiotExtractor) -> None:
        """Blob with both 'info' and 'metadata' keys is extractable."""
        blob = {
            "info": {"gameDuration": 1800, "participants": []},
            "metadata": {"matchId": "NA1_12345"},
        }
        assert extractor.can_extract(blob) is True

    def test_can_extract__missing_info(self, extractor: RiotExtractor) -> None:
        """Blob missing 'info' key is not extractable."""
        blob = {"metadata": {"matchId": "NA1_12345"}}
        assert extractor.can_extract(blob) is False

    def test_can_extract__missing_metadata(self, extractor: RiotExtractor) -> None:
        """Blob missing 'metadata' key is not extractable."""
        blob = {"info": {"gameDuration": 1800}}
        assert extractor.can_extract(blob) is False

    def test_can_extract__empty_blob(self, extractor: RiotExtractor) -> None:
        """Empty dict is not extractable."""
        assert extractor.can_extract({}) is False


class TestRiotExtractorExtract:
    def test_extract__returns_blob_unchanged(self, extractor: RiotExtractor) -> None:
        """Riot blobs are canonical — extract() returns them as-is."""
        blob = {
            "info": {"gameDuration": 1800, "participants": []},
            "metadata": {"matchId": "NA1_12345"},
        }
        result = extractor.extract(blob, match_id="NA1_12345", region="na1")
        assert result == blob
        # Verify it is the same object (identity), not just equal
        assert result is blob


class TestRiotExtractorProperties:
    def test_source_name(self, extractor: RiotExtractor) -> None:
        """RiotExtractor.source_name matches RiotSource.name."""
        assert extractor.source_name == "riot"

    def test_data_types(self, extractor: RiotExtractor) -> None:
        """RiotExtractor handles the MATCH data type."""
        assert extractor.data_types == frozenset({MATCH})
