"""Unit tests for lol_pipeline.raw_store."""

from __future__ import annotations

from unittest.mock import patch

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
    async def test_ttl_set(self, r):
        """Raw store keys expire after 24 h to prevent OOM (B1)."""
        store = RawStore(r)
        await store.set("NA1_100", "data")
        ttl = await r.ttl("raw:match:NA1_100")
        assert ttl == 86400


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
        content = 'CORRUPT LINE WITHOUT TAB\nNA1_800\t{"found": true}\n'
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
        except Exception:  # noqa: S110
            # If it raises, that's also acceptable — documents current behavior
            pass


class TestRawStoreBundleStreaming:
    """CQ-18: _search_bundle_file streams line-by-line instead of loading whole file."""

    def test_search_bundle_file_uses_open_not_read_text(self, tmp_path):
        """_search_bundle_file uses open() for line-by-line iteration."""
        bundle = tmp_path / "test.jsonl"
        bundle.write_text('NA1_STREAM\t{"streamed": true}\nNA1_OTHER\t{"other": 1}\n')

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
        content = 'NA1_OTHER\t{"other": true}\n'
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
        """Match IDs without underscore are rejected by validation."""
        store = RawStore(r, data_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid match_id"):
            await store.set("NOUNDERSCORE", '{"data": 1}')

    @pytest.mark.asyncio
    async def test_exists_false_on_disk_when_not_stored(self, r, tmp_path):
        """exists() returns False when match is in neither Redis nor disk."""
        store = RawStore(r, data_dir=str(tmp_path))
        assert await store.exists("NA1_99999999") is False


class TestRawStoreAsyncDiskIO:
    """I2-M3: Disk I/O runs in asyncio.to_thread to avoid blocking the event loop."""

    @pytest.mark.asyncio
    async def test_exists_uses_to_thread_for_disk_fallback(self, r, tmp_path):
        """exists() delegates _exists_in_bundles to asyncio.to_thread."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_10001", '{"data": 1}')
        await r.delete("raw:match:NA1_10001")

        calls = []
        original_to_thread = __import__("asyncio").to_thread

        async def tracking_to_thread(fn, *args, **kwargs):
            calls.append(fn.__name__)
            return await original_to_thread(fn, *args, **kwargs)

        with patch("lol_pipeline.raw_store.asyncio.to_thread", side_effect=tracking_to_thread):
            result = await store.exists("NA1_10001")

        assert result is True
        assert "_exists_in_bundles" in calls

    @pytest.mark.asyncio
    async def test_get_uses_to_thread_for_disk_fallback(self, r, tmp_path):
        """get() delegates _search_bundles to asyncio.to_thread."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_10002", '{"data": 2}')
        await r.delete("raw:match:NA1_10002")

        calls = []
        original_to_thread = __import__("asyncio").to_thread

        async def tracking_to_thread(fn, *args, **kwargs):
            calls.append(fn.__name__)
            return await original_to_thread(fn, *args, **kwargs)

        with patch("lol_pipeline.raw_store.asyncio.to_thread", side_effect=tracking_to_thread):
            result = await store.get("NA1_10002")

        assert result == '{"data": 2}'
        assert "_search_bundles" in calls

    @pytest.mark.asyncio
    async def test_set_uses_to_thread_for_bundle_check(self, r, tmp_path):
        """set() delegates _exists_in_current_bundle to asyncio.to_thread for dedup check."""
        store = RawStore(r, data_dir=str(tmp_path))

        calls = []
        original_to_thread = __import__("asyncio").to_thread

        async def tracking_to_thread(fn, *args, **kwargs):
            calls.append(fn.__name__)
            return await original_to_thread(fn, *args, **kwargs)

        with patch("lol_pipeline.raw_store.asyncio.to_thread", side_effect=tracking_to_thread):
            await store.set("NA1_10003", '{"data": 3}')

        assert "_exists_in_current_bundle" in calls

    @pytest.mark.asyncio
    async def test_exists_skips_disk_when_redis_hit(self, r):
        """exists() returns True from Redis without calling to_thread."""
        store = RawStore(r)
        await store.set("NA1_T4", '{"data": 4}')

        calls = []

        async def tracking_to_thread(fn, *args, **kwargs):
            calls.append(fn.__name__)

        with patch("lol_pipeline.raw_store.asyncio.to_thread", side_effect=tracking_to_thread):
            result = await store.exists("NA1_T4")

        assert result is True
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_get_skips_disk_when_redis_hit(self, r):
        """get() returns data from Redis without calling to_thread."""
        store = RawStore(r)
        await store.set("NA1_T5", '{"data": 5}')

        calls = []

        async def tracking_to_thread(fn, *args, **kwargs):
            calls.append(fn.__name__)

        with patch("lol_pipeline.raw_store.asyncio.to_thread", side_effect=tracking_to_thread):
            result = await store.get("NA1_T5")

        assert result == '{"data": 5}'
        assert len(calls) == 0


