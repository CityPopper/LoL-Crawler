"""Unit tests for lol_pipeline.sources foundation subpackage.

Covers:
- base.py: DataType, FetchContext, FetchResult, FetchResponse, WaterfallResult,
  ExtractionError, Source/Extractor protocols
- registry.py: SourceEntry validation, SourceRegistry (ordering, filtering,
  startup cross-check)
- blob_store.py: BlobStore disk operations (exists, read, write, find_any,
  atomic writes, disabled state, corrupt blobs, platform validation)
"""

from __future__ import annotations

import json

import pytest

from lol_pipeline.sources.base import (
    ExtractionError,
    FetchContext,
    FetchResponse,
    FetchResult,
    WaterfallResult,
)
from lol_pipeline.sources.blob_store import BlobStore
from lol_pipeline.sources.registry import SourceEntry, SourceRegistry


# ---------------------------------------------------------------------------
# Helper: mock source factory (no real source names)
# ---------------------------------------------------------------------------


def make_mock_source(name: str, data_types: frozenset[str]) -> object:
    """Create a minimal mock source for registry tests.

    Uses only synthetic names ("alpha", "beta", "gamma") -- never real
    source names like "riot" or "opgg".
    """

    class MockSource:
        @property
        def name(self) -> str:
            return name

        @property
        def supported_data_types(self) -> frozenset[str]:
            return data_types

        @property
        def required_context_keys(self) -> frozenset[str]:
            return frozenset()

        async def fetch(self, ctx, dt):  # noqa: ANN001, ANN201
            ...

        async def close(self) -> None:
            ...

    return MockSource()


def make_mock_extractor(source_name: str, data_types: frozenset[str]) -> object:
    """Create a minimal mock extractor for cross-check tests."""

    class MockExtractor:
        @property
        def source_name(self) -> str:
            return source_name

        @property
        def data_types(self) -> frozenset[str]:
            return data_types

        def can_extract(self, blob: dict) -> bool:
            return True

        def extract(self, blob: dict, match_id: str, region: str) -> dict:
            return blob

    return MockExtractor()


# ===========================================================================
# 1. SourceEntry Tests
# ===========================================================================


class TestSourceEntry:
    def test_valid_name__constructs(self) -> None:
        """SourceEntry with valid lowercase name 'riot' constructs without error."""
        src = make_mock_source("riot", frozenset({"match"}))
        entry = SourceEntry(name="riot", source=src, priority=0)  # type: ignore[arg-type]
        assert entry.name == "riot"

    @pytest.mark.parametrize(
        "bad_name",
        [
            "Riot",       # uppercase letter
            "riot-api",   # hyphen
            "riot/api",   # slash
            "../etc",     # path traversal
        ],
    )
    def test_invalid_name__raises_value_error(self, bad_name: str) -> None:
        """SourceEntry rejects names that don't match ^[a-z0-9_]+$."""
        # The source's own name matches the entry name, but the name is invalid.
        src = make_mock_source(bad_name, frozenset({"match"}))
        with pytest.raises(ValueError, match="must match"):
            SourceEntry(name=bad_name, source=src, priority=0)  # type: ignore[arg-type]

    def test_name_mismatch__raises_value_error(self) -> None:
        """SourceEntry raises ValueError when name != source.name."""
        src = make_mock_source("alpha", frozenset({"match"}))
        with pytest.raises(ValueError, match="must match source.name"):
            SourceEntry(name="beta", source=src, priority=0)  # type: ignore[arg-type]


# ===========================================================================
# 2. SourceRegistry Tests
# ===========================================================================


