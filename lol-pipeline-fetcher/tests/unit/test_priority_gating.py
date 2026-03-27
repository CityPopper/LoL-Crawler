"""FP-2: Unit tests for fetcher priority gating."""

from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import PRIORITY_MANUAL_20, PRIORITY_MANUAL_20PLUS
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.sources.base import WaterfallResult
from lol_pipeline.sources.coordinator import WaterfallCoordinator
from lol_pipeline.streams import consume, publish

from lol_fetcher.main import _fetch_match

_STREAM_IN = "stream:match_id"
_STREAM_OUT = "stream:parse"
_GROUP = "fetchers"

try:
    import lupa  # noqa: F401

    _LUPA_AVAILABLE = True
except ImportError:
    _LUPA_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _LUPA_AVAILABLE, reason="lupa required for rate limiter Lua scripts"
)


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
    return logging.getLogger("test-fetcher-priority")


@pytest.fixture(autouse=True)
def _reset_priority_cache():
    """Reset the module-level priority cache between tests."""
    import lol_fetcher.main as mod

    mod._priority_cache = (False, 0.0)
    yield
    mod._priority_cache = (False, 0.0)


def _match_envelope(match_id="NA1_90001", region="na1", priority="normal"):
    return MessageEnvelope(
        source_stream=_STREAM_IN,
        type="match_id",
        payload={"match_id": match_id, "region": region, "puuid": "test-puuid-pg"},
        max_attempts=5,
        priority=priority,
    )


async def _setup_message(r, envelope):
    await publish(r, _STREAM_IN, envelope)
    msgs = await consume(r, _STREAM_IN, _GROUP, "test-consumer", block=0)
    assert len(msgs) == 1
    return msgs[0][0]


def _mock_coordinator(result):
    mock = AsyncMock(spec=WaterfallCoordinator)
    mock.fetch_match.return_value = result
    return mock


class TestPriorityGatingLowPriorityDeferred:
    """Low-priority + priority active -> defer_message called, no fetch."""

    @pytest.mark.asyncio
    async def test_low_priority__priority_active__defers(self, r, cfg, log):
        env = _match_envelope(priority="normal")
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(
            WaterfallResult(status="success", data={"info": {}}, source="riot")
        )

        with patch(
            "lol_fetcher.main.has_priority_players",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "lol_fetcher.main.defer_message",
            new_callable=AsyncMock,
        ) as mock_defer:
            raw_store = RawStore(r)
            riot = RiotClient("RGAPI-test")
            try:
                await _fetch_match(
                    r, riot, raw_store, cfg, msg_id, env, log, coordinator=coordinator
                )
            finally:
                await riot.close()

        mock_defer.assert_called_once()
        call_args = mock_defer.call_args
        assert call_args[0][1] == msg_id  # msg_id
        assert call_args[0][2] is env  # envelope
        assert call_args[0][3] == _STREAM_IN  # stream
        assert call_args[0][4] == _GROUP  # group
        assert call_args[1]["envelope_ttl"] == cfg.delay_envelope_ttl_seconds
        coordinator.fetch_match.assert_not_called()


class TestPriorityGatingLowPriorityNoActiveProceeds:
    """Low-priority + no priority active -> fetch proceeds normally."""

    @pytest.mark.asyncio
    async def test_low_priority__no_priority_active__proceeds(self, r, cfg, log):
        env = _match_envelope(priority="normal")
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(
            WaterfallResult(status="success", data={"info": {}}, source="riot")
        )

        with patch(
            "lol_fetcher.main.has_priority_players",
            new_callable=AsyncMock,
            return_value=False,
        ), patch(
            "lol_fetcher.main.defer_message",
            new_callable=AsyncMock,
        ) as mock_defer:
            raw_store = RawStore(r)
            riot = RiotClient("RGAPI-test")
            try:
                await _fetch_match(
                    r, riot, raw_store, cfg, msg_id, env, log, coordinator=coordinator
                )
            finally:
                await riot.close()

        mock_defer.assert_not_called()
        coordinator.fetch_match.assert_called_once()


class TestPriorityGatingManual20NotDeferred:
    """PRIORITY_MANUAL_20 + priority active -> fetch proceeds (not deferred)."""

    @pytest.mark.asyncio
    async def test_manual_20__priority_active__proceeds(self, r, cfg, log):
        env = _match_envelope(priority=PRIORITY_MANUAL_20)
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(
            WaterfallResult(status="success", data={"info": {}}, source="riot")
        )

        with patch(
            "lol_fetcher.main.has_priority_players",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "lol_fetcher.main.defer_message",
            new_callable=AsyncMock,
        ) as mock_defer:
            raw_store = RawStore(r)
            riot = RiotClient("RGAPI-test")
            try:
                await _fetch_match(
                    r, riot, raw_store, cfg, msg_id, env, log, coordinator=coordinator
                )
            finally:
                await riot.close()

        mock_defer.assert_not_called()
        coordinator.fetch_match.assert_called_once()


class TestPriorityGatingManual20PlusNotDeferred:
    """PRIORITY_MANUAL_20PLUS + priority active -> fetch proceeds (not deferred)."""

    @pytest.mark.asyncio
    async def test_manual_20plus__priority_active__proceeds(self, r, cfg, log):
        env = _match_envelope(priority=PRIORITY_MANUAL_20PLUS)
        msg_id = await _setup_message(r, env)
        coordinator = _mock_coordinator(
            WaterfallResult(status="success", data={"info": {}}, source="riot")
        )

        with patch(
            "lol_fetcher.main.has_priority_players",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "lol_fetcher.main.defer_message",
            new_callable=AsyncMock,
        ) as mock_defer:
            raw_store = RawStore(r)
            riot = RiotClient("RGAPI-test")
            try:
                await _fetch_match(
                    r, riot, raw_store, cfg, msg_id, env, log, coordinator=coordinator
                )
            finally:
                await riot.close()

        mock_defer.assert_not_called()
        coordinator.fetch_match.assert_called_once()


class TestPriorityCacheReusesResult:
    """Cache: second call within 2s reuses cached result."""

    @pytest.mark.asyncio
    async def test_cache__second_call__reuses_result(self, r, cfg, log):
        from lol_fetcher.main import _has_priority_cached

        mock_has = AsyncMock(return_value=True)

        with patch("lol_fetcher.main.has_priority_players", mock_has):
            result1 = await _has_priority_cached(r)
            result2 = await _has_priority_cached(r)

        assert result1 is True
        assert result2 is True
        # has_priority_players should be called only once (cached on second call)
        mock_has.assert_called_once()


class TestPriorityCacheExpiryBoundary:
    """Cache expires after 2s — advancing monotonic by 3s forces a refresh."""

    @pytest.mark.asyncio
    async def test_cache__expired_after_2s__calls_has_priority_again(self, r, cfg, log):
        from lol_fetcher.main import _has_priority_cached

        mock_has = AsyncMock(return_value=True)
        base_time = 1000.0

        with (
            patch("lol_fetcher.main.has_priority_players", mock_has),
            patch("lol_fetcher.main.time") as mock_time,
        ):
            # First call: monotonic returns base_time; cache sets expiry at base+2
            mock_time.monotonic.return_value = base_time
            await _has_priority_cached(r)
            assert mock_has.call_count == 1

            # Second call: 3s later — past the 2s TTL
            mock_time.monotonic.return_value = base_time + 3.0
            await _has_priority_cached(r)
            assert mock_has.call_count == 2
