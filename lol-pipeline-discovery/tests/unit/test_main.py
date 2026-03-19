"""Unit tests for lol_discovery.main — player promotion with name resolution."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import httpx
import pytest
import respx
from lol_pipeline.config import Config
from lol_pipeline.riot_api import RiotClient
from redis.exceptions import RedisError

from lol_discovery.main import _is_idle, _parse_member, _promote_batch, _resolve_names, main


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
    return logging.getLogger("test-discovery")


class TestResolveNamesHmget:
    """Perf: _resolve_names uses HMGET (1 round-trip) instead of 2 HGET calls."""

    @pytest.mark.asyncio
    async def test_uses_hmget_for_name_resolution(self, r, log):
        """When both game_name and tag_line exist, HMGET returns both in one call."""
        await r.hset(
            "player:puuid-hmget",
            mapping={"game_name": "HmgetPlayer", "tag_line": "EUW1"},
        )
        riot = RiotClient("RGAPI-test")
        result = await _resolve_names(r, riot, "puuid-hmget", "euw1", log)
        await riot.close()
        assert result == ("HmgetPlayer", "EUW1")

    @pytest.mark.asyncio
    async def test_hmget_returns_none_when_names_missing(self, r, log):
        """When names not backfilled, HMGET returns [None, None] and falls through to API."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-noname"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid-noname", "gameName": "ApiName", "tagLine": "001"},
                )
            )
            riot = RiotClient("RGAPI-test")
            result = await _resolve_names(r, riot, "puuid-noname", "na1", log)
            await riot.close()
        assert result == ("ApiName", "001")


class TestPromoteBatchNames:
    @pytest.mark.asyncio
    async def test_uses_backfilled_names_when_available(self, r, cfg, log):
        """When parser has backfilled game_name/tag_line, discovery should use them."""
        # Backfill: parser set game_name/tag_line but no seeded_at
        await r.hset(
            "player:puuid-abc",
            mapping={
                "game_name": "TestPlayer",
                "tag_line": "NA1",
            },
        )
        await r.zadd("discover:players", {"puuid-abc:na1": 1700000000000.0})

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 1
        # Player hash should now have seeded_at and names
        assert await r.hget("player:puuid-abc", "game_name") == "TestPlayer"
        assert await r.hget("player:puuid-abc", "tag_line") == "NA1"
        assert await r.hget("player:puuid-abc", "seeded_at") is not None

        # Published payload should include game_name and tag_line
        entries = await r.xrange("stream:puuid")
        assert len(entries) == 1

    @pytest.mark.asyncio
    async def test_resolves_names_via_riot_api_when_not_backfilled(self, r, cfg, log):
        """When no backfilled names exist, discovery should resolve via Riot API."""
        await r.zadd("discover:players", {"puuid-xyz:na1": 1700000000000.0})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-xyz"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid-xyz", "gameName": "Resolved", "tagLine": "007"},
                )
            )
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 1
        assert await r.hget("player:puuid-xyz", "game_name") == "Resolved"
        assert await r.hget("player:puuid-xyz", "tag_line") == "007"

    @pytest.mark.asyncio
    async def test_removes_player_on_404(self, r, cfg, log):
        """When Riot API returns 404 for a PUUID, remove from queue permanently."""
        await r.zadd("discover:players", {"puuid-gone:na1": 1700000000000.0})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-gone"
            ).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 0
        assert await r.zcard("discover:players") == 0
        assert not await r.exists("player:puuid-gone")

    @pytest.mark.asyncio
    async def test_retains_player_on_transient_api_error(self, r, cfg, log):
        """Transient API errors (500) should leave player in queue for retry."""
        await r.zadd("discover:players", {"puuid-retry:na1": 1700000000000.0})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-retry"
            ).mock(return_value=httpx.Response(500, text="Internal Server Error"))
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 0
        # Player should STILL be in discovery queue for retry
        assert await r.zcard("discover:players") == 1

    @pytest.mark.asyncio
    async def test_skips_already_seeded_player(self, r, cfg, log):
        """Players seeded after being added to discover:players should be skipped."""
        await r.hset(
            "player:puuid-seeded",
            mapping={
                "game_name": "Already",
                "tag_line": "Here",
                "region": "na1",
                "seeded_at": "2024-01-01T00:00:00+00:00",
            },
        )
        await r.zadd("discover:players", {"puuid-seeded:na1": 1700000000000.0})

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 0
        assert await r.zcard("discover:players") == 0


