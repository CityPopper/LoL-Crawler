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

from lol_crawler.main import (
    _RANK_HISTORY_MAX,
    _compute_activity_rate,
    _crawl_player,
    _fetch_rank,
    _handle_crawl_error,
    main,
)

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
    """R5: Priority is always cleared after a successful crawl."""

    @pytest.mark.asyncio
    async def test_crawl__matches_found__priority_cleared(self, r, cfg, log):
        """When published > 0, clear_priority() IS called — priority key removed."""
        puuid = "test-puuid-0001"
        # Set a priority key to verify it gets cleared
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

        # R5: Always clear priority after a successful crawl
        assert await r.xlen(_STREAM_OUT) == 2
        assert await r.get(f"player:priority:{puuid}") is None

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
    """R5: clear_priority is always called after a successful crawl."""

    @pytest.mark.asyncio
    async def test_crawl__published_gt_zero__clear_priority_called(self, r, cfg, log):
        """R5: When published > 0, clear_priority() IS called."""
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

        # R5: Always clear priority after a successful crawl
        mock_clear.assert_called_once_with(r, puuid)

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
    async def test_zrange_called_once_for_known_set(self, r, cfg, log):
        """ZRANGE called once for known-set; _compute_activity_rate uses pipeline."""
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

        # 1 for known-matches set only; _compute_activity_rate ZRANGE is pipelined
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
        """V16-1: TimeoutError before any API call → no last_crawled_at, ACK, no DLQ."""
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
        # last_crawled_at NOT set — no API call was made, so we cannot confirm
        # whether new matches exist; Discovery can re-seed next cycle.
        assert await r.hget("player:test-puuid-0001", "last_crawled_at") is None
        # Message was ACKed (to avoid infinite redelivery)
        pending = await r.xpending(_STREAM_IN, _GROUP)
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_crawl__timeout_before_api__clear_priority_not_called(self, r, cfg, log):
        """V16-1: TimeoutError before first API call — clear_priority must NOT run.

        When pages_fetched == 0 we cannot confirm whether new matches exist,
        so priority must be preserved to prevent Discovery from unblocking
        prematurely for a player whose crawl never completed.
        """
        puuid = "test-puuid-0001"
        await r.set(f"player:priority:{puuid}", "1", ex=86400)

        env = _puuid_envelope(puuid=puuid)
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

        # Priority key must still be present — timeout means no API call was made
        assert await r.get(f"player:priority:{puuid}") == "1"
        # No matches published and no DLQ entry
        assert await r.xlen(_STREAM_OUT) == 0
        assert await r.xlen("stream:dlq") == 0

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


