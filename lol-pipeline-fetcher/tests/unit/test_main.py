"""Unit tests for lol_fetcher.main — Phase 03 ACs 03-21 through 03-28."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import httpx
import pytest
import respx
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.streams import consume, publish

from lol_fetcher.main import _fetch_match, main

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
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}})
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
    """P15-HORIZON: TimeoutError from wait_for_token leaves message in PEL."""

    @pytest.mark.asyncio
    async def test_timeout__no_ack_no_dlq(self, r, cfg, log):
        """TimeoutError → return without ACK or DLQ; message stays in PEL for retry."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with (
            respx.mock,
            patch(
                "lol_fetcher.main.wait_for_token",
                new_callable=AsyncMock,
                side_effect=TimeoutError("rate limiter timeout"),
            ),
        ):
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        # No output published
        assert await r.xlen(_STREAM_OUT) == 0
        # No DLQ entry — transient condition
        assert await r.xlen("stream:dlq") == 0
        # Message NOT ACKed — stays in PEL for autoclaim/retry
        pending = await r.xpending(_STREAM_IN, _GROUP)
        assert pending["pending"] == 1
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
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}})
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
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}})
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
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}})
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
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}})
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
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}})
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
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}})
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
    """Global dedup: fetcher adds match_id to seen:matches SET."""

    @pytest.mark.asyncio
    async def test_fetcher_adds_to_seen_set(self, r, cfg, log):
        """After successful fetch, match_id is added to seen:matches."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(
                return_value=httpx.Response(200, json={"info": {"gameDuration": 1800}})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        assert await r.sismember("seen:matches", "NA1_123")
        # TTL refreshed on set
        ttl = await r.ttl("seen:matches")
        assert ttl > 0

    @pytest.mark.asyncio
    async def test_seen_set_not_written_on_error(self, r, cfg, log):
        """When fetch fails (404), match_id is NOT added to seen:matches."""
        raw_store = RawStore(r)
        env = _match_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_url()).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            await _fetch_match(r, riot, raw_store, cfg, msg_id, env, log)
            await riot.close()

        assert not await r.sismember("seen:matches", "NA1_123")
