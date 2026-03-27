"""Unit tests for lol_pipeline.sources.blob_store — BlobStore isolation tests.

Phase 6 production readiness: verify BlobStore disk operations in isolation
(write, read, exists, find_any, path validation).

All tests use pytest tmp_path for filesystem isolation.
"""

from __future__ import annotations

import json

import pytest
import zstandard

from lol_pipeline.sources.blob_store import BlobStore

# ---------------------------------------------------------------------------
# Core write / read / exists
# ---------------------------------------------------------------------------


class TestWrite:
    """Write creates the expected file on disk."""

    async def test_write__creates_file(self, tmp_path) -> None:
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("riot", "NA1_12345", b'{"hello": "world"}')

        expected = tmp_path / "riot" / "NA1" / "NA1_12345.json.zst"
        assert expected.exists()
        decompressed = zstandard.ZstdDecompressor().decompress(expected.read_bytes())
        assert decompressed == b'{"hello": "world"}'

    async def test_write__is_idempotent(self, tmp_path) -> None:
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("riot", "NA1_99999", b'{"first": "write"}')
        # Second write with different data should be a no-op (write-once).
        await store.write("riot", "NA1_99999", b'{"second": "write"}')

        expected = tmp_path / "riot" / "NA1" / "NA1_99999.json.zst"
        assert expected.exists()
        # Content should be from the first write, not overwritten.
        decompressed = zstandard.ZstdDecompressor().decompress(expected.read_bytes())
        assert json.loads(decompressed) == {"first": "write"}

        # Verify only one file exists in the directory.
        files = list((tmp_path / "riot" / "NA1").glob("NA1_99999*.json.zst"))
        # Filter out any .tmp files that might linger.
        final_files = [f for f in files if not f.name.startswith(".tmp_")]
        assert len(final_files) == 1


class TestReadRoundtrip:
    """Write a dict as JSON, read it back, verify equality."""

    async def test_read__roundtrip(self, tmp_path) -> None:
        store = BlobStore(data_dir=str(tmp_path))
        original = {"gameId": 12345, "participants": [{"puuid": "abc"}]}
        await store.write("riot", "KR_67890", json.dumps(original).encode())

        result = await store.read("riot", "KR_67890")
        assert result == original


class TestExists:
    """exists() returns correct boolean for present/absent blobs."""

    async def test_exists__true_after_write(self, tmp_path) -> None:
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("opgg", "NA1_11111", b'{"data": true}')

        assert await store.exists("opgg", "NA1_11111") is True

    async def test_exists__false_for_missing(self, tmp_path) -> None:
        store = BlobStore(data_dir=str(tmp_path))

        assert await store.exists("riot", "NA1_00000") is False


# ---------------------------------------------------------------------------
# find_any
# ---------------------------------------------------------------------------


class TestFindAny:
    """find_any searches sources in priority order."""

    async def test_find_any__returns_highest_priority_source(self, tmp_path) -> None:
        store = BlobStore(data_dir=str(tmp_path))
        higher_blob = {"source": "higher", "quality": "best"}
        lower_blob = {"source": "lower", "quality": "okay"}

        await store.write("alpha", "NA1_55555", json.dumps(higher_blob).encode())
        await store.write("beta", "NA1_55555", json.dumps(lower_blob).encode())

        # alpha is listed first -> higher priority.
        result = await store.find_any("NA1_55555", source_names=["alpha", "beta"])

        assert result is not None
        source_name, blob_dict = result
        assert source_name == "alpha"
        assert blob_dict == higher_blob

    async def test_find_any__returns_none_for_cache_miss(self, tmp_path) -> None:
        store = BlobStore(data_dir=str(tmp_path))

        result = await store.find_any("NA1_99999", source_names=["riot", "opgg"])

        assert result is None

    async def test_find_any__skips_corrupt_json(self, tmp_path) -> None:
        store = BlobStore(data_dir=str(tmp_path))

        # Manually write a corrupt JSON file at the expected blob path.
        corrupt_dir = tmp_path / "riot" / "NA1"
        corrupt_dir.mkdir(parents=True)
        corrupt_file = corrupt_dir / "NA1_77777.json"
        corrupt_file.write_text("this is not valid json {{{")

        result = await store.find_any("NA1_77777", source_names=["riot"])

        # Corrupt JSON treated as cache miss -- no exception raised.
        assert result is None


# ---------------------------------------------------------------------------
# Path validation / security
# ---------------------------------------------------------------------------


class TestPathValidation:
    """Path traversal and platform validation."""

    async def test_write__prevents_path_traversal(self, tmp_path) -> None:
        store = BlobStore(data_dir=str(tmp_path))

        # match_id with ../ should trigger ValueError from is_relative_to backstop
        # or from _validate_platform (since "../" does not match ^[A-Z0-9]+$).
        with pytest.raises(ValueError):
            await store.write("riot", "../etc_passwd", b'{"evil": true}')

    async def test_platform_validation__lowercase_raises(self, tmp_path) -> None:
        store = BlobStore(data_dir=str(tmp_path))

        # Lowercase platform prefix (e.g., "na1_123") should fail
        # _validate_platform regex ^[A-Z0-9]+$.
        with pytest.raises(ValueError):
            await store.write("riot", "na1_123", b'{"bad": true}')


# ---------------------------------------------------------------------------
# Zstd compression
# ---------------------------------------------------------------------------


class TestBlobCompression:
    """BlobStore writes zstd-compressed files and reads them transparently."""

    async def test_write_produces_zst_file(self, tmp_path) -> None:
        """write() creates a .json.zst file on disk."""
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("riot", "NA1_10001", b'{"compressed": true}')

        zst_path = tmp_path / "riot" / "NA1" / "NA1_10001.json.zst"
        assert zst_path.exists()
        # Plain .json must NOT exist.
        assert not (tmp_path / "riot" / "NA1" / "NA1_10001.json").exists()

    async def test_read_decompresses_correctly(self, tmp_path) -> None:
        """write() then read() returns the original data."""
        store = BlobStore(data_dir=str(tmp_path))
        original = {"gameId": 42, "participants": ["a", "b"]}
        await store.write("riot", "KR_20002", json.dumps(original).encode())

        result = await store.read("riot", "KR_20002")
        assert result == original

    async def test_read_falls_back_to_plain_json(self, tmp_path) -> None:
        """read() can still load legacy uncompressed .json files."""
        store = BlobStore(data_dir=str(tmp_path))
        legacy_dir = tmp_path / "riot" / "NA1"
        legacy_dir.mkdir(parents=True)
        legacy_file = legacy_dir / "NA1_30003.json"
        legacy_file.write_bytes(b'{"legacy": true}')

        result = await store.read("riot", "NA1_30003")
        assert result == {"legacy": True}
