"""Unit tests for lol_crawler.main — Phase 03 ACs 03-12 through 03-20."""

from __future__ import annotations

import logging

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.streams import consume, publish
from lol_crawler.main import _crawl_player

_STREAM_IN = "stream:puuid"
_STREAM_OUT = "stream:match_id"
_GROUP = "crawlers"

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
    return logging.getLogger("test-crawler")


def _puuid_envelope(puuid="test-puuid-0001", region="na1"):
    return MessageEnvelope(
        source_stream=_STREAM_IN,
        type="puuid",
        payload={"puuid": puuid, "region": region, "game_name": "Test", "tag_line": "NA1"},
        max_attempts=5,
    )


async def _setup_message(r, envelope):
    """Publish to stream and consume so msg_id is in PEL for ack."""
    await publish(r, _STREAM_IN, envelope)
    msgs = await consume(r, _STREAM_IN, _GROUP, "test-consumer", block=0)
    assert len(msgs) == 1
    return msgs[0][0]  # msg_id


def _match_ids_url(puuid="test-puuid-0001", region="americas", start=0, count=100):
    return (
        f"https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/"
        f"{puuid}/ids?start={start}&count={count}"
    )


class TestCrawlZeroMatches:
    @pytest.mark.asyncio
    async def test_zero_matches(self, r, cfg, log):
        """AC-03-12: 0 match IDs → 0 in stream:match_id; last_crawled_at set; ACK sent."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url()).mock(
                return_value=httpx.Response(200, json=[])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 0
        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is not None


class TestCrawlPagination:
    @pytest.mark.asyncio
    async def test_single_page_100_matches(self, r, cfg, log):
        """AC-03-13: 100 match IDs (1 page) → stream:match_id=100."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)
        page1 = [f"NA1_{i}" for i in range(100)]

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=page1)
            )
            respx.get(_match_ids_url(start=100)).mock(
                return_value=httpx.Response(200, json=[])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 100

    @pytest.mark.asyncio
    async def test_three_pages(self, r, cfg, log):
        """AC-03-14: 3 pages (100+100+50) → 250 messages."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)
        page1 = [f"NA1_{i}" for i in range(100)]
        page2 = [f"NA1_{i}" for i in range(100, 200)]
        page3 = [f"NA1_{i}" for i in range(200, 250)]

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=page1)
            )
            respx.get(_match_ids_url(start=100)).mock(
                return_value=httpx.Response(200, json=page2)
            )
            respx.get(_match_ids_url(start=200)).mock(
                return_value=httpx.Response(200, json=page3)
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 250


class TestCrawlDedup:
    @pytest.mark.asyncio
    async def test_all_known_stops_early(self, r, cfg, log):
        """AC-03-15: all match IDs on page 1 already known → 0 messages."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)
        known_ids = [f"NA1_{i}" for i in range(100)]
        # Pre-populate known matches
        for mid in known_ids:
            await r.zadd(f"player:matches:test-puuid-0001", {mid: 1000.0})

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=known_ids)
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 0

    @pytest.mark.asyncio
    async def test_partial_known_publishes_new_only(self, r, cfg, log):
        """AC-03-16: 60 known + 40 new → 40 messages; pagination stops."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)
        known_ids = [f"NA1_{i}" for i in range(60)]
        new_ids = [f"NA1_{i}" for i in range(60, 100)]
        page = known_ids + new_ids
        for mid in known_ids:
            await r.zadd(f"player:matches:test-puuid-0001", {mid: 1000.0})

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=page)
            )
            # Page was full (100) but had new ids, so crawler continues
            respx.get(_match_ids_url(start=100)).mock(
                return_value=httpx.Response(200, json=[])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 40


class TestCrawlWindowedDedup:
    """Windowed dedup: use ZRANGEBYSCORE when last_crawled_at exists."""

    @pytest.mark.asyncio
    async def test_windowed_dedup_uses_zrangebyscore(self, r, cfg, log):
        """With last_crawled_at set, crawler uses ZRANGEBYSCORE instead of ZRANGE."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        # Set last_crawled_at (recent)
        ts = "2025-01-15T12:00:00+00:00"
        await r.hset(
            "player:test-puuid-0001",
            mapping={"last_crawled_at": ts},
        )

        # Pre-populate 100 recent known matches (within window)
        recent_ids = [f"NA1_{i}" for i in range(100)]
        key = "player:matches:test-puuid-0001"
        for mid in recent_ids:
            # Score = Jan 14, 2025 in epoch ms (within 7-day window)
            await r.zadd(key, {mid: 1736899200000.0})

        zrangebyscore_called = False
        zrange_called = False
        original_zrangebyscore = r.zrangebyscore
        original_zrange = r.zrange

        async def tracking_zrangebyscore(*args, **kwargs):
            nonlocal zrangebyscore_called
            zrangebyscore_called = True
            return await original_zrangebyscore(*args, **kwargs)

        async def tracking_zrange(*args, **kwargs):
            nonlocal zrange_called
            zrange_called = True
            return await original_zrange(*args, **kwargs)

        r.zrangebyscore = tracking_zrangebyscore
        r.zrange = tracking_zrange

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=recent_ids)
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert zrangebyscore_called, "Expected ZRANGEBYSCORE to be called"
        assert not zrange_called, "Expected ZRANGE not to be called"
        assert await r.xlen(_STREAM_OUT) == 0  # all known

    @pytest.mark.asyncio
    async def test_windowed_dedup_excludes_old_matches(self, r, cfg, log):
        """Old matches outside the window are not loaded into the known set."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        # Set last_crawled_at to Jan 15, 2025
        ts = "2025-01-15T12:00:00+00:00"
        await r.hset(
            "player:test-puuid-0001",
            mapping={"last_crawled_at": ts},
        )

        # Add an old match well outside the 7-day window (Dec 2024)
        key = "player:matches:test-puuid-0001"
        await r.zadd(key, {"NA1_OLD": 1733011200000.0})

        # API returns the old match ID — since it's outside the window,
        # it won't be in the known set and will be published as "new"
        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_OLD"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Old match is re-published because it was outside the dedup window
        assert await r.xlen(_STREAM_OUT) == 1

    @pytest.mark.asyncio
    async def test_no_last_crawled_at_falls_back_to_zrange(self, r, cfg, log):
        """Without last_crawled_at, crawler falls back to ZRANGE (loads all)."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        # No last_crawled_at set — first crawl scenario
        # Pre-populate a known match
        key = "player:matches:test-puuid-0001"
        await r.zadd(key, {"NA1_KNOWN": 1000.0})

        zrange_called = False
        original_zrange = r.zrange

        async def tracking_zrange(*args, **kwargs):
            nonlocal zrange_called
            zrange_called = True
            return await original_zrange(*args, **kwargs)

        r.zrange = tracking_zrange

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_KNOWN"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert zrange_called, "Expected ZRANGE fallback when no last_crawled_at"
        assert await r.xlen(_STREAM_OUT) == 0  # known match filtered