class TestSourceRegistry:
    @pytest.fixture()
    def three_sources(self) -> list[SourceEntry]:
        """Alpha (priority 0, match+build), beta (priority 1, match), gamma (priority 2, build)."""
        alpha = make_mock_source("alpha", frozenset({"match", "build"}))
        beta = make_mock_source("beta", frozenset({"match"}))
        gamma = make_mock_source("gamma", frozenset({"build"}))
        return [
            SourceEntry(name="alpha", source=alpha, priority=0),  # type: ignore[arg-type]
            SourceEntry(name="beta", source=beta, priority=1),  # type: ignore[arg-type]
            SourceEntry(name="gamma", source=gamma, priority=2),  # type: ignore[arg-type]
        ]

    def test_sources_for__match__returns_matching_in_priority_order(
        self, three_sources: list[SourceEntry]
    ) -> None:
        """sources_for('match') returns alpha then beta (both support match)."""
        reg = SourceRegistry(three_sources)
        result = reg.sources_for("match")
        assert [e.name for e in result] == ["alpha", "beta"]

    def test_sources_for__unknown__returns_empty(
        self, three_sources: list[SourceEntry]
    ) -> None:
        """sources_for('unknown') returns an empty list."""
        reg = SourceRegistry(three_sources)
        assert reg.sources_for("unknown") == []

    def test_source_names__returns_priority_order(
        self, three_sources: list[SourceEntry]
    ) -> None:
        """source_names property returns names ordered by priority."""
        reg = SourceRegistry(three_sources)
        assert reg.source_names == ["alpha", "beta", "gamma"]

    def test_get__existing__returns_entry(
        self, three_sources: list[SourceEntry]
    ) -> None:
        """get('alpha') returns the matching SourceEntry."""
        reg = SourceRegistry(three_sources)
        entry = reg.get("alpha")
        assert entry is not None
        assert entry.name == "alpha"

    def test_get__nonexistent__returns_none(
        self, three_sources: list[SourceEntry]
    ) -> None:
        """get('nonexistent') returns None."""
        reg = SourceRegistry(three_sources)
        assert reg.get("nonexistent") is None


class TestSourceRegistryCrossCheck:
    def test_missing_extractor__raises_value_error(self) -> None:
        """Startup cross-check: missing (source_name, data_type) pair raises ValueError."""
        alpha = make_mock_source("alpha", frozenset({"match"}))
        entries = [
            SourceEntry(name="alpha", source=alpha, priority=0),  # type: ignore[arg-type]
        ]
        # extractor_index is empty -- no extractor for ("alpha", "match")
        with pytest.raises(ValueError, match="no extractor is registered"):
            SourceRegistry(entries, extractor_index={})

    def test_correct_extractor_index__no_error(self) -> None:
        """Startup cross-check passes when all (source, data_type) pairs are covered."""
        alpha = make_mock_source("alpha", frozenset({"match"}))
        entries = [
            SourceEntry(name="alpha", source=alpha, priority=0),  # type: ignore[arg-type]
        ]
        ext = make_mock_extractor("alpha", frozenset({"match"}))
        # Should not raise.
        reg = SourceRegistry(entries, extractor_index={("alpha", "match"): ext})  # type: ignore[dict-item]
        assert reg.get("alpha") is not None

    def test_cross_check_is_per_source__not_any_source(self) -> None:
        """Extractor for ('beta', 'match') does NOT satisfy ('alpha', 'match')."""
        alpha = make_mock_source("alpha", frozenset({"match"}))
        beta = make_mock_source("beta", frozenset({"match"}))
        entries = [
            SourceEntry(name="alpha", source=alpha, priority=0),  # type: ignore[arg-type]
            SourceEntry(name="beta", source=beta, priority=1),  # type: ignore[arg-type]
        ]
        ext_beta = make_mock_extractor("beta", frozenset({"match"}))
        # Only beta has an extractor -- alpha is missing.
        with pytest.raises(ValueError, match="alpha"):
            SourceRegistry(
                entries,
                extractor_index={("beta", "match"): ext_beta},  # type: ignore[dict-item]
            )

    def test_no_extractor_index__skips_cross_check(self) -> None:
        """When extractor_index is None, no cross-check is performed."""
        alpha = make_mock_source("alpha", frozenset({"match"}))
        entries = [
            SourceEntry(name="alpha", source=alpha, priority=0),  # type: ignore[arg-type]
        ]
        # Should not raise even though no extractors exist.
        reg = SourceRegistry(entries, extractor_index=None)
        assert reg.source_names == ["alpha"]


# ===========================================================================
# 3. BlobStore Tests
# ===========================================================================


class TestBlobStoreExists:
    async def test_exists__nonexistent__returns_false(self, tmp_path: object) -> None:
        """exists() returns False for a blob that was never written."""
        store = BlobStore(data_dir=str(tmp_path))
        assert await store.exists("alpha", "NA1_12345") is False

    async def test_exists__after_write__returns_true(self, tmp_path: object) -> None:
        """exists() returns True after a blob is written."""
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("alpha", "NA1_12345", b'{"test": true}')
        assert await store.exists("alpha", "NA1_12345") is True


