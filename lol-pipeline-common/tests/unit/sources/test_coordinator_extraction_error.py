"""Unit tests for ExtractionError handling in the WaterfallCoordinator fresh-fetch path.

Phase 6 production readiness review (F5/F14): verifies that ExtractionError raised
during extractor.extract() in the _handle_success path is caught gracefully
(returns blob_validation_failed=True) rather than propagating as an unhandled
exception.

Also covers the raw_blob=None guard (F1 CRITICAL fix) and the asymmetric error
handling between cache-hit and fresh-fetch paths.

All tests use synthetic source names -- no real source names permitted.
"""

from __future__ import annotations

import json

import fakeredis.aioredis
import pytest

from lol_pipeline.raw_store import RawStore
from lol_pipeline.sources.base import (
    MATCH,
    DataType,
    ExtractionError,
    Extractor,
    FetchContext,
    FetchResponse,
    FetchResult,
    Source,
)
from lol_pipeline.sources.blob_store import BlobStore
from lol_pipeline.sources.coordinator import WaterfallCoordinator
from lol_pipeline.sources.registry import SourceEntry, SourceRegistry

# ---------------------------------------------------------------------------
# Mock factories (local to this module, following existing patterns)
# ---------------------------------------------------------------------------


def _make_source(
    name: str,
    data_types: frozenset[DataType] = frozenset({MATCH}),
    fetch_result: FetchResult = FetchResult.SUCCESS,
    raw_blob: bytes | None = b'{"mock": true}',
) -> Source:
    """Create a mock Source that tracks whether fetch() was called."""

    class _MockSource:
        _fetch_called: bool = False

        @property
        def name(self) -> str:
            return name

        @property
        def supported_data_types(self) -> frozenset[DataType]:
            return data_types

        @property
        def required_context_keys(self) -> frozenset[str]:
            return frozenset()

        async def fetch(self, context: FetchContext, data_type: DataType) -> FetchResponse:
            self._fetch_called = True
            return FetchResponse(
                result=fetch_result,
                raw_blob=raw_blob if fetch_result == FetchResult.SUCCESS else None,
            )

        async def close(self) -> None:
            pass

    return _MockSource()


def _make_extractor(
    source_name: str,
    data_types: frozenset[DataType] = frozenset({MATCH}),
    can_extract_result: bool = True,
    raises_on_extract: bool = False,
    extract_result: dict | None = None,
) -> Extractor:
    """Create a mock Extractor, optionally raising ExtractionError on extract()."""

    class _MockExtractor:
        @property
        def source_name(self) -> str:
            return source_name

        @property
        def data_types(self) -> frozenset[DataType]:
            return data_types

        def can_extract(self, blob: dict[str, str]) -> bool:
            return can_extract_result

        def extract(self, blob: dict[str, str], match_id: str, region: str) -> dict[str, str]:
            if raises_on_extract:
                raise ExtractionError("mock extraction failure")
            if extract_result is not None:
                return extract_result
            return {"extracted": "true", "match_id": match_id, "region": region}

    return _MockExtractor()


def _build_coordinator(
    sources: list[tuple[str, Source, int, frozenset[DataType]]],
    extractors: list[Extractor],
    blob_store: BlobStore | None,
    raw_store: RawStore,
) -> WaterfallCoordinator:
    """Build a WaterfallCoordinator from simplified source tuples."""
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
    return FetchContext(match_id="NA1_12345", puuid="abc123", region="na1")


# ===========================================================================
# Tests
# ===========================================================================


class TestHandleSuccessExtractionError:
    """F5/F14: ExtractionError in extract() during fresh-fetch returns
    blob_validation_failed=True, not an unhandled exception."""

    async def test_handle_success__extract_raises_extraction_error(
        self, redis, ctx, tmp_path
    ) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        # Source returns SUCCESS with valid JSON blob.
        alpha_src = _make_source("alpha", raw_blob=b'{"valid": "json"}')
        # Extractor passes can_extract() but raises ExtractionError on extract().
        alpha_ext = _make_extractor(
            "alpha",
            can_extract_result=True,
            raises_on_extract=True,
        )

        coord = _build_coordinator(
            sources=[("alpha", alpha_src, 0, frozenset())],
            extractors=[alpha_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        # Must NOT raise -- ExtractionError should be caught internally.
        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "all_exhausted"
        assert result.blob_validation_failed is True


class TestHandleSuccessRawBlobNone:
    """F1 CRITICAL: raw_blob=None on SUCCESS treated as validation failure,
    not an unhandled exception (TypeError on len(None))."""

    async def test_handle_success__raw_blob_none_treated_as_validation_failed(
        self, redis, ctx, tmp_path
    ) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        # Source returns SUCCESS but raw_blob is None.
        alpha_src = _make_source("alpha", raw_blob=None)
        alpha_ext = _make_extractor("alpha")

        coord = _build_coordinator(
            sources=[("alpha", alpha_src, 0, frozenset())],
            extractors=[alpha_ext],
            blob_store=blob_store,
            raw_store=raw_store,
        )

        result = await coord.fetch_match(ctx, MATCH)

        assert result.status == "all_exhausted"
        assert result.blob_validation_failed is True


class TestBlobCacheExtractionErrorFallsThrough:
    """Asymmetric error handling: ExtractionError from cached blob skips cache
    and falls through to the network source fetch."""

    async def test_blob_cache__extraction_error__falls_through_to_source(
        self, redis, ctx, tmp_path
    ) -> None:
        raw_store = RawStore(redis)
        blob_store = BlobStore(data_dir=str(tmp_path))

        # Pre-write a blob to the blob_store for source "alpha".
        cached_blob = {"cached": "blob", "can_extract": True}
        await blob_store.write("alpha", "NA1_12345", json.dumps(cached_blob).encode())

        # alpha's extractor passes can_extract() but raises on extract().
        alpha_ext = _make_extractor(
            "alpha",
            can_extract_result=True,
            raises_on_extract=True,
        )

        # alpha source (network) -- its fetch returns a GOOD blob this time.
        alpha_src = _make_source("alpha", raw_blob=b'{"fresh": "fetch"}')
        # Use a second extractor for the network path that succeeds.
        # Since both cache and network use the same extractor instance,
        # we need a source that works. We use beta as the fallback that
        # actually succeeds extraction.
        beta_src = _make_source("beta", raw_blob=b'{"beta": "data"}')
        beta_ext = _make_extractor(
            "beta",
            can_extract_result=True,
            raises_on_extract=False,
            extract_result={"extracted": "from_beta"},
        )

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

        # The cache hit for alpha should have raised ExtractionError and been
        # skipped (returns None from _try_blob_cache). Then _try_sources runs.
        # alpha's network fetch also raises ExtractionError, so beta succeeds.
        assert result.status == "success"
        assert result.source == "beta"
        assert result.data == {"extracted": "from_beta"}
        # Confirm alpha's source was indeed called (fell through from cache).
        assert alpha_src._fetch_called is True  # type: ignore[attr-defined]
