"""Unit tests for lol_fetcher.main — Phase 03 ACs 03-21 through 03-28."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import httpx
import pytest
import respx
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.opgg_client import OpggClient
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.streams import consume, publish

from lol_fetcher.main import _build_coordinator, _fetch_match, main

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
    return logging.getLogger("test-fetcher")


def _match_envelope(match_id="NA1_123", region="na1"):
    return MessageEnvelope(
        source_stream=_STREAM_IN,
        type="match_id",
        payload={"match_id": match_id, "region": region, "puuid": "test-puuid-0001"},
        max_attempts=5,
    )


async def _setup_message(r, envelope):
    await publish(r, _STREAM_IN, envelope)
    msgs = await consume(r, _STREAM_IN, _GROUP, "test-consumer", block=0)
    assert len(msgs) == 1
    return msgs[0][0]


def _match_url(match_id="NA1_123"):
    return f"https://americas.api.riotgames.com/lol/match/v5/matches/{match_id}"


class TestFetchIdempotent:
    @pytest.mark.asyncio
    async def test_raw_blob_exists_skips_api(self, r, cfg, log):
        """AC-03-21: raw blob exists → 0 API calls; publishes to stream:parse; ACKs."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)
        await raw_store.set("NA1_123", '{"info":{}}')

        with respx.mock:
            # No routes — any HTTP call would raise
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 1


class TestFetchSuccess:
    @pytest.mark.asyncio
    async def test_successful_fetch(self, r, cfg, log):
        """AC-03-22: raw blob missing; 200 → blob in RawStore; status=fetched; stream:parse."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(
                    200, json={"info": {"gameDuration": 1800}, "metadata": {}}
                )
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        assert await raw_store.exists("NA1_123") is True
        assert await r.hget("match:NA1_123", "status") == "fetched"
        assert await r.xlen(_STREAM_OUT) == 1


class TestFetchErrors:
    @pytest.mark.asyncio
    async def test_404_sets_not_found(self, r, cfg, log):
        """AC-03-23: 404 → status=not_found; ACK; 0 messages in stream:parse."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        assert await r.hget("match:NA1_123", "status") == "not_found"
        assert await r.xlen(_STREAM_OUT) == 0

    @pytest.mark.asyncio
    async def test_429_routes_to_delayed(self, r, cfg, log):
        """AC-03-24: 429 + Retry-After:30 → delayed:messages entry; ACK."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(429, headers={"Retry-After": "30"})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        # Should be in DLQ stream with http_429 code
        dlq_len = await r.xlen("stream:dlq")
        assert dlq_len == 1
        assert await r.xlen(_STREAM_OUT) == 0

    @pytest.mark.asyncio
    async def test_500_nacks_to_dlq(self, r, cfg, log):
        """AC-03-25: 500 → nack_to_dlq with failure_code=http_5xx."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(500, text="Internal Server Error")
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        dlq_len = await r.xlen("stream:dlq")
        assert dlq_len == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["failure_code"] == "http_5xx"

    @pytest.mark.asyncio
    async def test_403_halts_system(self, r, cfg, log):
        """AC-03-27: 403 → system:halted='1'; no ACK; 0 in stream:parse."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(return_value=httpx.Response(403))
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        assert await r.get("system:halted") == "1"
        assert await r.xlen(_STREAM_OUT) == 0

    @pytest.mark.asyncio
    async def test_system_halted_skips(self, r, cfg, log):
        """AC-03-27b (implied): system:halted → exits immediately."""
        await r.set("system:halted", "1")
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 0


class TestFetchMaxAttempts:
    @pytest.mark.asyncio
    async def test_at_max_attempts_dlq_envelope(self, r, cfg, log):
        """AC-03-28: attempts=MAX_ATTEMPTS → entry in stream:dlq with full fields."""
        raw_store = RawStore(r)
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="match_id",
            payload={"match_id": "NA1_123", "region": "na1", "puuid": "test-puuid-0001"},
            max_attempts=5,
            attempts=5,
        )
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(return_value=httpx.Response(500, text="Server Error"))
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        entries = await r.xrange("stream:dlq")
        assert len(entries) == 1
        fields = entries[0][1]
        assert fields["failure_code"] == "http_5xx"
        assert fields["failed_by"] == "fetcher"
        assert "original_message_id" in fields
        assert "payload" in fields


class TestFetchRateLimiterTimeout:
    """WATERFALL-5: All sources throttled → coordinator returns all_exhausted → DLQ."""

    @pytest.mark.asyncio
    async def test_all_sources_throttled__routes_to_dlq(self, r, cfg, log):
        """All sources throttled → coordinator returns all_exhausted → nack_to_dlq."""
        from lol_pipeline.sources.base import WaterfallResult

        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        coordinator = AsyncMock()
        coordinator.fetch_match.return_value = WaterfallResult(
            status="all_exhausted", retry_after_ms=5000
        )

        riot = RiotClient("RGAPI-test")
        await _fetch_match(
            r, riot, raw_store, cfg, msg_id, env, log, coordinator=coordinator
        )
        await riot.close()

        # No output published
        assert await r.xlen(_STREAM_OUT) == 0
        # DLQ entry created with retry_after_ms
        assert await r.xlen("stream:dlq") == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1].get("retry_after_ms") == "5000"
        # No match status set
        assert await r.hget("match:NA1_123", "status") is None


class TestFetchNotFoundTTL:
    """P15-HORIZON: 404 response sets TTL on match:{match_id}."""

    @pytest.mark.asyncio
    async def test_404_sets_ttl_on_match_key(self, r, cfg, log):
        """NotFoundError (404) → match:{match_id} has a TTL set."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        assert await r.hget("match:NA1_123", "status") == "not_found"
        ttl = await r.ttl("match:NA1_123")
        assert ttl > 0, "match:{match_id} must have a TTL after 404"
        assert abs(ttl - 604800) <= 60


