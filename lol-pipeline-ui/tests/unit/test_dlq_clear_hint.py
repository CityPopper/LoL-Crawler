"""E3: DLQ corrupt entry error message should show --all, not per-ID clear."""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis.aioredis
import pytest
from lol_pipeline.models import DLQEnvelope


class TestDlqCorruptEntryHint:
    """E3: Corrupt DLQ entry error message should show --all, not per-ID clear."""

    @pytest.mark.asyncio
    async def test_dlq_replay__corrupt_entry__shows_clear_all_hint(self):
        """Corrupt entry response should suggest 'dlq clear --all', not 'dlq clear {id}'."""
        from lol_ui.routes.dlq import dlq_replay

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        entry_id = await r.xadd("stream:dlq", {"garbage": "data"})

        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = MagicMock(max_attempts=5)

        resp = await dlq_replay(request, entry_id)

        assert resp.status_code == 422
        body = resp.body.decode()
        # Must NOT contain per-ID clear hint (the old buggy form)
        assert f"dlq clear {entry_id}" not in body
        # Must contain --all hint
        assert "dlq clear --all" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq_replay__invalid_stream__shows_clear_all_hint(self):
        """Invalid stream response should suggest 'dlq clear --all', not 'dlq clear {id}'."""
        from lol_ui.routes.dlq import dlq_replay

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"puuid": "p1", "region": "na1"},
            attempts=1,
            max_attempts=5,
            failure_code="http_5xx",
            failure_reason="server error",
            failed_by="crawler",
            original_stream="stream:arbitrary-unknown",
            original_message_id="orig-x",
        )
        entry_id = await r.xadd("stream:dlq", dlq.to_redis_fields())

        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = MagicMock(max_attempts=5)

        resp = await dlq_replay(request, entry_id)

        assert resp.status_code == 422
        body = resp.body.decode()
        # Must NOT contain per-ID clear hint (the old buggy form)
        assert f"dlq clear {entry_id}" not in body
        # Must contain --all hint
        assert "dlq clear --all" in body
        await r.aclose()
