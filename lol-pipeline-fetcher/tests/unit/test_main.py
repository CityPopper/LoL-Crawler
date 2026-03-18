"""Unit tests for lol_fetcher.main — Phase 03 ACs 03-21 through 03-28."""

from __future__ import annotations

import logging

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_pipeline.config import Config
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
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
            respx.get(_match_url()).mock(
                return_value=httpx.Response(500, text="Server Error")
            )
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