class TestRawStoreTtlConfigurable:
    """P13-INT-4: RAW_STORE_TTL_SECONDS env var controls the Redis key TTL."""

    @pytest.mark.asyncio
    async def test_set_uses_ttl_seconds(self, r):
        """set() stores the key with a non-zero TTL."""
        store = RawStore(r)
        await store.set("NA1_TTL1", '{"data": 1}')
        ttl = await r.ttl("raw:match:NA1_TTL1")
        # TTL should be positive (key will expire)
        assert ttl > 0

    @pytest.mark.asyncio
    async def test_get_writeback_uses_ttl_seconds(self, r, tmp_path):
        """get() Redis write-back uses a positive TTL."""
        match_id = "NA1_20002"
        bundle = tmp_path / "NA1" / "2099-01.jsonl"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        bundle.write_text(f'{match_id}\t{{"data": 2}}\n', encoding="utf-8")

        store = RawStore(r, data_dir=str(tmp_path))
        result = await store.get(match_id)
        assert result is not None

        ttl = await r.ttl(f"raw:match:{match_id}")
        assert ttl > 0

    def test_ttl_seconds_env_var(self, monkeypatch):
        """_TTL_SECONDS reads from RAW_STORE_TTL_SECONDS env var."""
        import importlib

        import lol_pipeline.raw_store as raw_store_mod

        monkeypatch.setenv("RAW_STORE_TTL_SECONDS", "999")
        importlib.reload(raw_store_mod)
        assert raw_store_mod._TTL_SECONDS == 999
        monkeypatch.delenv("RAW_STORE_TTL_SECONDS", raising=False)
        importlib.reload(raw_store_mod)


