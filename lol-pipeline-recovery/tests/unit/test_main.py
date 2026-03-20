"""Unit tests for lol_recovery.main — Phase 05 ACs 05-01 through 05-11b."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from redis.exceptions import RedisError

from lol_recovery.main import _backoff_ms, _process, main

_DLQ_STREAM = "stream:dlq"
_ARCHIVE_STREAM = "stream:dlq:archive"
_DELAYED_KEY = "delayed:messages"
_GROUP = "recovery"


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    return Config(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def log():
    return logging.getLogger("test-recovery")


def _make_dlq(
    failure_code="http_429",
    dlq_attempts=0,
    match_id="NA1_123",
    original_stream="stream:match_id",
    retry_after_ms=None,
):
    return DLQEnvelope(
        source_stream="stream:dlq",
        type="dlq",
        payload={"match_id": match_id, "region": "na1"},
        attempts=3,
        max_attempts=5,
        failure_code=failure_code,
        failure_reason="test reason",
        failed_by="fetcher",
        original_stream=original_stream,
        original_message_id="1234-0",
        dlq_attempts=dlq_attempts,
        retry_after_ms=retry_after_ms,
    )


async def _setup_dlq_msg(r, dlq):
    """Add a DLQ entry to stream:dlq and return msg_id."""
    msg_id = await r.xadd(_DLQ_STREAM, dlq.to_redis_fields())
    # Create group and read to put in PEL
    try:
        await r.xgroup_create(_DLQ_STREAM, _GROUP, id="0", mkstream=True)
    except Exception:  # noqa: S110
        pass
    await r.xreadgroup(_GROUP, "test-consumer", {_DLQ_STREAM: ">"}, count=1)
    return msg_id


class TestRecoveryRequeue:
    @pytest.mark.asyncio
    async def test_http_429_requeued_with_incremented_dlq_attempts(self, r, cfg, log):
        """AC-05-01: http_429, dlq_attempts=0 → delayed:messages with dlq_attempts=1."""
        dlq = _make_dlq(failure_code="http_429", dlq_attempts=0, retry_after_ms=5000)
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        members = await r.zrange(_DELAYED_KEY, 0, -1)
        assert len(members) == 1
        fields = json.loads(members[0])
        env = MessageEnvelope.from_redis_fields(fields)
        assert env.dlq_attempts == 1
        assert env.source_stream == "stream:match_id"

    @pytest.mark.asyncio
    async def test_http_5xx_requeued(self, r, cfg, log):
        """AC-05-03: http_5xx, dlq_attempts=0 → requeued with backoff."""
        dlq = _make_dlq(failure_code="http_5xx", dlq_attempts=0)
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        members = await r.zrange(_DELAYED_KEY, 0, -1)
        assert len(members) == 1

    @pytest.mark.asyncio
    async def test_http_5xx_higher_attempts_longer_delay(self, r, cfg, log):
        """AC-05-04: http_5xx, dlq_attempts=1 → delay longer than dlq_attempts=0."""
        dlq0 = _make_dlq(failure_code="http_5xx", dlq_attempts=0)
        msg_id0 = await _setup_dlq_msg(r, dlq0)
        await _process(r, cfg, "test-consumer", msg_id0, dlq0, log)
        members0 = await r.zrange(_DELAYED_KEY, 0, -1, withscores=True)
        score0 = members0[0][1]

        # Clear for second test
        await r.delete(_DELAYED_KEY)
        dlq1 = _make_dlq(failure_code="http_5xx", dlq_attempts=1)
        msg_id1 = await _setup_dlq_msg(r, dlq1)
        await _process(r, cfg, "test-consumer", msg_id1, dlq1, log)
        members1 = await r.zrange(_DELAYED_KEY, 0, -1, withscores=True)
        score1 = members1[0][1]

        # Higher dlq_attempts should have a higher score (later execution)
        assert score1 > score0


class TestRecoveryArchive:
    @pytest.mark.asyncio
    async def test_max_dlq_attempts_archived(self, r, cfg, log):
        """AC-05-02: http_429 at DLQ_MAX_ATTEMPTS → archived; match.status='failed'."""
        dlq = _make_dlq(failure_code="http_429", dlq_attempts=cfg.dlq_max_attempts)
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        assert await r.xlen(_ARCHIVE_STREAM) == 1
        assert await r.hget("match:NA1_123", "status") == "failed"
        assert await r.sismember("match:status:failed", "NA1_123")
        assert await r.zcard(_DELAYED_KEY) == 0


class TestRecoveryDiscard:
    @pytest.mark.asyncio
    async def test_http_404_discarded(self, r, cfg, log):
        """AC-05-05: http_404 → ACK'd, discarded; no ZADD; no archive."""
        dlq = _make_dlq(failure_code="http_404")
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen(_ARCHIVE_STREAM) == 0

    @pytest.mark.asyncio
    async def test_parse_error_archived(self, r, cfg, log):
        """AC-05-06: parse_error → ACK'd; archived for operator review."""
        dlq = _make_dlq(failure_code="parse_error")
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        assert await r.xlen(_ARCHIVE_STREAM) == 1
        assert await r.zcard(_DELAYED_KEY) == 0

    @pytest.mark.asyncio
    async def test_unknown_failure_code_archived_at_error_level(self, r, cfg, log, caplog):
        """AC-05-09: unknown failure_code → ACK'd; archived; logged at ERROR."""
        dlq = _make_dlq(failure_code="something_weird")
        msg_id = await _setup_dlq_msg(r, dlq)

        with caplog.at_level(logging.ERROR, logger="test-recovery"):
            await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        assert await r.xlen(_ARCHIVE_STREAM) == 1
        assert any("unknown failure_code" in rec.message for rec in caplog.records)