class TestBlobStoreWriteOnce:
    async def test_write__second_write__is_noop(self, tmp_path: object) -> None:
        """write() is write-once: second write to same path is a no-op."""
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("alpha", "NA1_12345", b'{"first": true}')
        await store.write("alpha", "NA1_12345", b'{"second": true}')
        result = await store.read("alpha", "NA1_12345")
        assert result == {"first": True}


class TestBlobStoreRead:
    async def test_read__nonexistent__returns_none(self, tmp_path: object) -> None:
        """read() returns None for a blob that does not exist."""
        store = BlobStore(data_dir=str(tmp_path))
        assert await store.read("alpha", "NA1_12345") is None

    async def test_read__returns_written_dict(self, tmp_path: object) -> None:
        """read() returns the dict that was written."""
        store = BlobStore(data_dir=str(tmp_path))
        original = {"gameDuration": 1800, "participants": []}
        await store.write("alpha", "NA1_12345", json.dumps(original).encode())
        result = await store.read("alpha", "NA1_12345")
        assert result == original


class TestBlobStoreWriteInputTypes:
    async def test_write__bytes_input(self, tmp_path: object) -> None:
        """write() with bytes input stores valid JSON."""
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("alpha", "NA1_100", b'{"bytes": true}')
        result = await store.read("alpha", "NA1_100")
        assert result == {"bytes": True}

    async def test_write__str_input(self, tmp_path: object) -> None:
        """write() with str input encodes to UTF-8 and stores valid JSON."""
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("alpha", "NA1_101", '{"str": true}')
        result = await store.read("alpha", "NA1_101")
        assert result == {"str": True}


class TestBlobStoreFindAny:
    async def test_find_any__no_blobs__returns_none(self, tmp_path: object) -> None:
        """find_any() returns None when no blobs exist."""
        store = BlobStore(data_dir=str(tmp_path))
        assert await store.find_any("NA1_12345", ["alpha", "beta"]) is None

    async def test_find_any__blob_exists__returns_tuple(self, tmp_path: object) -> None:
        """find_any() returns (source_name, dict) when a blob exists."""
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("beta", "NA1_12345", b'{"found": true}')
        result = await store.find_any("NA1_12345", ["alpha", "beta"])
        assert result is not None
        source_name, data = result
        assert source_name == "beta"
        assert data == {"found": True}

    async def test_find_any__respects_priority_order(self, tmp_path: object) -> None:
        """find_any() returns the first source in the priority list when both have blobs."""
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("alpha", "NA1_12345", b'{"from": "alpha"}')
        await store.write("beta", "NA1_12345", b'{"from": "beta"}')
        result = await store.find_any("NA1_12345", ["alpha", "beta"])
        assert result is not None
        source_name, data = result
        assert source_name == "alpha"
        assert data == {"from": "alpha"}

    async def test_find_any__corrupt_blob__returns_none(self, tmp_path: object) -> None:
        """find_any() treats corrupt JSON as a cache miss (returns None, logs warning)."""
        from pathlib import Path

        # Manually create a corrupt blob file.
        blob_dir = Path(str(tmp_path)) / "alpha" / "NA1"
        blob_dir.mkdir(parents=True)
        (blob_dir / "NA1_12345.json").write_text("NOT VALID JSON {{{")
        store = BlobStore(data_dir=str(tmp_path))
        result = await store.find_any("NA1_12345", ["alpha"])
        assert result is None

    async def test_find_any__disabled_store__returns_none(self) -> None:
        """BlobStore(data_dir='') returns None from find_any() (disabled state)."""
        store = BlobStore(data_dir="")
        assert await store.find_any("NA1_12345", ["alpha"]) is None

    async def test_find_any__nonexistent_data_dir__returns_none(
        self, tmp_path: object
    ) -> None:
        """find_any() returns None when data_dir does not exist on disk."""
        from pathlib import Path

        nonexistent = Path(str(tmp_path)) / "does_not_exist"
        store = BlobStore(data_dir=str(nonexistent))
        assert await store.find_any("NA1_12345", ["alpha"]) is None


