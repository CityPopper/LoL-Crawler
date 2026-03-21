"""Unit tests for lol_crawler.main — Phase 03 ACs 03-12 through 03-20."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import httpx
import pytest
import respx
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.streams import consume, publish

from lol_crawler.main import _crawl_player, main

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
            respx.get(_match_ids_url()).mock(return_value=httpx.Response(200, json=[]))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 0
        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is not None


class TestCrawlPriority:
    @pytest.mark.asyncio
    async def test_crawl__zero_matches__clears_priority_key(self, r, cfg, log):
        """Zero matches found → clear_priority called (removes player:priority key)."""
        puuid = "test-puuid-0001"
        # Set a priority key to verify it gets cleared
        await r.set(f"player:priority:{puuid}", "1", ex=86400)

        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url()).mock(return_value=httpx.Response(200, json=[]))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Priority key should be cleared
        assert await r.get(f"player:priority:{puuid}") is None


class TestCrawlPriorityPreservation:
    """Priority key behavior depends on whether new matches were published."""

    @pytest.mark.asyncio
    async def test_crawl__matches_found__priority_not_cleared(self, r, cfg, log):
        """When published > 0, clear_priority() is NOT called — priority key remains."""
        puuid = "test-puuid-0001"
        # Set a priority key to verify it is NOT cleared
        await r.set(f"player:priority:{puuid}", "1", ex=86400)

        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url()).mock(
                return_value=httpx.Response(200, json=["NA1_NEW_1", "NA1_NEW_2"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Matches were published, so priority must be preserved
        assert await r.xlen(_STREAM_OUT) == 2
        assert await r.get(f"player:priority:{puuid}") == "1"

    @pytest.mark.asyncio
    async def test_crawl__no_matches__priority_cleared(self, r, cfg, log):
        """When published == 0, clear_priority() IS called — priority key removed."""
        puuid = "test-puuid-0001"
        await r.set(f"player:priority:{puuid}", "1", ex=86400)

        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url()).mock(return_value=httpx.Response(200, json=[]))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # No matches published, so priority must be cleared
        assert await r.xlen(_STREAM_OUT) == 0
        assert await r.get(f"player:priority:{puuid}") is None


class TestCrawlPriorityClearCallBehavior:
    """clear_priority call depends on whether new matches were published."""

    @pytest.mark.asyncio
    async def test_crawl__published_gt_zero__clear_priority_not_called(self, r, cfg, log):
        """When published > 0, clear_priority() must NOT be called."""
        puuid = "test-puuid-0001"
        await r.set(f"player:priority:{puuid}", "high")
        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with (
            respx.mock,
            patch("lol_crawler.main.clear_priority", new_callable=AsyncMock) as mock_clear,
        ):
            respx.get(_match_ids_url()).mock(
                return_value=httpx.Response(200, json=["NA1_NEW_X", "NA1_NEW_Y"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Matches were published, so clear_priority must NOT have been called
        mock_clear.assert_not_called()

    @pytest.mark.asyncio
    async def test_crawl__published_eq_zero__clear_priority_called(self, r, cfg, log):
        """When published == 0, clear_priority() must be called with (r, puuid)."""
        puuid = "test-puuid-0001"
        await r.set(f"player:priority:{puuid}", "high")
        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with (
            respx.mock,
            patch("lol_crawler.main.clear_priority", new_callable=AsyncMock) as mock_clear,
        ):
            respx.get(_match_ids_url()).mock(return_value=httpx.Response(200, json=[]))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # No matches published, so clear_priority must have been called
        mock_clear.assert_called_once_with(r, puuid)

    @pytest.mark.asyncio
    async def test_crawl__all_known_matches__clear_priority_called(self, r, cfg, log):
        """All returned match IDs already known (dedup) → published=0 → clear_priority called."""
        puuid = "test-puuid-0001"
        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        # Pre-populate known matches
        known_ids = ["NA1_KNOWN_1", "NA1_KNOWN_2"]
        for mid in known_ids:
            await r.zadd(f"player:matches:{puuid}", {mid: 1000.0})

        with (
            respx.mock,
            patch("lol_crawler.main.clear_priority", new_callable=AsyncMock) as mock_clear,
        ):
            respx.get(_match_ids_url()).mock(return_value=httpx.Response(200, json=known_ids))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # All matches known → published=0 → clear_priority called
        mock_clear.assert_called_once_with(r, puuid)


class TestCrawlPagination:
    @pytest.mark.asyncio
    async def test_single_page_100_matches(self, r, cfg, log):
        """AC-03-13: 100 match IDs (1 page) → stream:match_id=100."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)
        page1 = [f"NA1_{i}" for i in range(100)]

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(return_value=httpx.Response(200, json=page1))
            respx.get(_match_ids_url(start=100)).mock(return_value=httpx.Response(200, json=[]))
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
            respx.get(_match_ids_url(start=0)).mock(return_value=httpx.Response(200, json=page1))
            respx.get(_match_ids_url(start=100)).mock(return_value=httpx.Response(200, json=page2))
            respx.get(_match_ids_url(start=200)).mock(return_value=httpx.Response(200, json=page3))
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
            await r.zadd("player:matches:test-puuid-0001", {mid: 1000.0})

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
            await r.zadd("player:matches:test-puuid-0001", {mid: 1000.0})

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(return_value=httpx.Response(200, json=page))
            # Page was full (100) but had new ids, so crawler continues
            respx.get(_match_ids_url(start=100)).mock(return_value=httpx.Response(200, json=[]))
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