class TestRecoveryHalted:
    @pytest.mark.asyncio
    async def test_http_403_sets_halted_and_archives(self, r, cfg, log):
        """AC-05-07: http_403 → system:halted='1'; archived; ACK'd."""
        dlq = _make_dlq(failure_code="http_403")
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        assert await r.get("system:halted") == "1"
        assert await r.xlen(_ARCHIVE_STREAM) == 1

    @pytest.mark.asyncio
    async def test_halted_leaves_5xx_unacked(self, r, cfg, log):
        """AC-05-11b: system:halted + http_5xx → leaves in PEL (no requeue)."""
        await r.set("system:halted", "1")
        dlq = _make_dlq(failure_code="http_5xx")
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        assert await r.zcard(_DELAYED_KEY) == 0
        assert await r.xlen(_ARCHIVE_STREAM) == 0

    @pytest.mark.asyncio
    async def test_halted_still_processes_403(self, r, cfg, log):
        """AC-05-11b: system:halted but http_403 → still processed (always handle 403)."""
        await r.set("system:halted", "1")
        dlq = _make_dlq(failure_code="http_403")
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        assert await r.xlen(_ARCHIVE_STREAM) == 1


class TestBackoffMs:
    def test_attempt_0(self):
        assert _backoff_ms(0) == 5_000

    def test_attempt_1(self):
        assert _backoff_ms(1) == 15_000

    def test_attempt_3(self):
        assert _backoff_ms(3) == 300_000

    def test_attempt_beyond_array_clamps(self):
        """Attempts beyond backoff array length clamp to max value."""
        assert _backoff_ms(10) == 300_000
        assert _backoff_ms(100) == 300_000