class TestBlobStorePlatformValidation:
    async def test_blob_path__lowercase_platform__raises(self, tmp_path: object) -> None:
        """_blob_path() raises ValueError for lowercase platform (e.g. 'na1_12345')."""
        store = BlobStore(data_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid platform segment"):
            store._blob_path("alpha", "na1_12345")

    async def test_match_id__extracts_correct_platform(self, tmp_path: object) -> None:
        """Match ID 'NA1_12345' correctly extracts platform 'NA1'."""
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("alpha", "NA1_12345", b'{"test": true}')
        from pathlib import Path

        expected = Path(str(tmp_path)) / "alpha" / "NA1" / "NA1_12345.json"
        assert expected.exists()


class TestBlobStoreDisabledState:
    async def test_disabled__exists__returns_false(self) -> None:
        """BlobStore(data_dir='') returns False from exists()."""
        store = BlobStore(data_dir="")
        assert await store.exists("alpha", "NA1_12345") is False

    async def test_disabled__read__returns_none(self) -> None:
        """BlobStore(data_dir='') returns None from read()."""
        store = BlobStore(data_dir="")
        assert await store.read("alpha", "NA1_12345") is None

    async def test_disabled__write__is_noop(self) -> None:
        """BlobStore(data_dir='') silently skips write()."""
        store = BlobStore(data_dir="")
        # Should not raise -- just returns.
        await store.write("alpha", "NA1_12345", b'{"test": true}')


class TestBlobStoreAtomicWrite:
    async def test_atomic_write__tmp_file_cleaned_up(self, tmp_path: object) -> None:
        """After successful write, no .tmp_ files remain."""
        from pathlib import Path

        store = BlobStore(data_dir=str(tmp_path))
        await store.write("alpha", "NA1_12345", b'{"test": true}')
        platform_dir = Path(str(tmp_path)) / "alpha" / "NA1"
        tmp_files = list(platform_dir.glob(".tmp_*"))
        assert tmp_files == [], f"leftover tmp files: {tmp_files}"
        # The final blob file should exist.
        assert (platform_dir / "NA1_12345.json").exists()


# ===========================================================================
# 4. Base Types Tests
# ===========================================================================


class TestFetchResult:
    def test_all_six_values(self) -> None:
        """FetchResult enum has exactly 6 members."""
        assert len(FetchResult) == 6
        expected = {"SUCCESS", "THROTTLED", "NOT_FOUND", "AUTH_ERROR", "SERVER_ERROR", "UNAVAILABLE"}
        assert {m.name for m in FetchResult} == expected


class TestFetchResponse:
    def test_minimal_construction(self) -> None:
        """FetchResponse can be constructed with only result."""
        resp = FetchResponse(result=FetchResult.SUCCESS)
        assert resp.result == FetchResult.SUCCESS
        assert resp.raw_blob is None
        assert resp.data is None
        assert resp.retry_after_ms is None
        assert resp.available_data_types == frozenset()


class TestWaterfallResult:
    def test_status_accepts_literal_values(self) -> None:
        """WaterfallResult status field accepts all Literal values."""
        for status in ("success", "not_found", "auth_error", "all_exhausted", "cached"):
            wr = WaterfallResult(status=status)  # type: ignore[arg-type]
            assert wr.status == status

    def test_default_field_values(self) -> None:
        """WaterfallResult defaults: data=None, source='', retry_after_ms=None, etc."""
        wr = WaterfallResult(status="success")
        assert wr.data is None
        assert wr.source == ""
        assert wr.retry_after_ms is None
        assert wr.available_data_types == frozenset()
        assert wr.blob_validation_failed is False


class TestFetchContext:
    def test_frozen__cannot_mutate(self) -> None:
        """FetchContext is frozen: attribute mutation raises FrozenInstanceError."""
        ctx = FetchContext(match_id="NA1_12345", puuid="abc", region="na1")
        with pytest.raises(AttributeError):
            ctx.match_id = "changed"  # type: ignore[misc]

    def test_extra_defaults_to_empty_dict(self) -> None:
        """FetchContext.extra defaults to an empty dict."""
        ctx = FetchContext(match_id="NA1_12345", puuid="abc", region="na1")
        assert ctx.extra == {}


class TestExtractionError:
    def test_is_exception_subclass(self) -> None:
        """ExtractionError is a subclass of Exception."""
        assert issubclass(ExtractionError, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        """ExtractionError can be raised with a message and caught."""
        with pytest.raises(ExtractionError, match="bad blob"):
            raise ExtractionError("bad blob")
