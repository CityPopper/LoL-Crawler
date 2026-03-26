"""WATERFALL-5: Unit tests for _fetch_match() using WaterfallCoordinator.

These tests mock WaterfallCoordinator.fetch_match() (not individual sources)
and verify that _fetch_match() handles each WaterfallResult status correctly
per the proposed flow in design-source-waterfall.md Section 4.

The mock coordinator is passed as a parameter to _fetch_match(), matching the
pattern used for riot, raw_store, and opgg in the current signature. When
WATERFALL-5 is implemented, _fetch_match() gains a ``coordinator`` keyword
parameter that replaces the inline Riot API call, the _try_opgg() cache check,
and the per-source RawStore switching.

The existing _publish_and_ack, _set_match_status, _write_seen_match, and
_fetch_timeline_if_needed helpers are NOT mocked (except in the timeline test)
-- we assert on Redis state to verify outcomes, matching the style of
test_main.py.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.sources.base import FetchContext, WaterfallResult
from lol_pipeline.sources.coordinator import WaterfallCoordinator
from lol_pipeline.streams import consume, publish

from lol_fetcher.main import _fetch_match

_STREAM_IN = "stream:match_id"
_STREAM_OUT = "stream:parse"
_STREAM_DLQ = "stream:dlq"
_GROUP = "fetchers"

try:
    import lupa  # noqa: F401

    _LUPA_AVAILABLE = True
except ImportError:
    _LUPA_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _LUPA_AVAILABLE, reason="lupa required for rate limiter Lua scripts"
)


# ---------------------------------------------------------------------------
# Fixtures -- match test_main.py style
# ---------------------------------------------------------------------------


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
    return logging.getLogger("test-fetcher-waterfall")


def _match_envelope(match_id="NA1_W001", region="na1", puuid="test-puuid-w001"):
    return MessageEnvelope(
        source_stream=_STREAM_IN,
        type="match_id",
        payload={"match_id": match_id, "region": region, "puuid": puuid},
        max_attempts=5,
    )


async def _setup_message(r, envelope):
    """Publish an envelope to the inbound stream and consume it, returning msg_id."""
    await publish(r, _STREAM_IN, envelope)
    msgs = await consume(r, _STREAM_IN, _GROUP, "test-consumer", block=0)
    assert len(msgs) == 1
    return msgs[0][0]


def _mock_coordinator(result):
    """Create an AsyncMock WaterfallCoordinator returning the given WaterfallResult."""
    mock = AsyncMock(spec=WaterfallCoordinator)
    mock.fetch_match.return_value = result
    return mock


async def _call_fetch_match(r, cfg, msg_id, env, log, coordinator):
    """Call _fetch_match with the coordinator injected.

    The updated _fetch_match() signature accepts ``coordinator`` as a keyword
    argument. RiotClient and RawStore are still passed for timeline fetch and
    other non-waterfall paths.
    """
    raw_store = RawStore(r)
    riot = RiotClient("RGAPI-test")
    try:
        await _fetch_match(
            r, riot, raw_store, cfg, msg_id, env, log, coordinator=coordinator
        )
    finally:
        await riot.close()


# ---------------------------------------------------------------------------
# Test 1: cached status
# ---------------------------------------------------------------------------


class TestFetchMatchCached:
    async def test_fetch_match_cached__publishes_and_acks(self, r, cfg, log):
        """Coordinator returns status='cached' -> _publish_and_ack called, no store."""
        env = _match_envelope()
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(WaterfallResult(status="cached"))

        await _call_fetch_match(r, cfg, msg_id, env, log, coordinator)

        # Message published to stream:parse
        assert await r.xlen(_STREAM_OUT) == 1
        # Message ACKed (no longer pending)
        pending = await r.xpending(_STREAM_IN, _GROUP)
        assert pending["pending"] == 0


# ---------------------------------------------------------------------------
# Test 2: success status
# ---------------------------------------------------------------------------


class TestFetchMatchSuccess:
    async def test_fetch_match_success__stores_and_publishes(self, r, cfg, log):
        """Coordinator returns status='success' with data -> data published downstream.

        The coordinator already stored the data to raw_store, so _fetch_match
        should call _write_seen_match, _fetch_timeline_if_needed, _publish_and_ack.
        """
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        match_data = {"info": {"gameDuration": 1800}, "metadata": {"matchId": "NA1_W001"}}
        coordinator = _mock_coordinator(
            WaterfallResult(status="success", data=match_data, source="riot")
        )

        await _call_fetch_match(r, cfg, msg_id, env, log, coordinator)

        # Message published to stream:parse
        assert await r.xlen(_STREAM_OUT) == 1
        # Message ACKed
        pending = await r.xpending(_STREAM_IN, _GROUP)
        assert pending["pending"] == 0
        # seen:matches updated
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        assert await r.sismember(f"seen:matches:{today}", "NA1_W001")
        # match status set to fetched
        assert await r.hget("match:NA1_W001", "status") == "fetched"


# ---------------------------------------------------------------------------
# Test 3: not_found status
# ---------------------------------------------------------------------------


class TestFetchMatchNotFound:
    async def test_fetch_match_not_found__acks_without_publish(self, r, cfg, log):
        """Coordinator returns status='not_found' -> message ACKed, nothing published."""
        env = _match_envelope()
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(WaterfallResult(status="not_found"))

        await _call_fetch_match(r, cfg, msg_id, env, log, coordinator)

        # Nothing published to stream:parse
        assert await r.xlen(_STREAM_OUT) == 0
        # Message ACKed
        pending = await r.xpending(_STREAM_IN, _GROUP)
        assert pending["pending"] == 0
        # match status set to not_found
        assert await r.hget("match:NA1_W001", "status") == "not_found"


# ---------------------------------------------------------------------------
# Test 4: auth_error status
# ---------------------------------------------------------------------------


class TestFetchMatchAuthError:
    async def test_fetch_match_auth_error__sets_halted(self, r, cfg, log):
        """Coordinator returns status='auth_error' -> system:halted set."""
        env = _match_envelope()
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(WaterfallResult(status="auth_error"))

        await _call_fetch_match(r, cfg, msg_id, env, log, coordinator)

        assert await r.get("system:halted") == "1"
        # Nothing published to stream:parse
        assert await r.xlen(_STREAM_OUT) == 0


# ---------------------------------------------------------------------------
# Test 5: all_exhausted status
# ---------------------------------------------------------------------------


class TestFetchMatchAllExhausted:
    async def test_fetch_match_all_exhausted__nacks_to_dlq(self, r, cfg, log):
        """Coordinator returns status='all_exhausted' -> nack_to_dlq called."""
        env = _match_envelope()
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(WaterfallResult(status="all_exhausted"))

        await _call_fetch_match(r, cfg, msg_id, env, log, coordinator)

        # DLQ entry created
        assert await r.xlen(_STREAM_DLQ) == 1
        # Nothing published to stream:parse
        assert await r.xlen(_STREAM_OUT) == 0

    # Test 6: retry_after_ms propagation
    async def test_fetch_match_all_exhausted__propagates_retry_after_ms(self, r, cfg, log):
        """retry_after_ms=5000 in result -> nack_to_dlq receives it."""
        env = _match_envelope(match_id="NA1_W002")
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(
            WaterfallResult(status="all_exhausted", retry_after_ms=5000)
        )

        await _call_fetch_match(r, cfg, msg_id, env, log, coordinator)

        # DLQ entry created with retry_after_ms propagated
        entries = await r.xrange(_STREAM_DLQ)
        assert len(entries) == 1
        fields = entries[0][1]
        assert fields.get("retry_after_ms") == "5000"


# ---------------------------------------------------------------------------
# Test 7: blob_validation_failed -> immediate DLQ
# ---------------------------------------------------------------------------


class TestFetchMatchBlobValidationFailed:
    async def test_fetch_match_blob_validation_failed__nacks_immediately(self, r, cfg, log):
        """status='all_exhausted' + blob_validation_failed=True -> nack_to_dlq with
        max_attempts=1 (or equivalent immediate-archive flag) so the message does not
        burn retry cycles against a structurally bad blob.
        """
        env = _match_envelope(match_id="NA1_W003")
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(
            WaterfallResult(status="all_exhausted", blob_validation_failed=True)
        )

        await _call_fetch_match(r, cfg, msg_id, env, log, coordinator)

        # DLQ entry created
        entries = await r.xrange(_STREAM_DLQ)
        assert len(entries) == 1
        fields = entries[0][1]
        # The failure_code should indicate blob validation failure
        assert "blob_validation" in fields.get("failure_code", "")
        # max_attempts forced to 1 to prevent retry cycles -- the design spec says
        # "nack_to_dlq with max_attempts=1 (or equivalent immediate-archive flag)"
        assert fields.get("max_attempts") == "1"


# ---------------------------------------------------------------------------
# Test 8: system:halted pre-check
# ---------------------------------------------------------------------------


class TestFetchMatchSystemHalted:
    async def test_fetch_match_skips_when_system_halted(self, r, cfg, log):
        """system:halted is set -> fetch skipped entirely, coordinator never called."""
        await r.set("system:halted", "1")
        env = _match_envelope()
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(WaterfallResult(status="success"))

        await _call_fetch_match(r, cfg, msg_id, env, log, coordinator)

        # Coordinator was never called
        coordinator.fetch_match.assert_not_called()
        # Nothing published
        assert await r.xlen(_STREAM_OUT) == 0


# ---------------------------------------------------------------------------
# Test 9: FetchContext built correctly from envelope
# ---------------------------------------------------------------------------


class TestFetchContextFromEnvelope:
    async def test_fetch_match_builds_fetch_context_from_envelope(self, r, cfg, log):
        """Verify FetchContext is built with correct match_id, puuid, region from envelope."""
        env = _match_envelope(match_id="NA1_CTX", region="euw1", puuid="ctx-puuid-42")
        msg_id = await _setup_message(r, env)

        coordinator = _mock_coordinator(
            WaterfallResult(
                status="success",
                data={"info": {"gameDuration": 900}},
                source="riot",
            )
        )

        await _call_fetch_match(r, cfg, msg_id, env, log, coordinator)

        # Verify the FetchContext passed to coordinator.fetch_match
        coordinator.fetch_match.assert_called_once()
        call_args = coordinator.fetch_match.call_args
        context = call_args[0][0] if call_args[0] else call_args[1].get("context")
        assert isinstance(context, FetchContext)
        assert context.match_id == "NA1_CTX"
        assert context.region == "euw1"
        assert context.puuid == "ctx-puuid-42"


# ---------------------------------------------------------------------------
# Test 10: timeline fetch still called after waterfall success
# ---------------------------------------------------------------------------


class TestTimelineFetchUnchanged:
    async def test_timeline_fetch_unchanged(self, r, cfg, log):
        """After a successful match fetch via coordinator, timeline fetch still called.

        The existing timeline code path (_fetch_timeline_if_needed) is preserved
        and runs after a successful waterfall result, using the Riot API directly.
        """
        cfg.fetch_timeline = True
        env = _match_envelope(match_id="NA1_TL01", region="na1")
        msg_id = await _setup_message(r, env)

        coordinator = _mock_coordinator(
            WaterfallResult(
                status="success",
                data={"info": {"gameDuration": 1800}},
                source="riot",
            )
        )

        mock_timeline_fn = AsyncMock()

        with patch("lol_fetcher.main._fetch_timeline_if_needed", mock_timeline_fn):
            raw_store = RawStore(r)
            riot = RiotClient("RGAPI-test")
            try:
                await _fetch_match(
                    r, riot, raw_store, cfg, msg_id, env, log, coordinator=coordinator
                )
            finally:
                await riot.close()

        # _fetch_timeline_if_needed was called
        mock_timeline_fn.assert_called_once()
        # Verify it received the correct match_id and region in its arguments
        call_args = mock_timeline_fn.call_args
        call_positional = call_args[0] if call_args[0] else ()
        call_keyword = call_args[1] if call_args[1] else {}
        all_args = list(call_positional) + list(call_keyword.values())
        all_str_args = [str(a) for a in all_args]
        assert any("NA1_TL01" in a for a in all_str_args), (
            f"Expected match_id 'NA1_TL01' in timeline call args, got: {all_str_args}"
        )
        assert any("na1" in a for a in all_str_args), (
            f"Expected region 'na1' in timeline call args, got: {all_str_args}"
        )