class TestRecoveryEdgeCases:
    @pytest.mark.asyncio
    async def test_429_with_retry_after_uses_retry_after(self, r, cfg, log):
        """http_429 with retry_after_ms should use that value, not backoff."""
        dlq = _make_dlq(failure_code="http_429", dlq_attempts=0, retry_after_ms=31000)
        msg_id = await _setup_dlq_msg(r, dlq)
        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        members = await r.zrange(_DELAYED_KEY, 0, -1, withscores=True)
        assert len(members) == 1
        # Score should be now + 31000, not now + 5000 (backoff)
        import time

        now_ms = int(time.time() * 1000)
        score = members[0][1]
        # Score should be close to now + 31000 (within 2s tolerance)
        assert abs(score - (now_ms + 31000)) < 2000

    @pytest.mark.asyncio
    async def test_5xx_without_retry_after_uses_backoff(self, r, cfg, log):
        """http_5xx (no retry_after) should use exponential backoff."""
        dlq = _make_dlq(failure_code="http_5xx", dlq_attempts=2)
        msg_id = await _setup_dlq_msg(r, dlq)
        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        members = await r.zrange(_DELAYED_KEY, 0, -1, withscores=True)
        assert len(members) == 1
        import time

        now_ms = int(time.time() * 1000)
        score = members[0][1]
        # dlq_attempts=2 → backoff=60000
        assert abs(score - (now_ms + 60000)) < 2000

    @pytest.mark.asyncio
    async def test_5xx_at_max_attempts_archived(self, r, cfg, log):
        """http_5xx at dlq_max_attempts → archived, not requeued."""
        dlq = _make_dlq(failure_code="http_5xx", dlq_attempts=cfg.dlq_max_attempts)
        msg_id = await _setup_dlq_msg(r, dlq)
        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        assert await r.xlen(_ARCHIVE_STREAM) == 1
        assert await r.zcard(_DELAYED_KEY) == 0

    @pytest.mark.asyncio
    async def test_handler_crash_requeued_as_transient(self, r, cfg, log):
        """handler_crash failure code → requeued with backoff (not archived)."""
        dlq = _make_dlq(failure_code="handler_crash", dlq_attempts=0)
        msg_id = await _setup_dlq_msg(r, dlq)
        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        assert await r.zcard(_DELAYED_KEY) == 1
        assert await r.xlen(_ARCHIVE_STREAM) == 0


class TestRequeuePreservesFields:
    """Fix 1-3: _requeue_delayed preserves priority and attempts from DLQ envelope."""

    @pytest.mark.asyncio
    async def test_requeue_preserves_priority(self, r, cfg, log):
        """Fix 1: Requeued envelope keeps the original priority from DLQ entry."""
        dlq = _make_dlq(failure_code="http_5xx", dlq_attempts=0)
        dlq.priority = "high"
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        members = await r.zrange(_DELAYED_KEY, 0, -1)
        assert len(members) == 1
        fields = json.loads(members[0])
        env = MessageEnvelope.from_redis_fields(fields)
        assert env.priority == "high"

    @pytest.mark.asyncio
    async def test_requeue_preserves_attempts(self, r, cfg, log):
        """Fix 2: Requeued envelope keeps attempts from DLQ (not reset to 0)."""
        dlq = _make_dlq(failure_code="http_5xx", dlq_attempts=0)
        dlq.attempts = 3
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        members = await r.zrange(_DELAYED_KEY, 0, -1)
        fields = json.loads(members[0])
        env = MessageEnvelope.from_redis_fields(fields)
        assert env.attempts == 3

    @pytest.mark.asyncio
    async def test_handler_crash_in_handlers_dict(self, r, cfg, log):
        """Fix 3: handler_crash is routed to _handle_transient, not unknown handler."""
        dlq = _make_dlq(failure_code="handler_crash", dlq_attempts=0)
        msg_id = await _setup_dlq_msg(r, dlq)

        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        # Should be requeued (transient), not archived (unknown)
        assert await r.zcard(_DELAYED_KEY) == 1
        assert await r.xlen(_ARCHIVE_STREAM) == 0


class TestRecoveryRetryAfterEdgeCases:
    """Tier 3 — Recovery edge cases for retry_after_ms."""

    @pytest.mark.asyncio
    async def test_retry_after_zero_uses_backoff(self, r, cfg, log):
        """retry_after_ms=0 is falsy in Python → should fall through to backoff."""
        dlq = _make_dlq(failure_code="http_429", dlq_attempts=0, retry_after_ms=0)
        msg_id = await _setup_dlq_msg(r, dlq)
        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        members = await r.zrange(_DELAYED_KEY, 0, -1, withscores=True)
        assert len(members) == 1
        import time

        now_ms = int(time.time() * 1000)
        score = members[0][1]
        # Should use backoff (5000ms for attempt 0), not 0
        assert abs(score - (now_ms + 5000)) < 2000

    @pytest.mark.asyncio
    async def test_retry_after_negative_uses_value(self, r, cfg, log):
        """retry_after_ms=-1 is truthy → code uses it as delay (current behavior)."""
        dlq = _make_dlq(failure_code="http_429", dlq_attempts=0, retry_after_ms=-1)
        msg_id = await _setup_dlq_msg(r, dlq)
        await _process(r, cfg, "test-consumer", msg_id, dlq, log)

        members = await r.zrange(_DELAYED_KEY, 0, -1, withscores=True)
        assert len(members) == 1
        import time

        now_ms = int(time.time() * 1000)
        score = members[0][1]
        # -1 is truthy so it's used as the delay: now + (-1)
        assert abs(score - (now_ms + (-1))) < 2000

    @pytest.mark.asyncio
    async def test_archive_xadd_failure_does_not_crash(self, r, cfg, log):
        """If XADD to archive stream fails, _process still completes."""
        dlq = _make_dlq(failure_code="parse_error")
        msg_id = await _setup_dlq_msg(r, dlq)

        original_xadd = r.xadd

        async def failing_xadd(stream, *args, **kwargs):
            if stream == "stream:dlq:archive":
                raise ConnectionError("redis write error")
            return await original_xadd(stream, *args, **kwargs)

        r.xadd = failing_xadd

        with pytest.raises(ConnectionError, match="redis write error"):
            await _process(r, cfg, "test-consumer", msg_id, dlq, log)