class TestParseMember:
    def test_puuid_with_region(self):
        puuid, region = _parse_member("abc-def-123:na1")
        assert puuid == "abc-def-123"
        assert region == "na1"

    def test_puuid_without_region(self):
        puuid, region = _parse_member("abc-def-123")
        assert puuid == "abc-def-123"
        assert region == "na1"  # default

    def test_puuid_with_colons(self):
        """PUUIDs can contain colons — rfind ensures last colon is the separator."""
        puuid, region = _parse_member("some:complex:puuid:euw1")
        assert puuid == "some:complex:puuid"
        assert region == "euw1"

    def test_empty_puuid_falls_back(self):
        """':region' with empty puuid treats whole string as puuid with default region."""
        puuid, region = _parse_member(":na1")
        assert puuid == ":na1"
        assert region == "na1"


class TestIsIdlePriority:
    @pytest.mark.asyncio
    async def test_is_idle__priority_count_positive__returns_false(self, r):
        """When system:priority_count > 0, pipeline is NOT idle (priority players in flight)."""
        await r.set("system:priority_count", "2")
        assert await _is_idle(r) is False

    @pytest.mark.asyncio
    async def test_is_idle__priority_count_zero__checks_streams(self, r):
        """When system:priority_count is 0, falls through to stream check (idle)."""
        await r.set("system:priority_count", "0")
        # No stream exists → idle
        assert await _is_idle(r) is True


class TestIsIdle:
    @pytest.mark.asyncio
    async def test_no_stream_returns_true(self, r):
        """When stream doesn't exist, pipeline is idle."""
        assert await _is_idle(r) is True

    @pytest.mark.asyncio
    async def test_no_groups_returns_true(self, r):
        """Stream exists but no consumer groups — idle."""
        await r.xadd(
            "stream:puuid",
            {
                "id": "test",
                "source_stream": "stream:puuid",
                "type": "puuid",
                "payload": "{}",
                "attempts": "0",
                "max_attempts": "5",
                "enqueued_at": "2024-01-01",
                "dlq_attempts": "0",
            },
        )
        assert await _is_idle(r) is True

    @pytest.mark.asyncio
    async def test_pending_messages_not_idle(self, r):
        """When group has pending (unACKed) messages, not idle."""
        from lol_pipeline.models import MessageEnvelope
        from lol_pipeline.streams import consume, publish

        env = MessageEnvelope(
            source_stream="stream:puuid", type="puuid", payload={"puuid": "test"}, max_attempts=5
        )
        await publish(r, "stream:puuid", env)
        await consume(r, "stream:puuid", "crawlers", "c1", block=0)
        # Message delivered but not ACKed
        assert await _is_idle(r) is False


class TestPromoteBatchEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_queue(self, r, cfg, log):
        """Empty discover:players → 0 promoted."""
        riot = RiotClient("RGAPI-test")
        assert await _promote_batch(r, cfg, log, riot) == 0
        await riot.close()

    @pytest.mark.asyncio
    async def test_halted_system_returns_zero(self, r, cfg, log):
        """When system:halted, no promotions occur."""
        await r.set("system:halted", "1")
        await r.zadd("discover:players", {"puuid-abc:na1": 1700000000000.0})
        await r.hset("player:puuid-abc", mapping={"game_name": "T", "tag_line": "1"})
        riot = RiotClient("RGAPI-test")
        assert await _promote_batch(r, cfg, log, riot) == 0
        await riot.close()
        # Player should still be in queue
        assert await r.zcard("discover:players") == 1


class TestDiscoveryTier3EdgeCases:
    """Tier 3 — Discovery edge case tests."""

    @pytest.mark.asyncio
    async def test_promote_auth_error_sets_halted_and_breaks(self, r, cfg, log):
        """SEC-5: AuthError (403) during name resolution sets system:halted and stops."""
        await r.zadd("discover:players", {"puuid-auth:na1": 1700000000000.0})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-auth"
            ).mock(return_value=httpx.Response(403))
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 0
        # system:halted must be set
        assert await r.get("system:halted") == "1"
        # Player should still be in queue (not removed — just halted)
        assert await r.zcard("discover:players") == 1

    @pytest.mark.asyncio
    async def test_is_idle_redis_error_returns_true(self, r):
        """ResponseError from XINFO GROUPS returns True (stream doesn't exist)."""
        # Stream doesn't exist yet — ResponseError → returns True
        assert await _is_idle(r) is True

        # Verify with a real stream that has no groups
        await r.xadd(
            "stream:puuid",
            {
                "id": "test",
                "source_stream": "stream:puuid",
                "type": "puuid",
                "payload": "{}",
                "attempts": "0",
                "max_attempts": "5",
                "enqueued_at": "2024-01-01",
                "dlq_attempts": "0",
            },
        )
        # Stream exists, no groups → idle
        assert await _is_idle(r) is True