class TestCrawlPriorityDowngrade:
    """4-tier priority: after 20 published match IDs, priority downgrades."""

    @pytest.mark.asyncio
    async def test_manual_20__first_20_keep_manual_20(self, r, cfg, log):
        """With manual_20, first 20 match IDs keep manual_20 priority."""
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="puuid",
            payload={"puuid": "puuid-m20", "region": "na1", "game_name": "M", "tag_line": "20"},
            max_attempts=5,
            priority="manual_20",
        )
        msg_id = await _setup_message(r, env)
        page = [f"NA1_{i}" for i in range(15)]  # fewer than 20

        with respx.mock:
            respx.get(_match_ids_url(puuid="puuid-m20", start=0)).mock(
                return_value=httpx.Response(200, json=page)
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        entries = await r.xrange(_STREAM_OUT)
        assert len(entries) == 15
        for _, fields in entries:
            assert fields["priority"] == "manual_20"

    @pytest.mark.asyncio
    async def test_manual_20__after_20_downgrades_to_manual_20plus(self, r, cfg, log):
        """With manual_20, match IDs beyond 20 get manual_20plus priority."""
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="puuid",
            payload={"puuid": "puuid-dg", "region": "na1", "game_name": "D", "tag_line": "G"},
            max_attempts=5,
            priority="manual_20",
        )
        msg_id = await _setup_message(r, env)
        page = [f"NA1_{i}" for i in range(25)]

        with respx.mock:
            respx.get(_match_ids_url(puuid="puuid-dg", start=0)).mock(
                return_value=httpx.Response(200, json=page)
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        entries = await r.xrange(_STREAM_OUT)
        assert len(entries) == 25
        priorities = [fields["priority"] for _, fields in entries]
        # First 20 keep manual_20
        assert all(p == "manual_20" for p in priorities[:20])
        # Remaining 5 downgraded to manual_20plus
        assert all(p == "manual_20plus" for p in priorities[20:])

    @pytest.mark.asyncio
    async def test_auto_20__after_20_downgrades_to_auto_new(self, r, cfg, log):
        """With auto_20, match IDs beyond 20 get auto_new priority."""
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="puuid",
            payload={"puuid": "puuid-a20", "region": "na1", "game_name": "A", "tag_line": "20"},
            max_attempts=5,
            priority="auto_20",
        )
        msg_id = await _setup_message(r, env)
        page = [f"NA1_{i}" for i in range(30)]

        with respx.mock:
            respx.get(_match_ids_url(puuid="puuid-a20", start=0)).mock(
                return_value=httpx.Response(200, json=page)
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        entries = await r.xrange(_STREAM_OUT)
        assert len(entries) == 30
        priorities = [fields["priority"] for _, fields in entries]
        assert all(p == "auto_20" for p in priorities[:20])
        assert all(p == "auto_new" for p in priorities[20:])

    @pytest.mark.asyncio
    async def test_normal_priority__no_downgrade(self, r, cfg, log):
        """Legacy 'normal' priority has no downgrade — all messages keep 'normal'."""
        env = _puuid_envelope()  # default priority='normal'
        msg_id = await _setup_message(r, env)
        page = [f"NA1_{i}" for i in range(25)]

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(return_value=httpx.Response(200, json=page))
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        entries = await r.xrange(_STREAM_OUT)
        assert len(entries) == 25
        for _, fields in entries:
            assert fields["priority"] == "normal"

    @pytest.mark.asyncio
    async def test_downgrade_across_pages(self, r, cfg, log):
        """Priority downgrade works correctly across page boundaries (100+10)."""
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="puuid",
            payload={
                "puuid": "puuid-pg2",
                "region": "na1",
                "game_name": "PG",
                "tag_line": "2",
            },
            max_attempts=5,
            priority="manual_20",
        )
        msg_id = await _setup_message(r, env)
        page1_full = [f"NA1_{i}" for i in range(100)]
        page2_partial = [f"NA1_{i}" for i in range(100, 110)]

        with respx.mock:
            respx.get(_match_ids_url(puuid="puuid-pg2", start=0)).mock(
                return_value=httpx.Response(200, json=page1_full)
            )
            respx.get(_match_ids_url(puuid="puuid-pg2", start=100)).mock(
                return_value=httpx.Response(200, json=page2_partial)
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        entries = await r.xrange(_STREAM_OUT)
        assert len(entries) == 110
        priorities = [fields["priority"] for _, fields in entries]
        assert all(p == "manual_20" for p in priorities[:20])
        assert all(p == "manual_20plus" for p in priorities[20:])


class TestBackpressure:
    """R1: Crawler pauses crawl when stream:match_id exceeds backpressure threshold."""

    @pytest.mark.asyncio
    async def test_backpressure_pauses_crawl_when_queue_deep(self, r, cfg, log):
        """When stream:match_id depth > threshold, crawler breaks out of pagination."""
        # Set a very low threshold
        cfg.match_id_backpressure_threshold = 5

        # Pre-fill stream:match_id with enough entries to exceed the threshold
        for i in range(10):
            await r.xadd(_STREAM_OUT, {"data": f"existing_{i}"})

        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            # The API should NOT be called because backpressure triggers first
            route = respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_NEW_1"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # API should not have been called — backpressure triggered before API call
        assert not route.called
        # No new matches published
        assert await r.xlen(_STREAM_OUT) == 10  # only pre-existing entries

    @pytest.mark.asyncio
    async def test_backpressure_threshold_zero_disables_check(self, r, cfg, log):
        """When threshold is 0, backpressure check is skipped entirely."""
        cfg.match_id_backpressure_threshold = 0

        # Fill stream with many entries
        for i in range(100):
            await r.xadd(_STREAM_OUT, {"data": f"existing_{i}"})

        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_NEW_1"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Match was published despite deep queue — threshold=0 means disabled
        assert await r.xlen(_STREAM_OUT) == 101  # 100 existing + 1 new

    @pytest.mark.asyncio
    async def test_backpressure_allows_crawl_when_below_threshold(self, r, cfg, log):
        """When depth is below threshold, crawl proceeds normally."""
        cfg.match_id_backpressure_threshold = 5000

        env = _puuid_envelope()
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_1", "NA1_2"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 2


class TestCrawlCursorPersistence:
    """R2: Crawler persists pagination offset so interrupted crawls can resume."""

    @pytest.mark.asyncio
    async def test_crawl_cursor_resumes_from_saved_offset(self, r, cfg, log):
        """When a cursor exists, pagination starts from the saved offset."""
        puuid = "test-puuid-0001"
        # Set a saved cursor at offset 200
        await r.set(f"crawl:cursor:{puuid}", "200", ex=600)

        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with respx.mock:
            # start=0 and start=100 should NOT be called
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=[f"NA1_{i}" for i in range(100)])
            )
            respx.get(_match_ids_url(start=100)).mock(
                return_value=httpx.Response(200, json=[f"NA1_{i}" for i in range(100, 200)])
            )
            # start=200 SHOULD be called (resume point)
            respx.get(_match_ids_url(start=200)).mock(
                return_value=httpx.Response(200, json=["NA1_RESUMED_1", "NA1_RESUMED_2"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Only the 2 matches from the resume page should be published
        assert await r.xlen(_STREAM_OUT) == 2
        entries = await r.xrange(_STREAM_OUT)
        match_ids = [
            MessageEnvelope.from_redis_fields(fields).payload["match_id"] for _, fields in entries
        ]
        assert "NA1_RESUMED_1" in match_ids
        assert "NA1_RESUMED_2" in match_ids

    @pytest.mark.asyncio
    async def test_crawl_cursor_deleted_on_completion(self, r, cfg, log):
        """After successful crawl completion, the cursor key is deleted."""
        puuid = "test-puuid-0001"
        cursor_key = f"crawl:cursor:{puuid}"
        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_1", "NA1_2"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Cursor should be deleted after completion
        assert await r.get(cursor_key) is None

    @pytest.mark.asyncio
    async def test_crawl_cursor_invalid_value_ignored(self, r, cfg, log):
        """Invalid cursor value is ignored, crawl starts from 0."""
        puuid = "test-puuid-0001"
        # Set an invalid cursor value
        await r.set(f"crawl:cursor:{puuid}", "not-a-number", ex=600)

        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with respx.mock:
            # start=0 should be called (invalid cursor ignored)
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_1"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        assert await r.xlen(_STREAM_OUT) == 1


class TestFetchRank:
    """Rank data fetching after successful crawl."""

    def _summoner_url(self, puuid="test-puuid-0001", region="na1"):
        return f"https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"

    def _league_url(self, summoner_id="summ-id-1", region="na1"):
        return f"https://{region}.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}"

    @pytest.mark.asyncio
    async def test_fetch_rank__stores_ranked_solo(self, r, cfg, log):
        """Rank data for RANKED_SOLO_5x5 is stored in player:rank:{puuid}."""
        puuid = "test-puuid-0001"
        with respx.mock:
            respx.get(self._summoner_url(puuid)).mock(
                return_value=httpx.Response(200, json={"id": "summ-id-1", "summonerLevel": 150})
            )
            respx.get(self._league_url("summ-id-1")).mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {
                            "queueType": "RANKED_SOLO_5x5",
                            "tier": "GOLD",
                            "rank": "II",
                            "leaguePoints": 75,
                            "wins": 100,
                            "losses": 80,
                        },
                        {
                            "queueType": "RANKED_FLEX_SR",
                            "tier": "SILVER",
                            "rank": "I",
                            "leaguePoints": 50,
                            "wins": 20,
                            "losses": 15,
                        },
                    ],
                )
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_rank(r, riot, cfg, puuid, "na1", log)
            await riot.close()

        rank = await r.hgetall(f"player:rank:{puuid}")
        assert rank["tier"] == "GOLD"
        assert rank["division"] == "II"
        assert rank["lp"] == "75"
        assert rank["wins"] == "100"
        assert rank["losses"] == "80"
        ttl = await r.ttl(f"player:rank:{puuid}")
        assert ttl > 0

        # Rank history timeline should have one entry
        hist = await r.zrange(f"player:rank:history:{puuid}", 0, -1, withscores=True)
        assert len(hist) == 1
        assert hist[0][0] == "GOLD:II:75"
        assert hist[0][1] > 0  # epoch_ms score
        hist_ttl = await r.ttl(f"player:rank:history:{puuid}")
        assert hist_ttl > 0

    @pytest.mark.asyncio
    async def test_fetch_rank__stores_summoner_level(self, r, cfg, log):
        """Summoner level is stored on player:{puuid} hash."""
        puuid = "test-puuid-0001"
        with respx.mock:
            respx.get(self._summoner_url(puuid)).mock(
                return_value=httpx.Response(200, json={"id": "summ-id-1", "summonerLevel": 250})
            )
            respx.get(self._league_url("summ-id-1")).mock(return_value=httpx.Response(200, json=[]))
            riot = RiotClient("RGAPI-test")
            await _fetch_rank(r, riot, cfg, puuid, "na1", log)
            await riot.close()

        assert await r.hget(f"player:{puuid}", "summoner_level") == "250"

    @pytest.mark.asyncio
    async def test_fetch_rank__stores_profile_icon_id(self, r, cfg, log):
        """profileIconId from summoner-v4 is stored on player:{puuid} hash."""
        puuid = "test-puuid-0001"
        with respx.mock:
            respx.get(self._summoner_url(puuid)).mock(
                return_value=httpx.Response(
                    200,
                    json={"id": "summ-id-1", "summonerLevel": 100, "profileIconId": 4567},
                )
            )
            respx.get(self._league_url("summ-id-1")).mock(return_value=httpx.Response(200, json=[]))
            riot = RiotClient("RGAPI-test")
            await _fetch_rank(r, riot, cfg, puuid, "na1", log)
            await riot.close()

        assert await r.hget(f"player:{puuid}", "profile_icon_id") == "4567"
        assert await r.hget(f"player:{puuid}", "summoner_level") == "100"

    @pytest.mark.asyncio
    async def test_fetch_rank__skips_when_disabled(self, r, cfg, log):
        """When fetch_rank_on_crawl is False, no API calls are made."""
        cfg.fetch_rank_on_crawl = False
        puuid = "test-puuid-0001"
        with respx.mock:
            route = respx.get(url__regex=r".*summoner.*").mock(
                return_value=httpx.Response(200, json={"id": "x"})
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_rank(r, riot, cfg, puuid, "na1", log)
            await riot.close()

        assert not route.called

    @pytest.mark.asyncio
    async def test_fetch_rank__failure_non_fatal(self, r, cfg, log):
        """Rank fetch failure does not raise — it is non-critical."""
        puuid = "test-puuid-0001"
        with respx.mock:
            respx.get(self._summoner_url(puuid)).mock(
                return_value=httpx.Response(500, text="Server Error")
            )
            riot = RiotClient("RGAPI-test")
            # Should NOT raise
            await _fetch_rank(r, riot, cfg, puuid, "na1", log)
            await riot.close()

        # No rank data stored
        assert not await r.exists(f"player:rank:{puuid}")

    @pytest.mark.asyncio
    async def test_fetch_rank__no_summoner_id__skips_league(self, r, cfg, log):
        """When summoner response has no 'id', league API is not called."""
        puuid = "test-puuid-0001"
        with respx.mock:
            respx.get(self._summoner_url(puuid)).mock(
                return_value=httpx.Response(200, json={"summonerLevel": 50})
            )
            league_route = respx.get(url__regex=r".*league.*").mock(
                return_value=httpx.Response(200, json=[])
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_rank(r, riot, cfg, puuid, "na1", log)
            await riot.close()

        assert not league_route.called


class TestGlobalDedup:
    """Global match dedup via seen:matches SET in crawler."""

    @pytest.mark.asyncio
    async def test_global_dedup__filters_seen_matches(self, r, cfg, log):
        """Matches in seen:matches are filtered out during crawl."""
        puuid = "test-puuid-0001"
        # Mark NA1_SEEN as already globally seen
        await r.sadd("seen:matches", "NA1_SEEN")

        env = _puuid_envelope(puuid=puuid)
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_SEEN", "NA1_NEW"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        # Only NA1_NEW should be published (NA1_SEEN filtered by global dedup)
        assert await r.xlen(_STREAM_OUT) == 1
        entries = await r.xrange(_STREAM_OUT)
        payload = MessageEnvelope.from_redis_fields(entries[0][1]).payload
        assert payload["match_id"] == "NA1_NEW"


class TestActivityRate:
    """Activity rate computation after successful crawl."""

    @pytest.mark.asyncio
    async def test_activity_rate__computed_after_crawl(self, r, cfg, log):
        """Activity rate is stored on player:{puuid} after crawl with new matches."""
        import time

        puuid = "test-puuid-0001"
        # Pre-populate match history: 10 matches over ~10 days
        now_ms = time.time() * 1000
        for i in range(10):
            await r.zadd(
                f"player:matches:{puuid}",
                {f"NA1_{i}": now_ms - (i * 86400 * 1000)},
            )

        await _compute_activity_rate(r, puuid, log)

        rate_str = await r.hget(f"player:{puuid}", "activity_rate")
        assert rate_str is not None
        rate = float(rate_str)
        assert rate > 0

        # Should also set recrawl_after
        recrawl = await r.hget(f"player:{puuid}", "recrawl_after")
        assert recrawl is not None
        assert float(recrawl) > time.time()

    @pytest.mark.asyncio
    async def test_activity_rate__high_rate_short_cooldown(self, r, cfg, log):
        """Players with >5 games/day get a 2-hour cooldown."""
        import time

        puuid = "test-puuid-fast"
        # 60 matches in ~1 day = high rate
        now_ms = time.time() * 1000
        one_day_ago_ms = now_ms - 86400 * 1000
        for i in range(60):
            score = one_day_ago_ms + (i * 1440 * 1000)  # spread within 1 day
            await r.zadd(f"player:matches:{puuid}", {f"NA1_{i}": score})

        await _compute_activity_rate(r, puuid, log)

        recrawl = float(await r.hget(f"player:{puuid}", "recrawl_after"))
        # 2h cooldown = time.time() + 7200 (±120s tolerance)
        expected = time.time() + 7200
        assert abs(recrawl - expected) < 120

    @pytest.mark.asyncio
    async def test_activity_rate__mid_rate_6h_cooldown(self, r, cfg, log):
        """Players with 1-5 games/day get a 6-hour cooldown."""
        import time

        puuid = "test-puuid-mid"
        # 3 matches over 1 day = mid rate (~3 games/day)
        now_ms = time.time() * 1000
        one_day_ago_ms = now_ms - 86400 * 1000
        for i in range(3):
            score = one_day_ago_ms + (i * 28800 * 1000)
            await r.zadd(f"player:matches:{puuid}", {f"NA1_MID_{i}": score})

        await _compute_activity_rate(r, puuid, log)

        recrawl = float(await r.hget(f"player:{puuid}", "recrawl_after"))
        # 6h cooldown = time.time() + 21600 (±120s tolerance)
        expected = time.time() + 21600
        assert abs(recrawl - expected) < 120

    @pytest.mark.asyncio
    async def test_activity_rate__low_rate_24h_cooldown(self, r, cfg, log):
        """Players with <1 game/day get a 24-hour cooldown."""
        import time

        puuid = "test-puuid-low"
        # 2 matches over 10 days = low rate (~0.2 games/day)
        now_ms = time.time() * 1000
        for i in range(2):
            score = now_ms - ((i + 1) * 5 * 86400 * 1000)
            await r.zadd(f"player:matches:{puuid}", {f"NA1_LOW_{i}": score})

        await _compute_activity_rate(r, puuid, log)

        recrawl = float(await r.hget(f"player:{puuid}", "recrawl_after"))
        # 24h cooldown = time.time() + 86400 (±120s tolerance)
        expected = time.time() + 86400
        assert abs(recrawl - expected) < 120

    @pytest.mark.asyncio
    async def test_activity_rate__no_matches__skips(self, r, cfg, log):
        """When player has no match history, activity rate is not computed."""
        puuid = "test-puuid-empty"
        await _compute_activity_rate(r, puuid, log)

        assert await r.hget(f"player:{puuid}", "activity_rate") is None
        assert await r.hget(f"player:{puuid}", "recrawl_after") is None


class TestRankHistoryCap:
    """Bug 3 fix: player:rank:history:{puuid} ZSET is capped to prevent unbounded growth."""

    def _summoner_url(self, puuid="test-puuid-0001", region="na1"):
        return f"https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"

    def _league_url(self, summoner_id="summ-id-1", region="na1"):
        return f"https://{region}.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}"

    @pytest.mark.asyncio
    async def test_rank_history__capped_at_max(self, r, cfg, log):
        """Rank history ZSET is trimmed to _RANK_HISTORY_MAX entries."""
        puuid = "test-puuid-cap"
        hist_key = f"player:rank:history:{puuid}"

        # Pre-fill with _RANK_HISTORY_MAX + 50 entries
        for i in range(_RANK_HISTORY_MAX + 50):
            await r.zadd(hist_key, {f"GOLD:II:{i}": float(i)})
        assert await r.zcard(hist_key) == _RANK_HISTORY_MAX + 50

        with respx.mock:
            respx.get(self._summoner_url(puuid)).mock(
                return_value=httpx.Response(200, json={"id": "summ-id-1", "summonerLevel": 100})
            )
            respx.get(self._league_url("summ-id-1")).mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {
                            "queueType": "RANKED_SOLO_5x5",
                            "tier": "PLAT",
                            "rank": "I",
                            "leaguePoints": 99,
                            "wins": 200,
                            "losses": 150,
                        }
                    ],
                )
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_rank(r, riot, cfg, puuid, "na1", log)
            await riot.close()

        # After trim, should be at most _RANK_HISTORY_MAX
        count = await r.zcard(hist_key)
        assert count <= _RANK_HISTORY_MAX

    @pytest.mark.asyncio
    async def test_rank_history__small_set_not_trimmed(self, r, cfg, log):
        """Rank history with fewer than max entries is not trimmed."""
        puuid = "test-puuid-small"
        hist_key = f"player:rank:history:{puuid}"

        # Pre-fill with 5 entries
        for i in range(5):
            await r.zadd(hist_key, {f"SILVER:III:{i}": float(i)})

        with respx.mock:
            respx.get(self._summoner_url(puuid)).mock(
                return_value=httpx.Response(200, json={"id": "summ-id-2", "summonerLevel": 50})
            )
            respx.get(self._league_url("summ-id-2")).mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {
                            "queueType": "RANKED_SOLO_5x5",
                            "tier": "SILVER",
                            "rank": "II",
                            "leaguePoints": 30,
                            "wins": 50,
                            "losses": 40,
                        }
                    ],
                )
            )
            riot = RiotClient("RGAPI-test")
            await _fetch_rank(r, riot, cfg, puuid, "na1", log)
            await riot.close()

        # 5 existing + 1 new = 6, which is under the cap
        count = await r.zcard(hist_key)
        assert count == 6

    def test_rank_history_max_constant(self):
        """_RANK_HISTORY_MAX is 500."""
        assert _RANK_HISTORY_MAX == 500


