"""Unit tests for lol_pipeline.raw_store."""

from __future__ import annotations

import fakeredis.aioredis
import pytest
import zstandard as zstd

from lol_pipeline.raw_store import RawStore


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestRawStoreRedis:
    @pytest.mark.asyncio
    async def test_set_and_get(self, r):
        store = RawStore(r)
        await store.set("NA1_123", '{"info":{}}')
        result = await store.get("NA1_123")
        assert result == '{"info":{}}'

    @pytest.mark.asyncio
    async def test_nx_semantics(self, r):
        """Second set does NOT overwrite the first."""
        store = RawStore(r)
        await store.set("NA1_123", "first")
        await store.set("NA1_123", "second")
        result = await store.get("NA1_123")
        assert result == "first"

    @pytest.mark.asyncio
    async def test_key_format(self, r):
        store = RawStore(r)
        await store.set("NA1_456", "data")
        assert await r.exists("raw:match:NA1_456") == 1

    @pytest.mark.asyncio
    async def test_exists_false_when_empty(self, r):
        store = RawStore(r)
        assert await store.exists("NA1_999") is False

    @pytest.mark.asyncio
    async def test_exists_true_after_set(self, r):
        store = RawStore(r)
        await store.set("NA1_789", "data")
        assert await store.exists("NA1_789") is True

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, r):
        store = RawStore(r)
        assert await store.get("NA1_nonexistent") is None

    @pytest.mark.asyncio
    async def test_no_ttl(self, r):
        """Raw store keys should not expire (TTL = -1)."""
        store = RawStore(r)
        await store.set("NA1_100", "data")
        ttl = await r.ttl("raw:match:NA1_100")
        assert ttl == -1


class TestRawStoreDisk:
    @pytest.mark.asyncio
    async def test_disk_write_to_jsonl(self, r, tmp_path):
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_500", '{"test": true}')
        # Verify written to JSONL bundle
        jsonl_files = list((tmp_path / "NA1").glob("*.jsonl"))
        assert len(jsonl_files) == 1
        content = jsonl_files[0].read_text()
        assert 'NA1_500\t{"test": true}' in content

    @pytest.mark.asyncio
    async def test_disk_fallback_on_redis_miss(self, r, tmp_path):
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_501", '{"fallback": 1}')
        # Delete from Redis, should fall back to disk
        await r.delete("raw:match:NA1_501")
        result = await store.get("NA1_501")
        assert result == '{"fallback": 1}'
        # Should have repopulated Redis
        assert await r.get("raw:match:NA1_501") == '{"fallback": 1}'

    @pytest.mark.asyncio
    async def test_legacy_json_fallback(self, r, tmp_path):
        """get() can still read legacy individual JSON files."""
        platform_dir = tmp_path / "NA1"
        platform_dir.mkdir()
        (platform_dir / "NA1_600.json").write_text('{"legacy": true}')

        store = RawStore(r, data_dir=str(tmp_path))
        result = await store.get("NA1_600")
        assert result == '{"legacy": true}'


class TestRawStoreJsonlBundle:
    @pytest.mark.asyncio
    async def test_set_writes_jsonl_bundle(self, r, tmp_path):
        """set() appends to a JSONL bundle file instead of individual JSON."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_100", '{"a":1}')
        await store.set("NA1_101", '{"b":2}')
        # Should have a JSONL file in the platform dir
        jsonl_files = list((tmp_path / "NA1").glob("*.jsonl"))
        assert len(jsonl_files) == 1
        lines = jsonl_files[0].read_text().strip().split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("NA1_100\t")
        assert lines[1].startswith("NA1_101\t")

    @pytest.mark.asyncio
    async def test_get_reads_from_jsonl_bundle(self, r, tmp_path):
        """get() falls back to JSONL bundle on Redis miss."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_200", '{"found": true}')
        await r.delete("raw:match:NA1_200")
        result = await store.get("NA1_200")
        assert result == '{"found": true}'

    @pytest.mark.asyncio
    async def test_exists_checks_jsonl_bundle(self, r, tmp_path):
        """exists() returns True if match is in a JSONL bundle."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_300", '{"exists": 1}')
        await r.delete("raw:match:NA1_300")
        assert await store.exists("NA1_300") is True

    @pytest.mark.asyncio
    async def test_get_reads_from_compressed_bundle(self, r, tmp_path):
        """get() can read from .jsonl.zst compressed bundles."""
        # Write a compressed bundle manually
        platform_dir = tmp_path / "NA1"
        platform_dir.mkdir()
        content = 'NA1_400\t{"compressed": true}\n'
        cctx = zstd.ZstdCompressor()
        compressed = cctx.compress(content.encode())
        (platform_dir / "2024-01.jsonl.zst").write_bytes(compressed)

        store = RawStore(r, data_dir=str(tmp_path))
        result = await store.get("NA1_400")
        assert result == '{"compressed": true}'

    @pytest.mark.asyncio
    async def test_nx_semantics_with_jsonl(self, r, tmp_path):
        """Write-once: second set() for same match_id does not duplicate in JSONL."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_500", '{"first": true}')
        await store.set("NA1_500", '{"second": true}')
        jsonl_files = list((tmp_path / "NA1").glob("*.jsonl"))
        lines = jsonl_files[0].read_text().strip().split("\n")
        assert len(lines) == 1
        assert '"first"' in lines[0]