class TestFetchMatchTTL:
    """P14-CR-1: After storing match data, set TTL on match:{match_id}."""

    @pytest.mark.asyncio
    async def test_successful_fetch_sets_match_ttl(self, r, cfg, log):
        """After fetch+store, match:{match_id} has a TTL set."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}, "metadata": {}})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        ttl = await r.ttl("match:NA1_123")
        assert ttl > 0, "match:{match_id} must have a TTL after successful fetch"
        # Default is 604800 (7 days)
        assert abs(ttl - 604800) <= 60


class TestFetchStoreErrors:
    """Tests for failure modes during fetch and store."""

    @pytest.mark.asyncio
    async def test_raw_store_set_fails__does_not_publish(self, r, cfg, log):
        """If RawStore.set raises, no message is published to stream:parse."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}, "metadata": {}})
            )

            async def failing_set(*args, **kwargs):
                raise OSError("disk full")

            raw_store.set = failing_set
            riot = RiotClient("RGAPI-test")
            with pytest.raises(OSError):
                await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 0
        assert await r.hget("match:NA1_123", "status") is None

    @pytest.mark.asyncio
    async def test_publish_fails_after_store__redelivery_idempotent(self, r, cfg, log):
        """If publish fails after store, redelivery finds blob and re-publishes."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}, "metadata": {}})
            )
            riot = RiotClient("RGAPI-test")

            call_count = 0
            original_xadd = r.xadd

            async def xadd_fail_once(stream, fields, *args, **kwargs):
                nonlocal call_count
                call_count += 1
                # Fail the first XADD to stream:parse (publish after store)
                # but allow XADD to stream:match_id (test setup)
                if stream == _STREAM_OUT and call_count <= 2:
                    raise Exception("connection lost")
                return await original_xadd(stream, fields, *args, **kwargs)

            r.xadd = xadd_fail_once
            with pytest.raises(Exception, match="connection lost"):
                await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)

            # Raw blob was stored despite publish failure
            assert await raw_store.exists("NA1_123") is True

            # Simulate redelivery: same message, same msg_id
            r.xadd = original_xadd
            env2 = _match_envelope()
            msg_id2 = await _setup_message(r, env2)
            await _fetch_match(r, riot, raw_store, cfg, msg_id2, env2, log)
            await riot.close()

        # Idempotent path detected existing blob and published
        assert await r.xlen(_STREAM_OUT) == 1


class TestMainEntryPoint:
    """Tests for main() bootstrap and teardown."""

    @pytest.mark.asyncio
    async def test_main__creates_redis_and_starts_consumer(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        mock_consumer = AsyncMock()
        with (
            patch("lol_fetcher.main.Config") as mock_cfg,
            patch("lol_fetcher.main.get_redis", return_value=mock_r),
            patch("lol_fetcher.main.RiotClient") as mock_riot,
            patch("lol_fetcher.main.RawStore"),
            patch("lol_fetcher.main.run_consumer", mock_consumer),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            await main()
        mock_consumer.assert_called_once()
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__keyboard_interrupt__closes_redis(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        with (
            patch("lol_fetcher.main.Config") as mock_cfg,
            patch("lol_fetcher.main.get_redis", return_value=mock_r),
            patch("lol_fetcher.main.RiotClient") as mock_riot,
            patch("lol_fetcher.main.RawStore"),
            patch("lol_fetcher.main.run_consumer", side_effect=KeyboardInterrupt),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()


class TestTimelineFetch:
    """Timeline fetching when cfg.fetch_timeline is enabled."""

    @pytest.mark.asyncio
    async def test_timeline_fetched_when_enabled(self, r, cfg, log):
        """When fetch_timeline=True, timeline is fetched and stored in raw:timeline:{id}."""
        cfg.fetch_timeline = True
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}, "metadata": {}})
            )
            respx.get(_match_url() + "/timeline").mock(
                return_value=httpx.Response(200, json={"info": {"frames": []}})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        # Timeline stored
        timeline_raw = await r.get("raw:timeline:NA1_123")
        assert timeline_raw is not None
        assert "frames" in timeline_raw
        # TTL set
        ttl = await r.ttl("raw:timeline:NA1_123")
        assert ttl > 0

    @pytest.mark.asyncio
    async def test_timeline_skipped_when_disabled(self, r, cfg, log):
        """When fetch_timeline=False (default), no timeline API call is made."""
        cfg.fetch_timeline = False
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}, "metadata": {}})
            )
            timeline_route = respx.get(_match_url() + "/timeline").mock(
                return_value=httpx.Response(200, json={"info": {"frames": []}})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        assert not timeline_route.called
        assert await r.get("raw:timeline:NA1_123") is None

    @pytest.mark.asyncio
    async def test_timeline_failure_non_fatal(self, r, cfg, log):
        """Timeline fetch failure does not prevent match data from being stored."""
        cfg.fetch_timeline = True
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}, "metadata": {}})
            )
            respx.get(_match_url() + "/timeline").mock(
                return_value=httpx.Response(500, text="Server Error")
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        # Main match data still stored and published
        assert await raw_store.exists("NA1_123") is True
        assert await r.xlen(_STREAM_OUT) == 1
        # Timeline not stored
        assert await r.get("raw:timeline:NA1_123") is None


class TestSeenMatches:
    """Global dedup: fetcher adds match_id to daily-bucketed seen:matches:{today} SET."""

    @pytest.mark.asyncio
    async def test_fetcher_adds_to_seen_set(self, r, cfg, log):
        """After successful fetch, match_id is added to seen:matches:{today}."""
        from datetime import UTC, datetime

        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}, "metadata": {}})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        seen_key = f"seen:matches:{today}"
        assert await r.sismember(seen_key, "NA1_123")
        ttl = await r.ttl(seen_key)
        assert ttl > 0

    @pytest.mark.asyncio
    async def test_seen_set_not_written_on_error(self, r, cfg, log):
        """When fetch fails (404), match_id is NOT added to seen:matches:{today}."""
        from datetime import UTC, datetime

        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        seen_key = f"seen:matches:{today}"
        assert not await r.sismember(seen_key, "NA1_123")


class TestCorrelationIdPropagation:
    """Outbound envelopes must propagate correlation_id from inbound."""

    @pytest.mark.asyncio
    async def test_fetch__propagates_correlation_id__success_path(self, r, cfg, log):
        """Successful fetch publishes to stream:parse with same correlation_id."""
        raw_store = RawStore(r)
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="match_id",
            payload={"match_id": "NA1_COR", "region": "na1", "puuid": "p1"},
            max_attempts=5,
            correlation_id="trace-abc-123",
        )
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url("NA1_COR")).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}, "metadata": {}})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        out_msgs = await consume(r, _STREAM_OUT, "test-group", "tc", block=0)
        assert len(out_msgs) == 1
        assert out_msgs[0][1].correlation_id == "trace-abc-123"

    @pytest.mark.asyncio
    async def test_fetch__propagates_correlation_id__idempotent_path(self, r, cfg, log):
        """Idempotent re-delivery (blob exists) still propagates correlation_id."""
        raw_store = RawStore(r)
        await raw_store.set("NA1_COR2", '{"info":{}}')
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="match_id",
            payload={"match_id": "NA1_COR2", "region": "na1", "puuid": "p1"},
            max_attempts=5,
            correlation_id="trace-xyz-789",
        )
        msg_id = await _setup_message(r, env)

        with respx.mock:
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        out_msgs = await consume(r, _STREAM_OUT, "test-group2", "tc", block=0)
        assert len(out_msgs) == 1
        assert out_msgs[0][1].correlation_id == "trace-xyz-789"


class TestSeenMatchesTTLNotReset:
    """R2: seen:matches TTL is only set when no TTL exists (ttl < 0)."""

    @pytest.mark.asyncio
    async def test_seen_matches_ttl__not_reset_on_second_fetch(self, r, cfg, log):
        """When seen:matches:{today} already has a TTL, a second fetch does not reset it."""
        raw_store = RawStore(r)
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        seen_key = f"seen:matches:{today}"

        # First fetch — sets TTL on seen:matches:{today}
        env1 = _match_envelope(match_id="NA1_FIRST")
        msg_id1 = await _setup_message(r, env1)
        with respx.mock:
            respx.get(_match_url("NA1_FIRST")).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 900}, "metadata": {}})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id1, env1, log)
            await riot.close()

        ttl_after_first = await r.ttl(seen_key)
        assert ttl_after_first > 0

        # Simulate time passing by manually lowering TTL
        await r.expire(seen_key, 1000)
        ttl_lowered = await r.ttl(seen_key)
        assert ttl_lowered <= 1000

        # Second fetch — should NOT reset TTL (guard: ttl < 0 only)
        env2 = _match_envelope(match_id="NA1_SECOND")
        msg_id2 = await _setup_message(r, env2)
        with respx.mock:
            respx.get(_match_url("NA1_SECOND")).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1200}, "metadata": {}})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id2, env2, log)
            await riot.close()

        ttl_after_second = await r.ttl(seen_key)
        # TTL should NOT have been reset to the full value
        assert ttl_after_second <= 1000

    @pytest.mark.asyncio
    async def test_seen_matches_ttl__expire_not_called_when_ttl_exists(self, r, cfg, log):
        """TCG-3: seen:matches TTL already set — r.expire() NOT called again.

        The fetcher checks `ttl < 0` before calling expire. When a TTL already
        exists (ttl >= 0), the false-branch skips the expire call entirely.
        This test instruments r.expire to verify the call is not made for
        seen:matches:{today} on the second fetch.
        """
        raw_store = RawStore(r)
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        seen_key = f"seen:matches:{today}"

        # First fetch — sets TTL on seen:matches:{today} (no TTL exists yet, ttl == -1)
        env1 = _match_envelope(match_id="NA1_TTL_A")
        msg_id1 = await _setup_message(r, env1)
        with respx.mock:
            respx.get(_match_url("NA1_TTL_A")).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 900}, "metadata": {}})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id1, env1, log)
            await riot.close()

        assert await r.ttl(seen_key) > 0  # TTL is now set

        # Instrument r.expire to track calls for the daily seen:matches key
        seen_expire_calls: list[str] = []
        original_expire = r.expire

        async def tracking_expire(name, *args, **kwargs):
            if name == seen_key:
                seen_expire_calls.append(name)
            return await original_expire(name, *args, **kwargs)

        r.expire = tracking_expire

        # Second fetch — TTL already exists (>= 0), so expire should NOT be called
        env2 = _match_envelope(match_id="NA1_TTL_B")
        msg_id2 = await _setup_message(r, env2)
        with respx.mock:
            respx.get(_match_url("NA1_TTL_B")).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1200}, "metadata": {}})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id2, env2, log)
            await riot.close()

        # expire("seen:matches", ...) must NOT have been called
        assert len(seen_expire_calls) == 0, (
            f"Expected 0 expire calls for seen:matches, got {len(seen_expire_calls)}"
        )


class TestOpggIntegration:
    """WATERFALL-5: With opgg_enabled, coordinator registers OpggSource as a fallback.

    In the waterfall architecture:
    - OpggSource returns UNAVAILABLE (cannot fetch by match_id), so Riot is tried next.
    - All data is stored under raw:match: (no per-source key prefix).
    - OpggSource failures never set system:halted (only Riot primary errors do).
    """

    @pytest.fixture
    def opgg_cfg(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("OPGG_ENABLED", "true")
        return Config(_env_file=None)  # type: ignore[call-arg]

    @pytest.mark.asyncio
    async def test_opgg_enabled__coordinator_includes_opgg_source(self, r, opgg_cfg, log):
        """When opgg_enabled=True, coordinator includes OpggSource; data stored via raw:match:."""
        from lol_pipeline.sources.base import WaterfallResult

        raw_store = RawStore(r)
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="match_id",
            payload={
                "match_id": "NA1_12345",
                "region": "na1",
                "puuid": "test-puuid-0001",
            },
            max_attempts=5,
        )
        msg_id = await _setup_message(r, env)

        match_data = {"info": {"gameDuration": 1800}, "metadata": {"matchId": "NA1_12345"}}
        coordinator = AsyncMock()
        coordinator.fetch_match.return_value = WaterfallResult(
            status="success", data=match_data, source="opgg"
        )

        riot = RiotClient("RGAPI-test")
        await _fetch_match(
            r, riot, raw_store, opgg_cfg, msg_id, env, log, coordinator=coordinator
        )
        await riot.close()

        assert await r.xlen(_STREAM_OUT) == 1

    @pytest.mark.asyncio
    async def test_opgg_fails__falls_through_to_riot(self, r, opgg_cfg, log):
        """When op.gg is unavailable, coordinator falls through to Riot API."""
        raw_store = RawStore(r)
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="match_id",
            payload={
                "match_id": "NA1_RIOT_FALLBACK",
                "region": "na1",
                "puuid": "test-puuid-0001",
            },
            max_attempts=5,
        )
        msg_id = await _setup_message(r, env)

        mock_opgg = AsyncMock(spec=OpggClient)

        with respx.mock:
            respx.get(_match_url("NA1_RIOT_FALLBACK")).mock(
                return_value=httpx.Response(
                    200, json={"info": {"gameDuration": 1800}, "metadata": {}}
                )
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(
                r, riot, raw_store, opgg_cfg, msg_id, env, log, opgg=mock_opgg
            )
            await riot.close()

        assert await raw_store.exists("NA1_RIOT_FALLBACK") is True
        assert await r.xlen(_STREAM_OUT) == 1

    @pytest.mark.asyncio
    async def test_opgg_failure__never_sets_system_halted(self, r, opgg_cfg, log):
        """Op.gg failure NEVER sets system:halted (only Riot primary 403 does)."""
        raw_store = RawStore(r)
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="match_id",
            payload={
                "match_id": "NA1_HALT_TEST",
                "region": "na1",
                "puuid": "test-puuid-0001",
            },
            max_attempts=5,
        )
        msg_id = await _setup_message(r, env)

        mock_opgg = AsyncMock(spec=OpggClient)

        with respx.mock:
            respx.get(_match_url("NA1_HALT_TEST")).mock(
                return_value=httpx.Response(
                    200, json={"info": {"gameDuration": 900}, "metadata": {}}
                )
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(
                r, riot, raw_store, opgg_cfg, msg_id, env, log, opgg=mock_opgg
            )
            await riot.close()

        assert await r.get("system:halted") is None

    @pytest.mark.asyncio
    async def test_opgg_data__stored_in_canonical_raw_store(self, r, opgg_cfg, log):
        """WATERFALL-5: All data stored under raw:match: regardless of source origin."""
        from lol_pipeline.sources.base import WaterfallResult

        raw_store = RawStore(r)
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="match_id",
            payload={
                "match_id": "NA1_99999",
                "region": "na1",
                "puuid": "test-puuid-0001",
            },
            max_attempts=5,
        )
        msg_id = await _setup_message(r, env)

        match_data = {"info": {"gameDuration": 1800}, "metadata": {"matchId": "NA1_99999"}}
        coordinator = AsyncMock()
        coordinator.fetch_match.return_value = WaterfallResult(
            status="success", data=match_data, source="opgg"
        )

        riot = RiotClient("RGAPI-test")
        await _fetch_match(
            r, riot, raw_store, opgg_cfg, msg_id, env, log, coordinator=coordinator
        )
        await riot.close()

        # In waterfall architecture, data is stored in canonical raw:match: regardless of source
        assert await r.xlen(_STREAM_OUT) == 1


class TestBuildCoordinatorSourceInit:
    """IMP-074: _build_coordinator logs warning when a source raises during init."""

    def test_opgg_init_failure__logs_warning_and_builds(self, cfg, log):
        """If OpggSource raises during init, coordinator still builds with warning."""
        riot = RiotClient("RGAPI-test")
        cfg.opgg_enabled = True
        opgg = OpggClient.__new__(OpggClient)

        fetcher_log = logging.getLogger("fetcher")
        handler = logging.handlers.MemoryHandler(capacity=100)
        fetcher_log.addHandler(handler)
        try:
            with patch(
                "lol_fetcher.main.OpggSource",
                side_effect=RuntimeError("opgg init boom"),
            ):
                coordinator = _build_coordinator(riot, RawStore(None), cfg, opgg)

            assert coordinator is not None
            messages = [r.getMessage() for r in handler.buffer]
            assert any("unavailable at startup" in m for m in messages)
        finally:
            fetcher_log.removeHandler(handler)

    def test_blob_store_init_failure__logs_warning(self, cfg, log):
        """If BlobStore raises during init, coordinator still builds with warning."""
        riot = RiotClient("RGAPI-test")
        cfg.blob_data_dir = "/some/bad/path"

        fetcher_log = logging.getLogger("fetcher")
        handler = logging.handlers.MemoryHandler(capacity=100)
        fetcher_log.addHandler(handler)
        try:
            with patch(
                "lol_fetcher.main.BlobStore",
                side_effect=OSError("bad path"),
            ):
                coordinator = _build_coordinator(riot, RawStore(None), cfg)

            assert coordinator is not None
            messages = [r.getMessage() for r in handler.buffer]
            assert any("unavailable at startup" in m for m in messages)
        finally:
            fetcher_log.removeHandler(handler)
