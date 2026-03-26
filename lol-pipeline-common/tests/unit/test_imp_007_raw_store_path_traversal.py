"""IMP-007: RawStore path traversal prevention tests."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.raw_store import RawStore


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestPathTraversal:
    async def test_traversal_match_id_rejected(self, r, tmp_path):
        """Match ID with path traversal component is rejected with ValueError."""
        store = RawStore(r, data_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid match_id"):
            await store.set("NA1_../../../etc/passwd", '{"evil": true}')

    async def test_traversal_in_get_rejected(self, r, tmp_path):
        """get() rejects traversal match IDs with ValueError."""
        store = RawStore(r, data_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid match_id"):
            await store.get("NA1_../../../etc/passwd")

    async def test_traversal_in_exists_rejected(self, r, tmp_path):
        """exists() rejects traversal match IDs with ValueError."""
        store = RawStore(r, data_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid match_id"):
            await store.exists("NA1_../../../etc/passwd")

    async def test_dots_only_match_id_rejected(self, r, tmp_path):
        """Match ID with dots and slashes is rejected."""
        store = RawStore(r, data_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid match_id"):
            await store.set("../../etc_passwd", '{"evil": true}')

    async def test_valid_match_id_accepted(self, r, tmp_path):
        """Normal match IDs pass validation."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("NA1_12345", '{"ok": true}')
        assert await store.exists("NA1_12345") is True

    async def test_valid_kr_match_id_accepted(self, r, tmp_path):
        """Korean server match IDs pass validation."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("KR_67890", '{"ok": true}')
        assert await store.exists("KR_67890") is True

    async def test_valid_euw1_match_id_accepted(self, r, tmp_path):
        """EUW1 match IDs pass validation (region with digit suffix)."""
        store = RawStore(r, data_dir=str(tmp_path))
        await store.set("EUW1_12345", '{"ok": true}')
        assert await store.exists("EUW1_12345") is True

    async def test_lowercase_match_id_rejected(self, r, tmp_path):
        """Lowercase platform prefix is rejected."""
        store = RawStore(r, data_dir=str(tmp_path))
        with pytest.raises(ValueError, match="invalid match_id"):
            await store.set("na1_12345", '{"bad": true}')

    async def test_no_data_dir_skips_validation(self, r):
        """Without data_dir, match_id validation is skipped (Redis-only mode)."""
        store = RawStore(r)
        # No data_dir — _bundle_path returns None before validation
        await store.set("NA1_12345", '{"ok": true}')
        result = await store.get("NA1_12345")
        assert result == '{"ok": true}'