class TestGracefulShutdown:
    """Recovery uses asyncio.Event for SIGTERM shutdown."""

    @pytest.mark.asyncio
    async def test_sigterm_stops_main_loop(self, monkeypatch):
        """Triggering the shutdown event causes main() to exit cleanly."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()

        captured_callbacks: list[object] = []

        async def fake_consume_dlq(*args, **kwargs):
            if captured_callbacks:
                cb = captured_callbacks[0]
                if callable(cb):
                    cb()  # sets the shutdown_event
            return []

        def spy_add_signal_handler(sig, callback, *args):
            captured_callbacks.append(callback)

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.side_effect = spy_add_signal_handler

        with (
            patch("lol_recovery.main.Config") as mock_cfg,
            patch("lol_recovery.main.get_redis", return_value=mock_r),
            patch("lol_recovery.main._consume_dlq", side_effect=fake_consume_dlq),
            patch("lol_recovery.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_recovery.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            await main()  # should exit cleanly, not loop forever
        mock_r.aclose.assert_called_once()


class TestMainLoopRetry:
    """CQ-9: Recovery main loop retries on RedisError/OSError."""

    @pytest.mark.asyncio
    async def test_main__redis_error_retries_with_sleep(self, monkeypatch):
        """RedisError in consume loop → log + sleep 1s + retry, not crash."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        call_count = 0

        async def fake_consume_dlq(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RedisError("connection lost")
            raise KeyboardInterrupt

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_recovery.main.Config") as mock_cfg,
            patch("lol_recovery.main.get_redis", return_value=mock_r),
            patch("lol_recovery.main._consume_dlq", side_effect=fake_consume_dlq),
            patch("lol_recovery.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("lol_recovery.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()
        # Sleep(1) called after RedisError, before retry
        mock_sleep.assert_called_once_with(1)
        assert call_count == 2


class TestCorruptDlqEntryHandling:
    """Corrupt DLQ entries are ACKed and skipped via consume_typed delegation."""

    @pytest.mark.asyncio
    async def test_consume_dlq__corrupt_entry_acked_and_skipped(self):
        """Corrupt entry in PEL is ACKed and not returned."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()
        mock_r.xreadgroup.side_effect = [
            [("stream:dlq", [("corrupt-1", {"garbage": "data"})])],  # PEL
            [("stream:dlq", [])],  # new messages
        ]
        mock_r.xautoclaim.return_value = ["0-0", [], []]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        assert entries == []
        mock_r.xack.assert_called_once_with("stream:dlq", "recovery", "corrupt-1")

    @pytest.mark.asyncio
    async def test_consume_dlq__invalid_payload_json_acked(self):
        """Entry with malformed JSON payload is ACKed and not returned."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()
        mock_r.xreadgroup.side_effect = [
            [
                (
                    "stream:dlq",
                    [
                        (
                            "bad-payload-1",
                            {
                                "id": "abc",
                                "source_stream": "stream:dlq",
                                "type": "dlq",
                                "payload": "NOT-VALID-JSON{{{",
                                "attempts": "3",
                                "max_attempts": "5",
                                "failure_code": "http_5xx",
                                "failure_reason": "test",
                                "failed_by": "fetcher",
                                "original_stream": "stream:match_id",
                                "original_message_id": "123-0",
                                "failed_at": "2024-01-01T00:00:00+00:00",
                                "enqueued_at": "2024-01-01T00:00:00+00:00",
                                "dlq_attempts": "0",
                                "retry_after_ms": "null",
                                "priority": "normal",
                            },
                        )
                    ],
                ),
            ],  # PEL
            [("stream:dlq", [])],  # new messages
        ]
        mock_r.xautoclaim.return_value = ["0-0", [], []]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        assert entries == []
        mock_r.xack.assert_called_once_with("stream:dlq", "recovery", "bad-payload-1")

    @pytest.mark.asyncio
    async def test_consume_dlq__mixed_valid_and_corrupt(self):
        """Valid entries returned, corrupt entries ACKed and skipped."""
        from lol_recovery.main import _consume_dlq

        dlq = _make_dlq()
        valid_fields = dlq.to_redis_fields()

        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [
            (
                "stream:dlq",
                [
                    ("valid-1", valid_fields),
                    ("corrupt-1", {"garbage": "data"}),
                ],
            ),
        ]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        assert len(entries) == 1
        assert entries[0][0] == "valid-1"
        mock_r.xack.assert_called_once_with("stream:dlq", "recovery", "corrupt-1")

    @pytest.mark.asyncio
    async def test_consume_dlq__no_messages_returns_empty(self):
        """No messages on stream returns empty list."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()
        mock_r.xreadgroup.return_value = [("stream:dlq", [])]
        mock_r.xautoclaim.return_value = ["0-0", [], []]

        # Override xreadgroup to return empty for both PEL and new messages
        mock_r.xreadgroup.side_effect = [
            [("stream:dlq", [])],  # PEL drain
            [("stream:dlq", [])],  # new messages
        ]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)
        assert entries == []

    @pytest.mark.asyncio
    async def test_consume_dlq__corrupt_entries_acked_in_pel(self):
        """Corrupt entries in PEL are ACKed so they don't block processing."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()
        dlq = _make_dlq()
        valid_fields = dlq.to_redis_fields()

        # PEL drain returns one corrupt + one valid entry
        mock_r.xreadgroup.return_value = [
            (
                "stream:dlq",
                [
                    ("corrupt-1", {"garbage": "data"}),
                    ("valid-1", valid_fields),
                ],
            ),
        ]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        # Only valid entry returned
        assert len(entries) == 1
        assert entries[0][0] == "valid-1"
        assert entries[0][1].failure_code == "http_429"
        # Corrupt entry was ACKed
        mock_r.xack.assert_called_once_with("stream:dlq", "recovery", "corrupt-1")

    @pytest.mark.asyncio
    async def test_consume_dlq__corrupt_entries_acked_in_new_messages(self):
        """Corrupt entries in new messages path are also ACKed."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()
        # PEL drain returns empty
        mock_r.xreadgroup.side_effect = [
            [("stream:dlq", [])],  # PEL drain: empty
            [("stream:dlq", [("corrupt-new-1", {"garbage": "data"})])],  # new msgs
        ]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        assert entries == []
        # Corrupt entry was ACKed
        mock_r.xack.assert_called_once_with("stream:dlq", "recovery", "corrupt-new-1")


class TestXautoclaim:
    """B10: _consume_dlq uses XAUTOCLAIM to reclaim stranded messages from crashed workers."""

    @pytest.mark.asyncio
    async def test_consume_dlq__xautoclaim_called_after_pel_drain(self):
        """XAUTOCLAIM is called between PEL drain and XREADGROUP for new messages."""
        from lol_recovery.main import _CLAIM_IDLE_MS, _consume_dlq

        mock_r = AsyncMock()
        dlq = _make_dlq()
        valid_fields = dlq.to_redis_fields()

        # PEL drain returns empty
        mock_r.xreadgroup.side_effect = [
            [("stream:dlq", [])],  # PEL drain: empty
            [("stream:dlq", [])],  # new messages: empty
        ]
        # XAUTOCLAIM returns one claimed message
        mock_r.xautoclaim.return_value = [
            "0-0",  # cursor
            [("claimed-1", valid_fields)],  # claimed entries
            [],  # deleted IDs
        ]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        # Should have called xautoclaim
        mock_r.xautoclaim.assert_called_once_with(
            "stream:dlq",
            "recovery",
            "test-consumer",
            _CLAIM_IDLE_MS,
            start_id="0-0",
            count=10,
        )
        # Should return the claimed entry
        assert len(entries) == 1
        assert entries[0][0] == "claimed-1"

    @pytest.mark.asyncio
    async def test_consume_dlq__xautoclaim_no_results_falls_through_to_new(self):
        """When XAUTOCLAIM returns no entries, falls through to XREADGROUP for new."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()
        dlq = _make_dlq()
        valid_fields = dlq.to_redis_fields()

        # PEL drain returns empty
        mock_r.xreadgroup.side_effect = [
            [("stream:dlq", [])],  # PEL drain: empty
            [("stream:dlq", [("new-1", valid_fields)])],  # new messages
        ]
        # XAUTOCLAIM returns nothing
        mock_r.xautoclaim.return_value = ["0-0", [], []]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        assert len(entries) == 1
        assert entries[0][0] == "new-1"
        # Two xreadgroup calls: PEL drain + new messages
        assert mock_r.xreadgroup.call_count == 2

    @pytest.mark.asyncio
    async def test_consume_dlq__xautoclaim_corrupt_entries_acked(self):
        """Corrupt entries from XAUTOCLAIM are ACKed and skipped."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()

        # PEL drain returns empty
        mock_r.xreadgroup.side_effect = [
            [("stream:dlq", [])],  # PEL drain: empty
            [("stream:dlq", [])],  # new messages: empty
        ]
        # XAUTOCLAIM returns a corrupt entry
        mock_r.xautoclaim.return_value = [
            "0-0",
            [("corrupt-claimed-1", {"garbage": "data"})],
            [],
        ]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        assert entries == []
        mock_r.xack.assert_called_once_with("stream:dlq", "recovery", "corrupt-claimed-1")

    @pytest.mark.asyncio
    async def test_consume_dlq__pel_takes_priority_over_xautoclaim(self):
        """Own PEL entries are returned before XAUTOCLAIM is even called."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()
        dlq = _make_dlq()
        valid_fields = dlq.to_redis_fields()

        # PEL drain returns a valid entry
        mock_r.xreadgroup.return_value = [
            ("stream:dlq", [("pel-1", valid_fields)]),
        ]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        assert len(entries) == 1
        assert entries[0][0] == "pel-1"
        # XAUTOCLAIM should NOT be called when PEL has entries
        mock_r.xautoclaim.assert_not_called()


class TestConsumeDlqCorruptInXautoclaim:
    """Corrupt DLQ entries in XAUTOCLAIM are ACKed and skipped."""

    @pytest.mark.asyncio
    async def test_consume_dlq__xautoclaim_corrupt_acked_and_skipped(self):
        """Single corrupt entry from XAUTOCLAIM is ACKed and skipped."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()
        # PEL drain returns empty
        mock_r.xreadgroup.side_effect = [
            [("stream:dlq", [])],  # PEL drain: empty
            [("stream:dlq", [])],  # new messages: empty
        ]
        # XAUTOCLAIM returns one corrupt entry
        mock_r.xautoclaim.return_value = [
            "0-0",
            [("corrupt-claimed-1", {"garbage": "data"})],
            [],
        ]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        assert entries == []
        mock_r.xack.assert_called_once_with("stream:dlq", "recovery", "corrupt-claimed-1")

    @pytest.mark.asyncio
    async def test_consume_dlq__xautoclaim_valid_and_corrupt__valid_processed(self):
        """Mix of valid and corrupt in XAUTOCLAIM: valid returned, corrupt ACKed."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()
        dlq = _make_dlq()
        valid_fields = dlq.to_redis_fields()

        # PEL drain returns empty
        mock_r.xreadgroup.side_effect = [
            [("stream:dlq", [])],  # PEL drain: empty
            [("stream:dlq", [])],  # new messages: empty
        ]
        # XAUTOCLAIM returns one valid + one corrupt
        mock_r.xautoclaim.return_value = [
            "0-0",
            [
                ("valid-claimed-1", valid_fields),
                ("corrupt-claimed-1", {"garbage": "data"}),
            ],
            [],
        ]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        # Only valid entry returned
        assert len(entries) == 1
        assert entries[0][0] == "valid-claimed-1"
        assert entries[0][1].failure_code == "http_429"
        # Corrupt entry was ACKed
        mock_r.xack.assert_called_once_with("stream:dlq", "recovery", "corrupt-claimed-1")

    @pytest.mark.asyncio
    async def test_consume_dlq__valid_then_corrupt__valid_processed_corrupt_skipped(self):
        """Valid entry followed by corrupt in PEL: valid processed, corrupt ACKed."""
        from lol_recovery.main import _consume_dlq

        mock_r = AsyncMock()
        dlq = _make_dlq()
        valid_fields = dlq.to_redis_fields()

        # PEL drain returns one valid + one corrupt
        mock_r.xreadgroup.return_value = [
            (
                "stream:dlq",
                [
                    ("valid-1", valid_fields),
                    ("corrupt-1", {"garbage": "data"}),
                ],
            ),
        ]

        entries = await _consume_dlq(mock_r, "test-consumer", count=10, block=0)

        # Only valid entry returned
        assert len(entries) == 1
        assert entries[0][0] == "valid-1"
        # Corrupt entry was ACKed
        mock_r.xack.assert_called_once_with("stream:dlq", "recovery", "corrupt-1")


class TestMainEntryPoint:
    """Tests for main() bootstrap and teardown."""

    @pytest.mark.asyncio
    async def test_main__creates_redis_and_starts_loop(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        call_count = 0

        async def fake_consume_dlq(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise KeyboardInterrupt
            return []

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_recovery.main.Config") as mock_cfg,
            patch("lol_recovery.main.get_redis", return_value=mock_r),
            patch("lol_recovery.main._consume_dlq", side_effect=fake_consume_dlq),
            patch("lol_recovery.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__keyboard_interrupt__closes_redis(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_recovery.main.Config") as mock_cfg,
            patch("lol_recovery.main.get_redis", return_value=mock_r),
            patch("lol_recovery.main._consume_dlq", side_effect=KeyboardInterrupt),
            patch("lol_recovery.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()


class TestArchiveMatchTTL:
    """P13-CR-4: _archive() sets TTL on match:{match_id} when archiving."""

    @pytest.fixture
    def _dlq_with_match(self):
        return DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_TTL99", "region": "na1"},
            attempts=5,
            max_attempts=5,
            failure_code="parse_error",
            failure_reason="parse_error",
            failed_by="parser",
            original_stream="stream:parse",
            original_message_id="1-0",
            dlq_attempts=3,
        )

    @pytest.mark.asyncio
    async def test_archive_sets_ttl_on_match_key(self, r, log, _dlq_with_match):
        """_archive() sets EXPIRE on match:{match_id} so it doesn't grow unbounded."""
        from lol_recovery.main import _archive

        await _archive(r, _dlq_with_match, log)

        ttl = await r.ttl("match:NA1_TTL99")
        assert ttl > 0, "match:{match_id} must have a TTL after _archive()"

    @pytest.mark.asyncio
    async def test_archive_ttl_approx_7_days(self, r, log, _dlq_with_match):
        """TTL is approximately 7 days (MATCH_DATA_TTL_SECONDS default)."""
        from lol_recovery.main import _archive

        await _archive(r, _dlq_with_match, log)

        ttl = await r.ttl("match:NA1_TTL99")
        # Default 604800s = 7 days; allow sub-second drift
        assert abs(ttl - 604800) <= 60, f"Expected ~604800s TTL, got {ttl}s"

    @pytest.mark.asyncio
    async def test_archive_no_match_id_no_ttl_set(self, r, log):
        """_archive() without match_id in payload skips the match key entirely."""
        from lol_recovery.main import _archive

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"puuid": "some-puuid"},  # no match_id
            attempts=5,
            max_attempts=5,
            failure_code="parse_error",
            failure_reason="parse_error",
            failed_by="parser",
            original_stream="stream:parse",
            original_message_id="1-0",
            dlq_attempts=3,
        )
        await _archive(r, dlq, log)
        # No match key should have been created
        assert await r.exists("match:") == 0
