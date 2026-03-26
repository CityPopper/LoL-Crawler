"""Unit tests for lol_pipeline.sources.coordinator — WaterfallCoordinator.

Black-box tests written against the design spec (Section 3.9).
All tests use synthetic source names ("alpha", "beta", "gamma") — no real
source names ("riot", "opgg", etc.) are permitted.

The coordinator file is being implemented concurrently. These tests define
the behavioral contract the implementation must satisfy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import httpx
import pytest

from lol_pipeline.raw_store import RawStore
from lol_pipeline.sources.base import (
    MATCH,
    BUILD,
    DataType,
    Extractor,
    FetchContext,
    FetchResponse,
    FetchResult,
    Source,
    WaterfallResult,
)
from lol_pipeline.sources.blob_store import BlobStore, MAX_BLOB_SIZE_BYTES
from lol_pipeline.sources.coordinator import WaterfallCoordinator
from lol_pipeline.sources.registry import SourceEntry, SourceRegistry


# ---------------------------------------------------------------------------
# Mock source and extractor factories
# ---------------------------------------------------------------------------


def make_mock_source(
    name: str,
    data_types: frozenset[DataType],
    fetch_result: FetchResult = FetchResult.SUCCESS,
    raw_blob: bytes = b'{"mock": true}',
    retry_after_ms: int | None = None,
    available_data_types: frozenset[DataType] | None = None,
    required_context_keys: frozenset[str] = frozenset(),
) -> Source:
    """Create a mock source for coordinator tests.

    Uses only synthetic names — never real source names.
    The returned object satisfies the Source protocol with real async methods
    (not AsyncMock) to catch type errors early.
    """

    class MockSource:
        _fetch_called: bool = False

        @property
        def name(self) -> str:
            return name

        @property
        def supported_data_types(self) -> frozenset[DataType]:
            return data_types

        @property
        def required_context_keys(self) -> frozenset[str]:
            return required_context_keys

        async def fetch(self, context: FetchContext, data_type: DataType) -> FetchResponse:
            self._fetch_called = True
            return FetchResponse(
                result=fetch_result,
                raw_blob=raw_blob if fetch_result == FetchResult.SUCCESS else None,
                retry_after_ms=retry_after_ms,
                available_data_types=available_data_types or frozenset(),
            )

        async def close(self) -> None:
            pass

    return MockSource()


def make_mock_extractor(
    source_name: str,
    data_types: frozenset[DataType],
    can_extract_result: bool = True,
    extract_result: dict | None = None,
) -> Extractor:
    """Create a mock extractor that returns canned results.

    Uses only synthetic names — never real source names.
    """

    class MockExtractor:
        @property
        def source_name(self) -> str:
            return source_name

        @property
        def data_types(self) -> frozenset[DataType]:
            return data_types

        def can_extract(self, blob: dict[str, str]) -> bool:
            return can_extract_result

        def extract(self, blob: dict[str, str], match_id: str, region: str) -> dict[str, str]:
            if extract_result is not None:
                return extract_result
            return {"extracted": "true", "match_id": match_id, "region": region}

    return MockExtractor()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.flushall()
    await r.aclose()


@pytest.fixture
def ctx() -> FetchContext:
    """Standard FetchContext for all tests."""
    return FetchContext(match_id="NA1_12345", puuid="abc123", region="na1")


def _build_coordinator(
    sources: list[tuple[str, Source, int, frozenset[DataType]]],
    extractors: list[Extractor],
    blob_store: BlobStore | None,
    raw_store: RawStore,
) -> WaterfallCoordinator:
    """Build a WaterfallCoordinator from simplified source tuples.

    Each tuple is (name, source, priority, primary_for).
    """
    entries = [
        SourceEntry(
            name=name,
            source=src,  # type: ignore[arg-type]
            priority=priority,
            primary_for=primary_for,
        )
        for name, src, priority, primary_for in sources
    ]
    extractor_index: dict[tuple[str, DataType], Extractor] = {
        (ext.source_name, dt): ext  # type: ignore[union-attr]
        for ext in extractors
        for dt in ext.data_types  # type: ignore[union-attr]
    }
    registry = SourceRegistry(entries, extractor_index=extractor_index)  # type: ignore[arg-type]
    return WaterfallCoordinator(
        registry=registry,
        blob_store=blob_store,
        raw_store=raw_store,
        extractors=extractors,
    )


# ===========================================================================
# Core waterfall path tests
# ===========================================================================


class TestSingleSourceSuccess:
    """1. [alpha(SUCCESS)] -> returns status='success', data from alpha."""

    async def test_single_source_success(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[("alpha", alpha_src, 0, frozenset())],
            extractors=[alpha_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "success"
        assert result.source == "alpha"
        assert result.data is not None


class TestFirstThrottledSecondSucceeds:
    """2. [alpha(THROTTLED), beta(SUCCESS)] -> skips alpha, returns beta."""

    async def test_first_throttled_second_succeeds(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}), fetch_result=FetchResult.THROTTLED)
        beta_src = make_mock_source("beta", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "success"
        assert result.source == "beta"


class TestAllThrottled:
    """3. [alpha(THROTTLED), beta(THROTTLED)] -> returns status='all_exhausted'."""

    async def test_all_throttled(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}), fetch_result=FetchResult.THROTTLED)
        beta_src = make_mock_source("beta", frozenset({MATCH}), fetch_result=FetchResult.THROTTLED)
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "all_exhausted"


class TestPrimaryNotFoundIsTerminal:
    """4. [alpha(NOT_FOUND, primary_for={MATCH})] -> returns status='not_found',
    does NOT try beta."""

    async def test_primary_not_found_is_terminal(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}), fetch_result=FetchResult.NOT_FOUND)
        beta_src = make_mock_source("beta", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset({MATCH})),  # primary for MATCH
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "not_found"
        # beta should NOT have been called
        assert not beta_src._fetch_called  # type: ignore[attr-defined]


class TestNonprimaryNotFoundTriesNext:
    """5. [alpha(NOT_FOUND, primary_for={}), beta(SUCCESS)] -> treats alpha
    NOT_FOUND as UNAVAILABLE, returns beta."""

    async def test_nonprimary_not_found_tries_next(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}), fetch_result=FetchResult.NOT_FOUND)
        beta_src = make_mock_source("beta", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),  # NOT primary
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "success"
        assert result.source == "beta"


class TestPrimaryAuthErrorIsTerminal:
    """6. [alpha(AUTH_ERROR, primary_for={MATCH})] -> returns status='auth_error'."""

    async def test_primary_auth_error_is_terminal(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}), fetch_result=FetchResult.AUTH_ERROR)
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[("alpha", alpha_src, 0, frozenset({MATCH}))],
            extractors=[alpha_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "auth_error"


class TestNonprimaryAuthErrorTriesNext:
    """7. [alpha(AUTH_ERROR, primary_for={}), beta(SUCCESS)] -> returns beta."""

    async def test_nonprimary_auth_error_tries_next(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}), fetch_result=FetchResult.AUTH_ERROR)
        beta_src = make_mock_source("beta", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "success"
        assert result.source == "beta"


class TestRawstoreIdempotency:
    """8. RawStore already has match -> returns status='cached' immediately,
    no source fetched."""

    async def test_rawstore_idempotency(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        # Pre-populate RawStore
        await raw_store.set("NA1_12345", '{"already": "stored"}')

        alpha_src = make_mock_source("alpha", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[("alpha", alpha_src, 0, frozenset())],
            extractors=[alpha_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "cached"
        # alpha.fetch should NOT have been called
        assert not alpha_src._fetch_called  # type: ignore[attr-defined]


class TestBlobstoreCacheHit:
    """9. BlobStore has blob + matching extractor -> returns status='cached',
    no network call."""

    async def test_blobstore_cache_hit(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        # Pre-populate BlobStore
        await blob_store.write("alpha", "NA1_12345", b'{"cached": "blob"}')

        alpha_src = make_mock_source("alpha", frozenset({MATCH}))
        alpha_ext = make_mock_extractor(
            "alpha",
            frozenset({MATCH}),
            can_extract_result=True,
            extract_result={"extracted": "from_cache"},
        )

        coord = _build_coordinator(
            sources=[("alpha", alpha_src, 0, frozenset())],
            extractors=[alpha_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "cached"
        assert result.data is not None
        # alpha.fetch should NOT have been called — cache hit
        assert not alpha_src._fetch_called  # type: ignore[attr-defined]


class TestSourceFiltersByDataType:
    """10. [alpha(supports=MATCH), beta(supports=BUILD)] -> only alpha tried
    for MATCH fetch."""

    async def test_source_filters_by_data_type(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}))
        beta_src = make_mock_source("beta", frozenset({BUILD}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({BUILD}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "success"
        assert result.source == "alpha"
        # beta should NOT have been called — doesn't support MATCH
        assert not beta_src._fetch_called  # type: ignore[attr-defined]


class TestThreeSourcesMiddleSucceeds:
    """11. [alpha(THROTTLED), beta(SUCCESS), gamma(UNAVAILABLE)] ->
    gamma.fetch never called."""

    async def test_three_sources_middle_succeeds(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}), fetch_result=FetchResult.THROTTLED)
        beta_src = make_mock_source("beta", frozenset({MATCH}))
        gamma_src = make_mock_source("gamma", frozenset({MATCH}), fetch_result=FetchResult.UNAVAILABLE)
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))
        gamma_ext = make_mock_extractor("gamma", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),
                ("beta", beta_src, 1, frozenset()),
                ("gamma", gamma_src, 2, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext, gamma_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "success"
        assert result.source == "beta"
        # gamma.fetch should NOT have been called — beta succeeded
        assert not gamma_src._fetch_called  # type: ignore[attr-defined]


class TestZeroSourcesForDataType:
    """12. Registry has no sources for 'timeline' -> returns status='all_exhausted'
    immediately."""

    async def test_zero_sources_for_data_type(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        # Only alpha supports MATCH, not "timeline"
        alpha_src = make_mock_source("alpha", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[("alpha", alpha_src, 0, frozenset())],
            extractors=[alpha_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, "timeline")

        assert result.status == "all_exhausted"


class TestRetryAfterMsPropagated:
    """13. alpha returns THROTTLED with retry_after_ms=5000, beta THROTTLED with
    retry_after_ms=3000 -> result.retry_after_ms == 5000 (max)."""

    async def test_retry_after_ms_propagated(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source(
            "alpha", frozenset({MATCH}),
            fetch_result=FetchResult.THROTTLED,
            retry_after_ms=5000,
        )
        beta_src = make_mock_source(
            "beta", frozenset({MATCH}),
            fetch_result=FetchResult.THROTTLED,
            retry_after_ms=3000,
        )
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "all_exhausted"
        assert result.retry_after_ms == 5000


class TestBlobValidationFailedFlag:
    """14. alpha returns SUCCESS but extractor.can_extract() returns False ->
    result.blob_validation_failed == True."""

    async def test_blob_validation_failed_flag(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}))
        alpha_ext = make_mock_extractor(
            "alpha", frozenset({MATCH}), can_extract_result=False,
        )

        coord = _build_coordinator(
            sources=[("alpha", alpha_src, 0, frozenset())],
            extractors=[alpha_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "all_exhausted"
        assert result.blob_validation_failed is True


class TestBlobTooLargeTreatedAsUnavailable:
    """15. Source returns raw_blob > MAX_BLOB_SIZE_BYTES -> coordinator skips,
    tries next source."""

    async def test_blob_too_large_treated_as_unavailable(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        # Create an oversized blob (just over the limit)
        oversized_blob = b'{"data": "' + b"x" * (MAX_BLOB_SIZE_BYTES + 1) + b'"}'

        alpha_src = make_mock_source(
            "alpha", frozenset({MATCH}), raw_blob=oversized_blob,
        )
        beta_src = make_mock_source("beta", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "success"
        assert result.source == "beta"
        # Oversized blobs are NOT treated as blob_validation_failed
        assert result.blob_validation_failed is False


class TestRequiredContextKeysMissingSkipsSource:
    """16. Source requires 'summoner_id' in extra, context.extra is empty ->
    source skipped with warning."""

    async def test_required_context_keys_missing_skips_source(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        # alpha requires "summoner_id" which is not in ctx.extra
        alpha_src = make_mock_source(
            "alpha", frozenset({MATCH}),
            required_context_keys=frozenset({"summoner_id"}),
        )
        beta_src = make_mock_source("beta", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "success"
        assert result.source == "beta"
        # alpha.fetch should NOT have been called
        assert not alpha_src._fetch_called  # type: ignore[attr-defined]


class TestBlobstoreCacheHitWithCanExtractFalse:
    """17. BlobStore has blob but can_extract() returns False -> falls through
    to network fetch."""

    async def test_blobstore_cache_hit_with_can_extract_false(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        # Pre-populate BlobStore
        await blob_store.write("alpha", "NA1_12345", b'{"stale": "blob"}')

        alpha_src = make_mock_source("alpha", frozenset({MATCH}))
        alpha_ext = make_mock_extractor(
            "alpha", frozenset({MATCH}), can_extract_result=False,
        )
        beta_src = make_mock_source("beta", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        # Should fall through to network fetch since can_extract was False
        assert result.status == "success"
        assert result.data is not None


class TestBlobstoreCacheHitWithMissingExtractor:
    """18. BlobStore has blob but no extractor registered for (source_name,
    data_type) -> falls through to network fetch."""

    async def test_blobstore_cache_hit_with_missing_extractor(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        # Pre-populate BlobStore with a blob from "delta" — a source with no extractor
        await blob_store.write("delta", "NA1_12345", b'{"orphan": "blob"}')

        # Only alpha has an extractor — delta does not
        alpha_src = make_mock_source("alpha", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[("alpha", alpha_src, 0, frozenset())],
            extractors=[alpha_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        # Should fall through to network fetch since no extractor for "delta"
        assert result.status == "success"
        assert result.source == "alpha"


class TestServerErrorTriesNext:
    """19. [alpha(SERVER_ERROR), beta(SUCCESS)] -> returns beta."""

    async def test_server_error_tries_next(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source(
            "alpha", frozenset({MATCH}), fetch_result=FetchResult.SERVER_ERROR,
        )
        beta_src = make_mock_source("beta", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))
        beta_ext = make_mock_extractor("beta", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[
                ("alpha", alpha_src, 0, frozenset()),
                ("beta", beta_src, 1, frozenset()),
            ],
            extractors=[alpha_ext, beta_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "success"
        assert result.source == "beta"


class TestSuccessfulFetchWritesToBlobstore:
    """20. After SUCCESS, blob should exist in BlobStore."""

    async def test_successful_fetch_writes_to_blobstore(self, redis, ctx, tmp_path) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        alpha_src = make_mock_source("alpha", frozenset({MATCH}))
        alpha_ext = make_mock_extractor("alpha", frozenset({MATCH}))

        coord = _build_coordinator(
            sources=[("alpha", alpha_src, 0, frozenset())],
            extractors=[alpha_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "success"
        # Verify blob was written to BlobStore
        assert await blob_store.exists("alpha", "NA1_12345") is True
        blob_data = await blob_store.read("alpha", "NA1_12345")
        assert blob_data is not None


# ===========================================================================
# try_token tests
# ===========================================================================


class TestTryTokenReturnsTrue:
    """21. try_token returns True when token is available."""

    async def test_try_token_returns_true_when_token_available(self) -> None:
        mock_response = httpx.Response(200, json={"granted": True})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        from lol_pipeline.rate_limiter_client import try_token

        with patch("lol_pipeline.rate_limiter_client._client", mock_client):
            result = await try_token("alpha", "endpoint")

        assert result is True


class TestTryTokenReturnsFalse:
    """22. try_token returns False when limit is exhausted."""

    async def test_try_token_returns_false_when_limit_exhausted(self) -> None:
        mock_response = httpx.Response(200, json={"granted": False, "retry_after_ms": 1000})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        from lol_pipeline.rate_limiter_client import try_token

        with patch("lol_pipeline.rate_limiter_client._client", mock_client):
            result = await try_token("alpha", "endpoint")

        assert result is False