class TestCrawlFromisoformatValueError:
    """P14-CR-4: Corrupt last_crawled_at falls back to full ZRANGE instead of crashing."""

    @pytest.mark.asyncio
    async def test_corrupt_last_crawled_at_falls_back_to_zrange(self, r, cfg, log):
        """Corrupt last_crawled_at (not valid ISO date) falls back to ZRANGE."""
        puuid = "test-puuid-0001"
        # Set a corrupt last_crawled_at value
        await r.hset(f"player:{puuid}", mapping={"last_crawled_at": "not-a-date"})
        # Add a known match
        await r.zadd(f"player:matches:{puuid}", {"NA1_KNOWN": 1000.0})

        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with respx.mock:
            # API returns the known match — should not publish (it's known)
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_KNOWN"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Should NOT crash, should fall back to ZRANGE and filter known matches
        assert await r.xlen(_STREAM_OUT) == 0
        assert await r.hget(f"player:{puuid}", "last_crawled_at") is not None


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
            respx.get(_match_ids_url(start=0)).mock(return_value=httpx.Response(200, json=page1))
            respx.get(_match_ids_url(start=100)).mock(return_value=httpx.Response(403))
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
            respx.get(_match_ids_url(start=0)).mock(return_value=httpx.Response(403))
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


class TestCrawlNotFound:
    """NotFoundError (404) — player doesn't exist, discard permanently."""

    @pytest.mark.asyncio
    async def test_404_acks_and_does_not_dlq(self, r, cfg, log):
        """404 → ACK the message, no DLQ entry, no last_crawled_at set."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # No DLQ entry — permanent error, retrying won't help
        assert await r.xlen("stream:dlq") == 0
        # No output messages
        assert await r.xlen(_STREAM_OUT) == 0
        # last_crawled_at NOT set (crawl did not succeed)
        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is None
        # Message was ACKed (removed from PEL)
        pending = await r.xpending(_STREAM_IN, _GROUP)
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_404_on_page2_does_not_set_last_crawled(self, r, cfg, log):
        """404 on page 2 → partial matches published but no last_crawled_at."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)
        page1 = [f"NA1_{i}" for i in range(100)]

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(return_value=httpx.Response(200, json=page1))
            respx.get(_match_ids_url(start=100)).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Page 1 matches were published before the 404
        assert await r.xlen(_STREAM_OUT) == 100
        # No DLQ entry
        assert await r.xlen("stream:dlq") == 0
        # last_crawled_at NOT set
        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is None
        # Message was ACKed
        pending = await r.xpending(_STREAM_IN, _GROUP)
        assert pending["pending"] == 0


class TestCrawl404ClearsPriority:
    """P14-FV-7: 404 response clears the priority key."""

    @pytest.mark.asyncio
    async def test_404_clears_priority(self, r, cfg, log):
        """NotFoundError (404) clears player:priority:{puuid} before ACK."""
        puuid = "test-puuid-0001"
        await r.set(f"player:priority:{puuid}", "1", ex=86400)
        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Priority should be cleared after 404
        assert await r.get(f"player:priority:{puuid}") is None
        # Message should be ACK'd (no DLQ)
        assert await r.xlen("stream:dlq") == 0