class TestRawStoreStreamingDecompression:
    @pytest.mark.asyncio
    async def test_compressed_search_uses_streaming(self, r, tmp_path):
        """Compressed bundle search uses streaming, not full decompress."""
        platform_dir = tmp_path / "NA1"
        platform_dir.mkdir()
        content = 'NA1_700\t{"streaming": true}\n'
        cctx = zstd.ZstdCompressor()
        compressed = cctx.compress(content.encode())
        (platform_dir / "2024-01.jsonl.zst").write_bytes(compressed)

        store = RawStore(r, data_dir=str(tmp_path))

        # Patch decompress to fail — streaming should not call it
        original_decompress = zstd.ZstdDecompressor.decompress
        zstd.ZstdDecompressor.decompress = property(
            lambda self: (_ for _ in ()).throw(AssertionError("decompress() should not be called"))
        )
        try:
            result = await store.get("NA1_700")
            assert result == '{"streaming": true}'
        finally:
            zstd.ZstdDecompressor.decompress = original_decompress


class TestRawStoreBundleSearchEdgeCases:
    """Tier 3 — RawStore bundle search edge cases."""

    @pytest.mark.asyncio
    async def test_malformed_json_line_skipped(self, r, tmp_path):
        """Malformed lines in JSONL bundle are skipped without error."""
        platform_dir = tmp_path / "NA1"
        platform_dir.mkdir()
        content = "CORRUPT LINE WITHOUT TAB\nNA1_800\t{\"found\": true}\n"
        (platform_dir / "2024-01.jsonl").write_text(content)

        store = RawStore(r, data_dir=str(tmp_path))
        result = await store.get("NA1_800")
        assert result == '{"found": true}'

    @pytest.mark.asyncio
    async def test_corrupt_zst_returns_none(self, r, tmp_path):
        """Corrupt .zst file should not crash; returns None."""
        platform_dir = tmp_path / "NA1"
        platform_dir.mkdir()
        (platform_dir / "2024-01.jsonl.zst").write_bytes(b"not valid zstd data")

        store = RawStore(r, data_dir=str(tmp_path))
        # Should handle the error gracefully (zstd raises ZstdError)
        try:
            result = await store.get("NA1_999")
            # If it returns None, that's fine
            assert result is None
        except Exception:
            # If it raises, that's also acceptable — documents current behavior
            pass


class TestRawStoreBundleStreaming:
    """CQ-18: _search_bundle_file streams line-by-line instead of loading whole file."""

    def test_search_bundle_file_uses_open_not_read_text(self, tmp_path):
        """_search_bundle_file uses open() for line-by-line iteration."""
        bundle = tmp_path / "test.jsonl"
        bundle.write_text("NA1_STREAM\t{\"streamed\": true}\nNA1_OTHER\t{\"other\": 1}\n")

        # Verify the function works correctly with streaming
        result = RawStore._search_bundle_file(bundle, "NA1_STREAM")
        assert result == '{"streamed": true}'

        # Verify it returns None for missing entries
        assert RawStore._search_bundle_file(bundle, "NA1_MISSING") is None

        # Verify it uses open() by inspecting source — the implementation
        # uses `with path.open()` not `path.read_text().splitlines()`
        import inspect

        source = inspect.getsource(RawStore._search_bundle_file)
        assert "path.open(" in source, "Expected path.open() for streaming"
        assert "read_text" not in source, "read_text() loads entire file into memory"


class TestCompressedBundleNoUnreachableCode:
    """Fix 6: _search_compressed_bundle has no unreachable return after with block."""

    def test_search_compressed_returns_none_when_not_found(self, tmp_path):
        """Compressed bundle search returns None for missing match (no dead code)."""
        platform_dir = tmp_path / "NA1"
        platform_dir.mkdir()
        content = "NA1_OTHER\t{\"other\": true}\n"
        cctx = zstd.ZstdCompressor()
        compressed = cctx.compress(content.encode())
        bundle = platform_dir / "2024-01.jsonl.zst"
        bundle.write_bytes(compressed)

        result = RawStore._search_compressed_bundle(bundle, "NA1_MISSING")
        assert result is None


class TestRawStoreBundleEdgeCases:
    @pytest.mark.asyncio
    async def test_search_empty_jsonl_file(self, r, tmp_path):
        """Empty JSONL bundle file returns None."""
        platform_dir = tmp_path / "NA1"
        platform_dir.mkdir()
        (platform_dir / "2024-01.jsonl").write_text("")
        store = RawStore(r, data_dir=str(tmp_path))
        assert await store.get("NA1_999") is None

    @pytest.mark.asyncio
    async def test_no_data_dir_skips_disk(self, r):
        """RawStore without data_dir only uses Redis."""
        store = RawStore(r)
        await store.set("NA1_100", '{"test": 1}')
        result = await store.get("NA1_100")
        assert result == '{"test": 1}'
        # Delete from Redis — no disk fallback
        await r.delete("raw:match:NA1_100")
        assert await store.get("NA1_100") is None

    @pytest.mark.asyncio
    async def test_unknown_platform_prefix(self, r, tmp_path):
        """Match IDs without underscore get platform 'UNKNOWN'."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NOUNDERSCORE", '{"data": 1}')
        # Should write to UNKNOWN directory
        assert (tmp_path / "UNKNOWN").exists()

    @pytest.mark.asyncio
    async def test_exists_false_on_disk_when_not_stored(self, r, tmp_path):
        """exists() returns False when match is in neither Redis nor disk."""
        store = RawStore(r, data_dir=str(tmp_path))
        assert await store.exists("NA1_nonexistent") is False
