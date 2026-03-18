"""Unit tests for lol_pipeline.raw_store."""

from __future__ import annotations

import zstandard as zstd

import fakeredis.aioredis
import pytest

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
        content = "NA1_400\t{\"compressed\": true}\n"
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
