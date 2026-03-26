"""IMP-027: _write_to_disk uses atomic write pattern (no interleaved lines)."""

from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest

from lol_pipeline.raw_store import RawStore


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestAtomicDiskWrite:
    async def test_concurrent_writes_produce_valid_jsonl(self, r, tmp_path):
        """Two concurrent writes to the same bundle produce valid JSONL output."""
        store = RawStore(r, data_dir=str(tmp_path))

        # Write two different matches concurrently
        await asyncio.gather(
            store.set("NA1_1000", '{"match": 1000}'),
            store.set("NA1_1001", '{"match": 1001}'),
        )

        # Check the bundle file has valid JSONL (no interleaved lines)
        jsonl_files = list((tmp_path / "NA1").glob("*.jsonl"))
        assert len(jsonl_files) == 1
        content = jsonl_files[0].read_text()
        lines = [ln for ln in content.strip().split("\n") if ln]

        # Each line must be a valid tab-separated entry
        for line in lines:
            parts = line.split("\t", 1)
            assert len(parts) == 2, f"Malformed line: {line!r}"
            assert parts[0].startswith("NA1_")

        # Both match IDs must be present
        match_ids = {line.split("\t")[0] for line in lines}
        assert "NA1_1000" in match_ids
        assert "NA1_1001" in match_ids

    async def test_single_write_produces_valid_entry(self, r, tmp_path):
        """Single write produces a clean single-line JSONL entry."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_2000", '{"test": true}')

        jsonl_files = list((tmp_path / "NA1").glob("*.jsonl"))
        assert len(jsonl_files) == 1
        content = jsonl_files[0].read_text().strip()
        assert content == 'NA1_2000\t{"test": true}'
