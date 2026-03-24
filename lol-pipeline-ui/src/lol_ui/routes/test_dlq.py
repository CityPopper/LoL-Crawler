"""Tests for routes/dlq.py — expandable DLQ entries."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from lol_pipeline.models import DLQEnvelope

from lol_ui.routes.dlq import show_dlq


class TestDlqExpandableEntries:
    """DLQ entries are clickable to expand and show full payload."""

    @pytest.fixture
    async def _setup(self, r):
        """Seed a DLQ entry for testing."""
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="match_id",
            payload={"match_id": "NA1_123", "puuid": "abc", "region": "na1"},
            attempts=2,
            max_attempts=3,
            failure_code="http_429",
            failure_reason="Rate limited by Riot API",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="1-0",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())
        return r

    @pytest.mark.asyncio
    async def test_dlq_entry__has_expandable_row(self, r, _setup):
        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = bytes(resp.body).decode()

        assert "dlq-row" in body
        assert "dlq-detail" in body

    @pytest.mark.asyncio
    async def test_dlq_entry__detail_contains_full_payload(self, r, _setup):
        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = bytes(resp.body).decode()

        # Full payload should be in the detail row
        assert "NA1_123" in body
        assert "match_id" in body

    @pytest.mark.asyncio
    async def test_dlq_entry__detail_contains_failure_reason(self, r, _setup):
        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = bytes(resp.body).decode()

        assert "Rate limited" in body
        assert "Failure Reason" in body

    @pytest.mark.asyncio
    async def test_dlq_entry__onclick_toggles_detail(self, r, _setup):
        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = bytes(resp.body).decode()

        assert "classList.toggle" in body
        assert "'open'" in body

    @pytest.mark.asyncio
    async def test_dlq_entry__detail_has_colspan_7(self, r, _setup):
        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = bytes(resp.body).decode()

        assert 'colspan="7"' in body


class TestDlqUsesIsSystemHalted:
    """DRY-5: DLQ route uses is_system_halted() instead of raw r.get."""

    @pytest.mark.asyncio
    async def test_show_dlq__calls_is_system_halted(self, r):
        """show_dlq uses is_system_halted() for halt check."""
        mock_halted = AsyncMock(return_value=False)
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        with patch("lol_ui.routes.dlq.is_system_halted", mock_halted):
            await show_dlq(request)
        mock_halted.assert_called_once()