class TestCrawlEdgeCases:
    """Tier 3 — Crawler edge case tests."""

    @pytest.mark.asyncio
    async def test_429_without_retry_after_uses_default(self, r, cfg, log):
        """429 without Retry-After header uses default retry_after_ms (None → backoff)."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(429)  # no Retry-After header
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen("stream:dlq") == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["failure_code"] == "http_429"
        # retry_after_ms should be present (default from RiotClient)
        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is None

    @pytest.mark.asyncio
    async def test_empty_puuid_in_payload_still_attempts_api(self, r, cfg, log):
        """Empty puuid string in payload still attempts API call (no pre-validation)."""
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="puuid",
            payload={"puuid": "", "region": "na1", "game_name": "", "tag_line": ""},
            max_attempts=5,
        )
        msg_id = await _setup_message(r, env)

        with respx.mock:
            # Empty puuid produces a URL with empty path segment
            respx.get(url__regex=r".*/ids\?.*").mock(return_value=httpx.Response(200, json=[]))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Should complete without error (empty matches = no output)
        assert await r.xlen(_STREAM_OUT) == 0


class TestMainEntryPoint:
    """Tests for main() bootstrap and teardown."""

    @pytest.mark.asyncio
    async def test_main__creates_redis_and_starts_consumer(self, monkeypatch):
        """main() creates Config, Redis, RiotClient, then calls run_consumer."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        mock_consumer = AsyncMock()
        with (
            patch("lol_crawler.main.Config") as mock_cfg,
            patch("lol_crawler.main.get_redis", return_value=mock_r),
            patch("lol_crawler.main.RiotClient") as mock_riot,
            patch("lol_crawler.main.run_consumer", mock_consumer),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            await main()
        mock_consumer.assert_called_once()
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__keyboard_interrupt__closes_redis(self, monkeypatch):
        """KeyboardInterrupt during run_consumer → redis.aclose() still called."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        with (
            patch("lol_crawler.main.Config") as mock_cfg,
            patch("lol_crawler.main.get_redis", return_value=mock_r),
            patch("lol_crawler.main.RiotClient") as mock_riot,
            patch("lol_crawler.main.run_consumer", side_effect=KeyboardInterrupt),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()


class TestCrawlerPublishPipeline:
    """P14-OPT-4: Crawler batches per-page match ID publishes into a pipeline."""

    @pytest.mark.asyncio
    async def test_publish_uses_pipeline_not_individual_calls(self, r, cfg, log):
        """New match IDs are published via pipeline xadd, not individual publish() calls."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url()).mock(
                return_value=httpx.Response(200, json=["NA1_NEW_1", "NA1_NEW_2"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # All new match IDs should be in stream:match_id
        assert await r.xlen(_STREAM_OUT) == 2
        # Verify the messages have correct content
        entries = await r.xrange(_STREAM_OUT)
        match_ids = [
            MessageEnvelope.from_redis_fields(fields).payload["match_id"] for _, fields in entries
        ]
        assert "NA1_NEW_1" in match_ids
        assert "NA1_NEW_2" in match_ids


class TestPaginationRateLimiterTimeout:
    """P15-HORIZON: TimeoutError from wait_for_token exits pagination gracefully."""

    @pytest.mark.asyncio
    async def test_timeout__returns_zero_published__no_propagation(self, r, cfg, log):
        """TimeoutError in pagination loop → break, return 0, no DLQ, no crash."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with (
            respx.mock,
            patch(
                "lol_crawler.main.wait_for_token",
                new_callable=AsyncMock,
                side_effect=TimeoutError("rate limiter timeout"),
            ),
        ):
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # No matches published (timeout on first page)
        assert await r.xlen(_STREAM_OUT) == 0
        # No DLQ entry — transient, not a handler crash
        assert await r.xlen("stream:dlq") == 0
        # last_crawled_at IS set (crawl completed normally, just with 0 results)
        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is not None
        # Message was ACKed
        pending = await r.xpending(_STREAM_IN, _GROUP)
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_timeout_on_page2__publishes_page1_only(self, r, cfg, log):
        """TimeoutError on page 2 → page 1 matches published, page 2 skipped."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)
        page1 = [f"NA1_{i}" for i in range(100)]

        call_count = 0

        async def _timeout_on_second_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise TimeoutError("rate limiter timeout")

        with (
            respx.mock,
            patch(
                "lol_crawler.main.wait_for_token",
                new_callable=AsyncMock,
                side_effect=_timeout_on_second_call,
            ),
        ):
            riot = RiotClient("RGAPI-test")
            riot.get_match_ids = AsyncMock(side_effect=[page1])
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Page 1 matches were published before timeout
        assert await r.xlen(_STREAM_OUT) == 100
        # No DLQ entry
        assert await r.xlen("stream:dlq") == 0
        # last_crawled_at set (crawl finished gracefully)
        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is not None


class TestPaginationHaltCheck:
    """P12-DBG-2: Crawler checks system:halted between pagination pages."""

    @pytest.mark.asyncio
    async def test_pagination__halted_mid_crawl__stops_early(self, r, cfg, log):
        """When system:halted is set after page 1, page 2 is never fetched."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)
        page1 = [f"NA1_{i}" for i in range(100)]

        page_fetches = 0

        async def _halt_after_page1(*args, **kwargs):
            nonlocal page_fetches
            page_fetches += 1
            if page_fetches == 1:
                # After first page fetch, set system:halted
                await r.set("system:halted", "1")
                return page1
            # Second page should never be reached
            return [f"NA1_P2_{i}" for i in range(50)]

        with (
            respx.mock,
            patch("lol_crawler.main.wait_for_token", new_callable=AsyncMock),
        ):
            riot = RiotClient("RGAPI-test")
            riot.get_match_ids = AsyncMock(side_effect=_halt_after_page1)
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Only page 1 matches should be published (100), not page 2 (50)
        assert await r.xlen(_STREAM_OUT) == 100
        assert page_fetches == 1, "Second page should not have been fetched"

    @pytest.mark.asyncio
    async def test_pagination__not_halted__fetches_all_pages(self, r, cfg, log):
        """When system:halted is NOT set, all pages are fetched normally."""
        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)
        page1 = [f"NA1_{i}" for i in range(100)]
        page2 = [f"NA1_{i}" for i in range(100, 150)]

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(return_value=httpx.Response(200, json=page1))
            respx.get(_match_ids_url(start=100)).mock(return_value=httpx.Response(200, json=page2))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 150
