"""Unit tests for admin-ui _helpers.py (PRIN-AUI-02) and startup validation (PRIN-AUI-03)."""

from __future__ import annotations

import importlib
import sys

import fakeredis.aioredis
import pytest

from lol_admin_ui._helpers import (
    clear_dlq,
    clear_system_halted,
    list_dlq_entries,
    replay_entry,
    set_system_halted,
)


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.flushall()
    await redis.aclose()


# -------------------------------------------------------------------------
# PRIN-AUI-03: startup fails when ADMIN_UI_SECRET is unset or empty
# -------------------------------------------------------------------------


class TestAdminUiStartupValidation:
    """PRIN-AUI-03: admin-ui refuses to start without ADMIN_UI_SECRET."""

    def test_missing_secret__raises_value_error(self, monkeypatch):
        """Importing main without ADMIN_UI_SECRET raises ValueError."""
        monkeypatch.delenv("ADMIN_UI_SECRET", raising=False)
        # Remove cached module so re-import triggers validation
        sys.modules.pop("lol_admin_ui.main", None)
        with pytest.raises(ValueError, match="ADMIN_UI_SECRET"):
            importlib.import_module("lol_admin_ui.main")

    def test_empty_secret__raises_value_error(self, monkeypatch):
        """Importing main with empty ADMIN_UI_SECRET raises ValueError."""
        monkeypatch.setenv("ADMIN_UI_SECRET", "")
        sys.modules.pop("lol_admin_ui.main", None)
        with pytest.raises(ValueError, match="ADMIN_UI_SECRET"):
            importlib.import_module("lol_admin_ui.main")

    def test_valid_secret__no_error(self, monkeypatch):
        """Importing main with a valid secret succeeds."""
        monkeypatch.setenv("ADMIN_UI_SECRET", "my-secret-123")
        sys.modules.pop("lol_admin_ui.main", None)
        mod = importlib.import_module("lol_admin_ui.main")
        assert hasattr(mod, "app")


# -------------------------------------------------------------------------
# PRIN-AUI-02: _helpers.py business logic functions
# -------------------------------------------------------------------------


class TestListDlqEntries:
    """list_dlq_entries returns paginated DLQ entries with total and cursor."""

    async def test_list_dlq_entries__empty__returns_empty_list(self, r):
        entries, total, next_cursor = await list_dlq_entries(r)
        assert entries == []
        assert total == 0
        assert next_cursor is None

    async def test_list_dlq_entries__with_entries__includes_id(self, r):
        entry_id = await r.xadd("stream:dlq", {"failure_code": "http_429", "payload": "{}"})
        entries, total, _cursor = await list_dlq_entries(r)
        assert len(entries) == 1
        assert total == 1
        assert entries[0]["id"] == entry_id
        assert entries[0]["failure_code"] == "http_429"

    async def test_list_dlq_entries__multiple_entries(self, r):
        for i in range(3):
            await r.xadd("stream:dlq", {"failure_code": f"code_{i}"})
        entries, total, _cursor = await list_dlq_entries(r)
        assert len(entries) == 3
        assert total == 3

    async def test_list_dlq_entries__respects_count_limit(self, r):
        for i in range(5):
            await r.xadd("stream:dlq", {"failure_code": f"code_{i}"})
        entries, total, next_cursor = await list_dlq_entries(r, count=2)
        assert len(entries) == 2
        assert total == 5
        assert next_cursor is not None

    async def test_list_dlq_entries__cursor_paginates(self, r):
        for i in range(5):
            await r.xadd("stream:dlq", {"failure_code": f"code_{i}"})
        page1, total1, cursor1 = await list_dlq_entries(r, count=3)
        assert len(page1) == 3
        assert total1 == 5
        assert cursor1 is not None
        page2, total2, cursor2 = await list_dlq_entries(r, cursor=cursor1, count=3)
        assert len(page2) >= 1
        assert total2 == 5


class TestReplayEntry:
    """replay_entry replays a DLQ message to its original stream."""

    async def test_replay_entry__valid__returns_tuple(self, r):
        entry_id = await r.xadd(
            "stream:dlq",
            {"original_stream": "stream:match_id", "type": "match_id", "payload": "{}"},
        )
        result = await replay_entry(r, entry_id)
        assert result is not None
        assert result[0] == entry_id
        assert result[1] == "stream:match_id"

    async def test_replay_entry__valid__removes_from_dlq(self, r):
        entry_id = await r.xadd(
            "stream:dlq",
            {"original_stream": "stream:match_id", "type": "match_id"},
        )
        await replay_entry(r, entry_id)
        remaining = await r.xlen("stream:dlq")
        assert remaining == 0

    async def test_replay_entry__valid__publishes_to_original_stream(self, r):
        await r.xadd(
            "stream:dlq",
            {"original_stream": "stream:parse", "type": "parse", "data": "test"},
        )
        entries = await r.xrange("stream:dlq")
        entry_id = entries[0][0]
        await replay_entry(r, entry_id)
        target_entries = await r.xrange("stream:parse")
        assert len(target_entries) == 1
        # original_stream field should NOT be in the republished entry
        assert "original_stream" not in target_entries[0][1]

    async def test_replay_entry__not_found__returns_none(self, r):
        result = await replay_entry(r, "9999999-0")
        assert result is None

    async def test_replay_entry__no_original_stream__raises_value_error(self, r):
        entry_id = await r.xadd("stream:dlq", {"type": "test", "payload": "{}"})
        with pytest.raises(ValueError, match="original_stream"):
            await replay_entry(r, entry_id)


class TestClearDlq:
    """clear_dlq removes all entries from stream:dlq."""

    async def test_clear_dlq__removes_entries(self, r):
        for i in range(5):
            await r.xadd("stream:dlq", {"data": str(i)})
        await clear_dlq(r)
        exists = await r.exists("stream:dlq")
        assert exists == 0

    async def test_clear_dlq__empty__noop(self, r):
        await clear_dlq(r)  # Should not raise


class TestSetSystemHalted:
    """set_system_halted sets the system:halted key."""

    async def test_set_system_halted__sets_key(self, r):
        await set_system_halted(r)
        val = await r.get("system:halted")
        assert val == "1"


class TestClearSystemHalted:
    """clear_system_halted removes the system:halted key."""

    async def test_clear_system_halted__removes_key(self, r):
        await r.set("system:halted", "1")
        await clear_system_halted(r)
        val = await r.get("system:halted")
        assert val is None

    async def test_clear_system_halted__missing_key__noop(self, r):
        await clear_system_halted(r)  # Should not raise