class TestCrawlSingleZrange:
    @pytest.mark.asyncio
    async def test_zrange_called_once(self, r, cfg, log):
        """AC-03-17: ZRANGE called exactly once per crawl."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        call_count = 0
        original_zrange = r.zrange

        async def counting_zrange(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return await original_zrange(*args, **kwargs)

        r.zrange = counting_zrange

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_1", "NA1_2"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert call_count == 1


class TestCrawlFailureMidPage:
    @pytest.mark.asyncio
    async def test_failure_on_page2_no_last_crawled_at(self, r, cfg, log):
        """AC-03-18: failure on page 2 → last_crawled_at not set."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)
        page1 = [f"NA1_{i}" for i in range(100)]

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=page1)
            )
            respx.get(_match_ids_url(start=100)).mock(
                return_value=httpx.Response(403)
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is None


class TestCrawlErrors:
    @pytest.mark.asyncio
    async def test_403_halts_system(self, r, cfg, log):
        """AC-03-19: 403 → system:halted='1'; last_crawled_at not set; no ACK."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(403)
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.get("system:halted") == "1"
        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is None

    @pytest.mark.asyncio
    async def test_429_nacks_to_dlq(self, r, cfg, log):
        """AC-03-19b: 429 → nack_to_dlq with http_429; last_crawled_at not set."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(429, headers={"Retry-After": "5"})
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen("stream:dlq") == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["failure_code"] == "http_429"
        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is None

    @pytest.mark.asyncio
    async def test_500_nacks_to_dlq(self, r, cfg, log):
        """AC-03-19c: 500 → nack_to_dlq with http_5xx."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(500, text="Server Error")
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen("stream:dlq") == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["failure_code"] == "http_5xx"

    @pytest.mark.asyncio
    async def test_system_halted_skips(self, r, cfg, log):
        """AC-03-20: system:halted set → no ACK; exits immediately."""
        await r.set("system:halted", "1")
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            # No routes — any HTTP call would raise
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 0