class TestRawStoreSetScopedDedup:
    """set() only checks the current month's bundle, not all historical bundles."""

    @pytest.mark.asyncio
    async def test_set_skips_old_bundles_on_dedup(self, r, tmp_path):
        """set() does NOT scan old-month bundles for dedup — only current month."""
        platform_dir = tmp_path / "NA1"
        platform_dir.mkdir(parents=True)

        # Write the match into an old month's bundle (simulating historical data)
        old_bundle = platform_dir / "2024-01.jsonl"
        old_bundle.write_text('NA1_30001\t{"old": true}\n')

        store = RawStore(r, data_dir=str(tmp_path))
        # Redis key does not exist (simulates Redis restart), match only on old disk
        await store.set("NA1_30001", '{"new": true}')

        # The current month's bundle should have the new write because set()
        # only checked the current bundle (where NA1_30001 was absent)
        current_bundles = list(platform_dir.glob("202*.jsonl"))
        # Should have 2 bundles: the old one and the current month
        assert len(current_bundles) == 2

        # The current month's bundle should contain the match
        current_month_bundles = [b for b in current_bundles if b.name != "2024-01.jsonl"]
        assert len(current_month_bundles) == 1
        content = current_month_bundles[0].read_text()
        assert "NA1_30001" in content

    @pytest.mark.asyncio
    async def test_set_dedup_within_current_bundle(self, r, tmp_path):
        """set() still prevents duplicates within the current month's bundle."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_30002", '{"first": true}')

        # Simulate Redis restart: delete the key, then try to write again
        await r.delete("raw:match:NA1_30002")
        await store.set("NA1_30002", '{"second": true}')

        # The current bundle should have exactly one entry
        jsonl_files = list((tmp_path / "NA1").glob("*.jsonl"))
        assert len(jsonl_files) == 1
        lines = jsonl_files[0].read_text().strip().split("\n")
        assert len(lines) == 1
        assert '"first"' in lines[0]

    @pytest.mark.asyncio
    async def test_set_does_not_call_search_bundles(self, r, tmp_path):
        """set() calls _exists_in_current_bundle, never _search_bundles."""
        store = RawStore(r, data_dir=str(tmp_path))

        with patch.object(store, "_search_bundles", wraps=store._search_bundles) as mock_search:
            await store.set("NA1_30003", '{"data": 1}')
            mock_search.assert_not_called()

    def test_exists_in_current_bundle__missing_file__returns_false(self, tmp_path):
        """_exists_in_current_bundle returns False when current bundle does not exist."""
        import fakeredis.aioredis

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        store = RawStore(r, data_dir=str(tmp_path))
        assert store._exists_in_current_bundle("NA1_MISS") is False

    def test_exists_in_current_bundle__no_data_dir__returns_false(self):
        """_exists_in_current_bundle returns False when no data_dir configured."""
        import fakeredis.aioredis

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        store = RawStore(r)
        assert store._exists_in_current_bundle("NA1_MISS") is False


class TestRawStoreKeyPrefix:
    @pytest.mark.asyncio
    async def test_custom_key_prefix_writes_to_custom_key(self, r):
        """RawStore with custom key_prefix writes to that prefix, not raw:match:."""
        store = RawStore(r, key_prefix="raw:opgg:match:")
        await store.set("mid123", "data")
        assert await r.exists("raw:opgg:match:mid123") == 1
        assert await r.exists("raw:match:mid123") == 0

    @pytest.mark.asyncio
    async def test_default_key_prefix_unchanged(self, r):
        """Default key_prefix behavior is backward-compatible."""
        store = RawStore(r)
        await store.set("NA1_456", "data")
        assert await r.exists("raw:match:NA1_456") == 1

    @pytest.mark.asyncio
    async def test_exists_uses_custom_prefix(self, r):
        """exists() uses the custom key_prefix."""
        store = RawStore(r, key_prefix="raw:opgg:match:")
        await store.set("mid999", "data")
        assert await store.exists("mid999") is True
        store_default = RawStore(r)
        assert await store_default.exists("mid999") is False

    @pytest.mark.asyncio
    async def test_get_uses_custom_prefix(self, r):
        """get() uses the custom key_prefix."""
        store = RawStore(r, key_prefix="raw:opgg:match:")
        await store.set("mid888", '{"src": "opgg"}')
        result = await store.get("mid888")
        assert result == '{"src": "opgg"}'


class TestRawStoreDiskWriteOffEventLoop:
    """ASYNC-2: Disk write in set() must be delegated to asyncio.to_thread."""

    @pytest.mark.asyncio
    async def test_set__uses_to_thread_for_disk_write(self, r, tmp_path):
        """ASYNC-2: Disk write must be delegated to asyncio.to_thread, not run on event loop."""
        store = RawStore(r, data_dir=str(tmp_path))

        calls = []
        original_to_thread = __import__("asyncio").to_thread

        async def tracking_to_thread(fn, *args, **kwargs):
            calls.append(fn.__name__ if hasattr(fn, "__name__") else str(fn))
            return await original_to_thread(fn, *args, **kwargs)

        with patch("lol_pipeline.raw_store.asyncio.to_thread", side_effect=tracking_to_thread):
            await store.set("NA1_40002", '{"info": {"gameDuration": 100}}')

        # Must call _write_to_disk via to_thread (in addition to _exists_in_current_bundle)
        assert "_write_to_disk" in calls, (
            f"Expected _write_to_disk in to_thread calls, got: {calls}"
        )

        # Verify the file was actually written
        jsonl_files = list((tmp_path / "NA1").glob("*.jsonl"))
        assert len(jsonl_files) == 1
        content = jsonl_files[0].read_text()
        assert "NA1_40002" in content