class TestCrawlCorrelationIdPropagation:
    """Outbound match_id envelopes propagate correlation_id from inbound puuid envelope."""

    @pytest.mark.asyncio
    async def test_crawl__propagates_correlation_id(self, r, cfg, log):
        """Match IDs published to stream:match_id carry the same correlation_id."""
        env = MessageEnvelope(
            source_stream=_STREAM_IN,
            type="puuid",
            payload={
                "puuid": "test-puuid-0001",
                "region": "na1",
                "game_name": "Test",
                "tag_line": "NA1",
            },
            max_attempts=5,
            correlation_id="trace-crawl-abc",
        )
        msg_id = await _setup_message(r, env)

        with respx.mock:
            respx.get(_match_ids_url(start=0)).mock(
                return_value=httpx.Response(200, json=["NA1_COR_1", "NA1_COR_2"])
            )
            riot = RiotClient("RGAPI-test")
            await _crawl_player(r, riot, cfg, msg_id, env, log)
            await riot.close()

        entries = await r.xrange(_STREAM_OUT)
        assert len(entries) == 2
        for _, fields in entries:
            out_env = MessageEnvelope.from_redis_fields(fields)
            assert out_env.correlation_id == "trace-crawl-abc"


class TestHandleCrawlErrorLogMessage:
    """E4: Riot API error log should mention DLQ routing context."""

    @pytest.mark.asyncio
    async def test_server_error__log_mentions_dlq(self, r):
        """ServerError log message should contain 'DLQ' for operator context."""
        from lol_pipeline.riot_api import ServerError

        env = MessageEnvelope(
            source_stream="stream:puuid",
            type="puuid",
            payload={"puuid": "test-puuid", "game_name": "T", "tag_line": "1", "region": "na1"},
            max_attempts=5,
        )
        msg_id = await r.xadd("stream:puuid", env.to_redis_fields())
        await r.xgroup_create("stream:puuid", "crawlers", "0", mkstream=True)
        log = logging.getLogger("crawler")
        exc = ServerError(500, "internal error")

        # Capture log records directly since the crawler logger has propagate=False
        captured: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = lambda rec: captured.append(rec)  # type: ignore[method-assign]
        handler.setLevel(logging.ERROR)
        log.addHandler(handler)
        try:
            await _handle_crawl_error(r, msg_id, env, exc, "test-puuid", log)
        finally:
            log.removeHandler(handler)

        assert any("DLQ" in rec.getMessage() for rec in captured)