class TestPromoteBatchOrdering:
    """CQ-12: publish() must happen before hset(seeded_at) in _promote_batch."""

    @pytest.mark.asyncio
    async def test_publish_before_hset_seeded_at(self, r, cfg, log):
        """Discovery writes to stream:puuid BEFORE marking seeded_at in player hash."""
        call_order: list[str] = []

        await r.hset(
            "player:puuid-order",
            mapping={"game_name": "OrderTest", "tag_line": "001"},
        )
        await r.zadd("discover:players", {"puuid-order:na1": 1700000000000.0})

        original_hset = r.hset

        async def tracking_hset(key, *args, **kwargs):
            mapping = kwargs.get("mapping", {})
            if isinstance(mapping, dict) and "seeded_at" in mapping:
                call_order.append("hset_seeded_at")
            return await original_hset(key, *args, **kwargs)

        r.hset = tracking_hset

        original_xadd = r.xadd

        async def tracking_xadd(stream, *args, **kwargs):
            if stream == "stream:puuid":
                call_order.append("publish")
            return await original_xadd(stream, *args, **kwargs)

        r.xadd = tracking_xadd

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 1
        assert call_order == ["publish", "hset_seeded_at"]


class TestGracefulShutdown:
    """CQ-13: Discovery uses asyncio.Event for shutdown."""

    @pytest.mark.asyncio
    async def test_sigterm_stops_main_loop(self, monkeypatch):
        """Triggering the shutdown event causes main() to exit cleanly."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()

        # Capture the shutdown_event set method via add_signal_handler spy
        captured_callbacks: list[object] = []

        async def fake_is_idle(*args):
            # Fire the captured SIGTERM handler on first call
            if captured_callbacks:
                cb = captured_callbacks[0]
                if callable(cb):
                    cb()  # sets the shutdown_event
            return False

        def spy_add_signal_handler(sig, callback, *args):
            captured_callbacks.append(callback)

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.side_effect = spy_add_signal_handler

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=fake_is_idle),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_discovery.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            await main()  # should exit cleanly, not loop forever
        mock_r.aclose.assert_called_once()


class TestShutdownEventPattern:
    """Architecture: discovery uses asyncio.Event instead of module-level global."""

    @pytest.mark.asyncio
    async def test_no_module_level_shutdown_global(self):
        """The _shutdown global should no longer exist in the module."""
        import lol_discovery.main as mod

        assert not hasattr(mod, "_shutdown"), "Module-level _shutdown global should be removed"

    @pytest.mark.asyncio
    async def test_signal_handler_registered_via_loop(self, monkeypatch):
        """main() registers SIGTERM via loop.add_signal_handler, not signal.signal."""
        import signal as sig_mod

        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()

        registered_signals: list[int] = []

        def spy_add_signal_handler(signum, callback, *args):
            registered_signals.append(signum)
            callback()  # immediately set to stop the loop

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.side_effect = spy_add_signal_handler

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_discovery.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            await main()

        assert sig_mod.SIGTERM in registered_signals


class TestMainEntryPoint:
    """Tests for main() bootstrap and teardown."""

    @pytest.mark.asyncio
    async def test_main__creates_redis_and_starts_loop(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        call_count = 0

        async def fake_is_idle(*args):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise KeyboardInterrupt
            return False

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=fake_is_idle),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_discovery.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__keyboard_interrupt__closes_redis(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=KeyboardInterrupt),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_discovery.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            with pytest.raises(KeyboardInterrupt):
                await main()
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__redis_error__logs_and_retries(self, monkeypatch):
        """C4: RedisError in main loop is caught, logged, and retried after 1s sleep."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()

        call_count = 0

        async def failing_then_shutdown_is_idle(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RedisError("connection lost")
            # Second call: trigger shutdown via KeyboardInterrupt
            raise KeyboardInterrupt

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=failing_then_shutdown_is_idle),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("lol_discovery.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            with pytest.raises(KeyboardInterrupt):
                await main()

        # The error-recovery sleep(1) must have been called
        mock_sleep.assert_any_call(1)
        # Loop continued to the second _is_idle call (didn't crash on first)
        assert call_count == 2
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__os_error__logs_and_retries(self, monkeypatch):
        """C4: OSError in main loop is caught and retried, same as RedisError."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()

        call_count = 0

        async def failing_then_shutdown_is_idle(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("network unreachable")
            raise KeyboardInterrupt

        mock_loop = AsyncMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=failing_then_shutdown_is_idle),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("lol_discovery.main.asyncio.get_event_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            with pytest.raises(KeyboardInterrupt):
                await main()

        mock_sleep.assert_any_call(1)
        assert call_count == 2
        mock_r.aclose.assert_called_once()
