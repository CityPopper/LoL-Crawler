"""Unit tests for lol_discovery.main — player promotion with name resolution."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import httpx
import pytest
import respx
from lol_pipeline.config import Config
from lol_pipeline.priority import set_priority
from lol_pipeline.riot_api import RiotClient
from redis.exceptions import RedisError, ResponseError

from lol_discovery.main import (
    _PIPELINE_STREAMS,
    _THROTTLED,
    _is_idle,
    _parse_member,
    _promote_batch,
    _resolve_names,
    main,
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


class TestResolveNamesMissingFields:
    """B2: Deleted/banned accounts may return 200 with missing gameName/tagLine."""

    @pytest.mark.asyncio
    async def test_resolve_names__missing_game_name__returns_none(self, r, log):
        """API returns 200 but no gameName field — should return None, not crash."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-banned"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid-banned", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            result = await _resolve_names(r, riot, "puuid-banned", "na1", log)
            await riot.close()
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_names__missing_tag_line__returns_none(self, r, log):
        """API returns 200 but no tagLine field — should return None, not crash."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-notagline"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid-notagline", "gameName": "SomeName"},
                )
            )
            riot = RiotClient("RGAPI-test")
            result = await _resolve_names(r, riot, "puuid-notagline", "na1", log)
            await riot.close()
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_names__both_fields_missing__returns_none(self, r, log):
        """API returns 200 but neither gameName nor tagLine — should return None."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-empty"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid-empty"},
                )
            )
            riot = RiotClient("RGAPI-test")
            result = await _resolve_names(r, riot, "puuid-empty", "na1", log)
            await riot.close()
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_names__empty_game_name__returns_none(self, r, log):
        """API returns 200 with empty string gameName — should return None."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-emptyname"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid-emptyname", "gameName": "", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            result = await _resolve_names(r, riot, "puuid-emptyname", "na1", log)
            await riot.close()
        assert result is None

    @pytest.mark.asyncio
    async def test_promote_batch__missing_names_removes_player(self, r, cfg, log):
        """B2: Player with missing gameName/tagLine is removed from queue, not crash-looped."""
        await r.zadd("discover:players", {"puuid-deleted:na1": 1700000000000.0})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-deleted"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid-deleted"},
                )
            )
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 0
        # Player must be removed from queue — no infinite retry loop
        assert await r.zcard("discover:players") == 0


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
        puuid, region = _parse_member("abc-def-123:na1", default_region="na1")
        assert puuid == "abc-def-123"
        assert region == "na1"

    def test_puuid_without_region(self):
        puuid, region = _parse_member("abc-def-123", default_region="na1")
        assert puuid == "abc-def-123"
        assert region == "na1"  # default

    def test_puuid_with_colons(self):
        """PUUIDs can contain colons — rfind ensures last colon is the separator."""
        puuid, region = _parse_member("some:complex:puuid:euw1", default_region="na1")
        assert puuid == "some:complex:puuid"
        assert region == "euw1"

    def test_empty_puuid_falls_back(self):
        """':region' with empty puuid treats whole string as puuid with default region."""
        puuid, region = _parse_member(":na1", default_region="na1")
        assert puuid == ":na1"
        assert region == "na1"


class TestIsIdlePriority:
    @pytest.mark.asyncio
    async def test_is_idle__priority_keys_exist__returns_false(self, r):
        """When player:priority:* keys exist, pipeline is NOT idle."""
        await set_priority(r, "puuid-1")
        await set_priority(r, "puuid-2")
        assert await _is_idle(r) is False

    @pytest.mark.asyncio
    async def test_is_idle__no_priority_keys__checks_streams(self, r):
        """When no player:priority:* keys exist, falls through to stream check (idle)."""
        # No priority keys, no streams → idle
        assert await _is_idle(r) is True

    @pytest.mark.asyncio
    async def test_is_idle__stale_counter_ignored(self, r):
        """A stale system:priority_count key does NOT block idle detection."""
        await r.set("system:priority_count", "99")
        # No actual player:priority:* keys → should be idle
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


class TestIsIdleAllStreams:
    """I2-C2: _is_idle checks ALL pipeline streams, not just stream:puuid."""

    @pytest.mark.asyncio
    async def test_is_idle__all_streams_empty__returns_true(self, r):
        """When no pipeline streams exist, all are treated as idle."""
        assert await _is_idle(r) is True

    @pytest.mark.asyncio
    async def test_is_idle__all_streams_drained__returns_true(self, r):
        """When all streams exist with consumer groups but 0 pending/lag, idle."""
        from lol_pipeline.models import MessageEnvelope
        from lol_pipeline.streams import ack, consume, publish

        groups = ["crawlers", "fetchers", "parsers", "analyzers", "recovery"]
        for stream, group in zip(_PIPELINE_STREAMS, groups, strict=True):
            env = MessageEnvelope(source_stream=stream, type="puuid", payload={}, max_attempts=5)
            await publish(r, stream, env)
            msgs = await consume(r, stream, group, "w1", block=0)
            assert msgs
            for msg_id, _env in msgs:
                await ack(r, stream, group, msg_id)

        assert await _is_idle(r) is True

    @pytest.mark.asyncio
    async def test_is_idle__match_id_stream_pending__returns_false(self, r):
        """When stream:match_id has pending messages, pipeline is NOT idle."""
        from lol_pipeline.models import MessageEnvelope
        from lol_pipeline.streams import consume, publish

        env = MessageEnvelope(
            source_stream="stream:match_id",
            type="match_id",
            payload={"match_id": "NA1_123"},
            max_attempts=5,
        )
        await publish(r, "stream:match_id", env)
        await consume(r, "stream:match_id", "fetchers", "f1", block=0)
        # Delivered but NOT acked
        assert await _is_idle(r) is False

    @pytest.mark.asyncio
    async def test_is_idle__parse_stream_pending__returns_false(self, r):
        """When stream:parse has pending messages, pipeline is NOT idle."""
        from lol_pipeline.models import MessageEnvelope
        from lol_pipeline.streams import consume, publish

        env = MessageEnvelope(
            source_stream="stream:parse",
            type="parse",
            payload={"match_id": "NA1_456"},
            max_attempts=5,
        )
        await publish(r, "stream:parse", env)
        await consume(r, "stream:parse", "parsers", "p1", block=0)
        assert await _is_idle(r) is False

    @pytest.mark.asyncio
    async def test_is_idle__analyze_stream_pending__returns_false(self, r):
        """When stream:analyze has pending messages, pipeline is NOT idle."""
        from lol_pipeline.models import MessageEnvelope
        from lol_pipeline.streams import consume, publish

        env = MessageEnvelope(
            source_stream="stream:analyze",
            type="analyze",
            payload={"puuid": "abc"},
            max_attempts=5,
        )
        await publish(r, "stream:analyze", env)
        await consume(r, "stream:analyze", "analyzers", "a1", block=0)
        assert await _is_idle(r) is False

    @pytest.mark.asyncio
    async def test_is_idle__missing_stream_treated_as_idle(self, r):
        """Streams that don't exist yet are treated as idle (ResponseError handled)."""
        from lol_pipeline.models import MessageEnvelope
        from lol_pipeline.streams import ack, consume, publish

        # Only create stream:puuid — the other 3 don't exist
        env = MessageEnvelope(
            source_stream="stream:puuid", type="puuid", payload={}, max_attempts=5
        )
        await publish(r, "stream:puuid", env)
        msgs = await consume(r, "stream:puuid", "crawlers", "c1", block=0)
        for msg_id, _env in msgs:
            await ack(r, "stream:puuid", "crawlers", msg_id)

        # stream:puuid is drained, others don't exist — should be idle
        assert await _is_idle(r) is True

    @pytest.mark.asyncio
    async def test_is_idle__checks_all_pipeline_streams(self, r):
        """Verify _PIPELINE_STREAMS contains all expected streams including DLQ."""
        assert set(_PIPELINE_STREAMS) == {
            "stream:puuid",
            "stream:match_id",
            "stream:parse",
            "stream:analyze",
            "stream:dlq",
        }

    @pytest.mark.asyncio
    async def test_is_idle__puuid_idle_but_analyze_busy__returns_false(self, r):
        """Even if stream:puuid is idle, busy downstream streams block promotion."""
        from lol_pipeline.models import MessageEnvelope
        from lol_pipeline.streams import ack, consume, publish

        # Drain stream:puuid
        env1 = MessageEnvelope(
            source_stream="stream:puuid", type="puuid", payload={}, max_attempts=5
        )
        await publish(r, "stream:puuid", env1)
        msgs = await consume(r, "stream:puuid", "crawlers", "c1", block=0)
        for msg_id, _env in msgs:
            await ack(r, "stream:puuid", "crawlers", msg_id)

        # Leave stream:analyze with pending work
        env2 = MessageEnvelope(
            source_stream="stream:analyze",
            type="analyze",
            payload={"puuid": "busy"},
            max_attempts=5,
        )
        await publish(r, "stream:analyze", env2)
        await consume(r, "stream:analyze", "analyzers", "a1", block=0)

        assert await _is_idle(r) is False


class TestIsIdleNoneLagPending:
    """P11-PM-03-A: XINFO GROUPS can return lag=None or pending=None."""

    @pytest.mark.asyncio
    async def test_is_idle__lag_none__returns_true(self, r):
        """When XINFO GROUPS returns lag=None (empty stream), _is_idle returns True."""
        mock_groups = [
            {
                "pending": None,
                "lag": None,
                "name": "grp",
                "consumers": 0,
                "last-delivered-id": "0-0",
            }
        ]
        with patch.object(r, "xinfo_groups", new_callable=AsyncMock, return_value=mock_groups):
            result = await _is_idle(r)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_idle__lag_none_pending_nonzero__returns_false(self, r):
        """When lag=None but pending > 0, pipeline is not idle."""
        mock_groups = [
            {"pending": 3, "lag": None, "name": "grp", "consumers": 1, "last-delivered-id": "1-0"}
        ]
        with patch.object(r, "xinfo_groups", new_callable=AsyncMock, return_value=mock_groups):
            result = await _is_idle(r)
        assert result is False


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


class TestIsIdleNarrowResponseError:
    """P14-CR-6: _is_idle only catches NOGROUP ResponseError, re-raises others."""

    @pytest.mark.asyncio
    async def test_nogroup_error_treated_as_idle(self, r):
        """ResponseError containing 'NOGROUP' is caught and stream treated as idle."""
        with patch.object(
            r,
            "xinfo_groups",
            new_callable=AsyncMock,
            side_effect=ResponseError("ERR no such key or NOGROUP"),
        ):
            result = await _is_idle(r)
        assert result is True

    @pytest.mark.asyncio
    async def test_non_nogroup_error_is_reraised(self, r):
        """ResponseError NOT containing 'NOGROUP' is re-raised, not swallowed."""
        with patch.object(
            r,
            "xinfo_groups",
            new_callable=AsyncMock,
            side_effect=ResponseError(
                "WRONGTYPE Operation against a key holding the wrong kind of value"
            ),
        ):
            with pytest.raises(ResponseError, match="WRONGTYPE"):
                await _is_idle(r)

    @pytest.mark.asyncio
    async def test_is_idle__no_such_key_error__treats_stream_as_idle(self, r):
        """'no such key' ResponseError (without NOGROUP) is also treated as idle."""
        with patch.object(
            r,
            "xinfo_groups",
            new_callable=AsyncMock,
            side_effect=ResponseError("ERR no such key"),
        ):
            result = await _is_idle(r)
        assert result is True


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


class TestPromoteBatchAtomicCleanup:
    """I2-H12: ZREM + HSET after publish must be atomic (pipeline transaction)."""

    @pytest.mark.asyncio
    async def test_zrem_and_hset_are_atomic(self, r, cfg, log):
        """Post-publish cleanup (ZREM + HSET) executes in a single Redis pipeline."""
        await r.hset(
            "player:puuid-atomic",
            mapping={"game_name": "AtomicPlayer", "tag_line": "001"},
        )
        await r.zadd("discover:players", {"puuid-atomic:na1": 1700000000000.0})

        pipeline_calls: list[list[str]] = []
        original_pipeline = r.pipeline

        def tracking_pipeline(**kwargs):
            pipe = original_pipeline(**kwargs)
            original_execute = pipe.execute
            ops: list[str] = []

            original_zrem = pipe.zrem
            original_hset = pipe.hset

            def track_zrem(*args, **kw):
                ops.append("zrem")
                return original_zrem(*args, **kw)

            def track_hset(*args, **kw):
                ops.append("hset")
                return original_hset(*args, **kw)

            async def tracking_execute(*args, **kw):
                pipeline_calls.append(list(ops))
                return await original_execute(*args, **kw)

            pipe.zrem = track_zrem
            pipe.hset = track_hset
            pipe.execute = tracking_execute
            return pipe

        r.pipeline = tracking_pipeline

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 1
        # Verify a pipeline was used containing both ZREM and HSET
        atomic_batches = [ops for ops in pipeline_calls if "zrem" in ops and "hset" in ops]
        assert len(atomic_batches) == 1, (
            f"Expected exactly one pipeline with both ZREM and HSET, got: {pipeline_calls}"
        )
        # I2-H12: HSET before ZREM — if crash after HSET but before ZREM,
        # the seeded_at check on next batch cycle skips and cleans up the member.
        assert atomic_batches[0] == ["hset", "zrem"]


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

        original_xadd = r.xadd

        async def tracking_xadd(stream, *args, **kwargs):
            if stream == "stream:puuid":
                call_order.append("publish")
            return await original_xadd(stream, *args, **kwargs)

        r.xadd = tracking_xadd

        # Wrap r.pipeline to intercept pipe.hset inside the transaction
        original_pipeline = r.pipeline

        def tracking_pipeline(**kwargs):
            pipe = original_pipeline(**kwargs)
            original_pipe_hset = pipe.hset

            def tracked_pipe_hset(*args, **kw):
                mapping = kw.get("mapping", {})
                if isinstance(mapping, dict) and "seeded_at" in mapping:
                    call_order.append("hset_seeded_at")
                return original_pipe_hset(*args, **kw)

            pipe.hset = tracked_pipe_hset
            return pipe

        r.pipeline = tracking_pipeline

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

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.side_effect = spy_add_signal_handler

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=fake_is_idle),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_discovery.main.asyncio.get_running_loop", return_value=mock_loop),
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

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.side_effect = spy_add_signal_handler

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_discovery.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            await main()

        assert sig_mod.SIGTERM in registered_signals


class TestMainEntryPoint:
    """Tests for main() bootstrap and teardown."""

    @staticmethod
    def _mock_redis_not_halted() -> AsyncMock:
        """Return an AsyncMock Redis that reports system:halted as unset."""
        mock_r = AsyncMock()
        mock_r.get = AsyncMock(return_value=None)
        return mock_r

    @pytest.mark.asyncio
    async def test_main__creates_redis_and_starts_loop(self, monkeypatch):
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = self._mock_redis_not_halted()
        call_count = 0

        async def fake_is_idle(*args):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise KeyboardInterrupt
            return False

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=fake_is_idle),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_discovery.main.asyncio.get_running_loop", return_value=mock_loop),
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
        mock_r = self._mock_redis_not_halted()

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=KeyboardInterrupt),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_discovery.main.asyncio.get_running_loop", return_value=mock_loop),
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
        mock_r = self._mock_redis_not_halted()

        call_count = 0

        async def failing_then_shutdown_is_idle(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RedisError("connection lost")
            # Second call: trigger shutdown via KeyboardInterrupt
            raise KeyboardInterrupt

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=failing_then_shutdown_is_idle),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("lol_discovery.main.asyncio.get_running_loop", return_value=mock_loop),
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
        mock_r = self._mock_redis_not_halted()

        call_count = 0

        async def failing_then_shutdown_is_idle(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("network unreachable")
            raise KeyboardInterrupt

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=failing_then_shutdown_is_idle),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("lol_discovery.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            with pytest.raises(KeyboardInterrupt):
                await main()

        mock_sleep.assert_any_call(1)
        assert call_count == 2
        mock_r.aclose.assert_called_once()


class TestIsIdleDlqAndDelayed:
    """_is_idle must also check stream:dlq and delayed:messages.

    Discovery should not promote when DLQ retries or delayed messages are
    in-flight, even if the four main pipeline streams appear drained.
    """

    @pytest.mark.asyncio
    async def test_is_idle__dlq_stream_pending__returns_false(self, r):
        """When stream:dlq has pending messages, pipeline is NOT idle."""
        from lol_pipeline.models import MessageEnvelope
        from lol_pipeline.streams import consume, publish

        env = MessageEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_999"},
            max_attempts=5,
        )
        await publish(r, "stream:dlq", env)
        await consume(r, "stream:dlq", "recovery", "r1", block=0)
        # DLQ message delivered but not ACKed
        assert await _is_idle(r) is False

    @pytest.mark.asyncio
    async def test_is_idle__dlq_stream_drained__returns_true(self, r):
        """When stream:dlq exists but is fully drained, pipeline is idle."""
        from lol_pipeline.models import MessageEnvelope
        from lol_pipeline.streams import ack, consume, publish

        env = MessageEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_999"},
            max_attempts=5,
        )
        await publish(r, "stream:dlq", env)
        msgs = await consume(r, "stream:dlq", "recovery", "r1", block=0)
        for msg_id, _env in msgs:
            await ack(r, "stream:dlq", "recovery", msg_id)
        assert await _is_idle(r) is True

    @pytest.mark.asyncio
    async def test_is_idle__delayed_messages_nonempty__returns_false(self, r):
        """When delayed:messages ZSET has entries, pipeline is NOT idle."""
        await r.zadd("delayed:messages", {"some-envelope-json": 1700000000000.0})
        assert await _is_idle(r) is False

    @pytest.mark.asyncio
    async def test_is_idle__delayed_messages_empty__returns_true(self, r):
        """When delayed:messages ZSET is empty, that check passes."""
        # No delayed:messages key at all
        assert await _is_idle(r) is True

    @pytest.mark.asyncio
    async def test_is_idle__delayed_messages_cleared__returns_true(self, r):
        """After all delayed:messages are processed (ZSET empty), idle is True."""
        await r.zadd("delayed:messages", {"msg": 100.0})
        assert await _is_idle(r) is False
        await r.zrem("delayed:messages", "msg")
        assert await _is_idle(r) is True


class TestIsIdleNoXlenCheck:
    """V16-3: _is_idle uses only XINFO GROUPS, not XLEN backlog threshold."""

    @pytest.mark.asyncio
    async def test_is_idle__many_messages_no_consumer_groups__returns_true(self, r):
        """Streams with many messages but no consumer groups are treated as idle.

        Layer 1 (XLEN threshold) was removed because stream:match_id has no
        MAXLEN trimming, so XLEN grows monotonically and permanently blocks
        Discovery after ~8000 cumulative match IDs.
        """
        for i in range(100):
            await r.xadd(
                "stream:match_id",
                {
                    "id": f"msg-{i}",
                    "source_stream": "stream:match_id",
                    "type": "match_id",
                    "payload": "{}",
                    "attempts": "0",
                    "max_attempts": "5",
                    "enqueued_at": "2024-01-01",
                    "dlq_attempts": "0",
                },
            )
        # No consumer groups → idle regardless of XLEN
        assert await _is_idle(r) is True


class TestHaltExitsLoop:
    """I2-M5: Discovery exits polling loop when system:halted is set."""

    @pytest.mark.asyncio
    async def test_main__halted__exits_cleanly(self, monkeypatch):
        """When system:halted is set, main() breaks out of loop and shuts down."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        mock_r.get = AsyncMock(return_value="1")  # system:halted = "1"

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        is_idle_called = False

        async def should_not_be_called(*args):
            nonlocal is_idle_called
            is_idle_called = True
            return False

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", side_effect=should_not_be_called),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock),
            patch("lol_discovery.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            await main()  # should exit cleanly, not loop forever

        # Must NOT have called _is_idle — halted check should break before that
        assert not is_idle_called
        mock_r.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_main__halted__does_not_sleep_loop(self, monkeypatch):
        """Halted system exits immediately, not sleep(10)+continue like before."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_r = AsyncMock()
        mock_r.get = AsyncMock(return_value="1")

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient") as mock_riot,
            patch("lol_discovery.main._is_idle", new_callable=AsyncMock),
            patch("lol_discovery.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("lol_discovery.main.asyncio.get_running_loop", return_value=mock_loop),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            mock_riot.return_value = AsyncMock()
            await main()

        # Should NOT have called sleep(10) — the old behavior was sleep+continue
        for call in mock_sleep.call_args_list:
            assert call.args[0] != 10, "Should not sleep(10) when halted — must exit"
        mock_r.aclose.assert_called_once()


class TestPromoteBatchAtomicOrdering:
    """I2-H12: Promotion must be XADD-first, then HSET+ZREM in pipeline."""

    @pytest.mark.asyncio
    async def test_hset_before_zrem_in_pipeline(self, r, cfg, log):
        """HSET seeded_at executes before ZREM inside the pipeline.

        If crash after HSET but before ZREM, the seeded_at check on next
        batch cycle (hexists seeded_at) catches the player and cleans up.
        """
        await r.hset(
            "player:puuid-order2",
            mapping={"game_name": "OrderTest2", "tag_line": "002"},
        )
        await r.zadd("discover:players", {"puuid-order2:na1": 1700000000000.0})

        pipeline_ops: list[str] = []
        original_pipeline = r.pipeline

        def tracking_pipeline(**kwargs):
            pipe = original_pipeline(**kwargs)
            original_hset = pipe.hset
            original_zrem = pipe.zrem

            def track_hset(*args, **kw):
                pipeline_ops.append("hset")
                return original_hset(*args, **kw)

            def track_zrem(*args, **kw):
                pipeline_ops.append("zrem")
                return original_zrem(*args, **kw)

            pipe.hset = track_hset
            pipe.zrem = track_zrem
            return pipe

        r.pipeline = tracking_pipeline

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 1
        # HSET must come before ZREM in the pipeline
        hset_idx = pipeline_ops.index("hset")
        zrem_idx = pipeline_ops.index("zrem")
        assert hset_idx < zrem_idx, (
            f"HSET must execute before ZREM in pipeline, got: {pipeline_ops}"
        )

    @pytest.mark.asyncio
    async def test_xadd_before_transactional_pipeline(self, r, cfg, log):
        """XADD (publish) must happen before the HSET+ZREM transactional pipeline.

        At-least-once guarantee: if crash after XADD but before pipeline,
        the player stays in discover:players and gets re-promoted.

        Note: a non-transactional pipeline for batched HEXISTS may run before
        XADD — only the transactional pipeline (transaction=True) must follow XADD.
        """
        await r.hset(
            "player:puuid-xadd-order",
            mapping={"game_name": "XaddOrder", "tag_line": "003"},
        )
        await r.zadd("discover:players", {"puuid-xadd-order:na1": 1700000000000.0})

        global_ops: list[str] = []
        original_xadd = r.xadd
        original_pipeline = r.pipeline

        async def tracking_xadd(stream, *args, **kwargs):
            if stream == "stream:puuid":
                global_ops.append("xadd")
            return await original_xadd(stream, *args, **kwargs)

        r.xadd = tracking_xadd

        def tracking_pipeline(**kwargs):
            pipe = original_pipeline(**kwargs)
            is_transaction = kwargs.get("transaction", True)
            original_execute = pipe.execute

            async def tracking_execute(*args, **kw):
                label = "tx_pipeline" if is_transaction else "check_pipeline"
                global_ops.append(label)
                return await original_execute(*args, **kw)

            pipe.execute = tracking_execute
            return pipe

        r.pipeline = tracking_pipeline

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 1
        # Batched HEXISTS check pipeline runs first, then XADD, then transactional
        assert global_ops == ["check_pipeline", "xadd", "tx_pipeline"]

    @pytest.mark.asyncio
    async def test_crash_after_xadd_leaves_player_in_discover(self, r, cfg, log):
        """Simulated crash after XADD: player stays in discover:players for re-promotion."""
        await r.hset(
            "player:puuid-crash",
            mapping={"game_name": "CrashTest", "tag_line": "004"},
        )
        await r.zadd("discover:players", {"puuid-crash:na1": 1700000000000.0})

        original_pipeline = r.pipeline

        def crashing_pipeline(**kwargs):
            pipe = original_pipeline(**kwargs)
            is_transaction = kwargs.get("transaction", True)
            if is_transaction:
                # Only crash the transactional pipeline (HSET+ZREM after XADD),
                # not the HEXISTS check pipeline (transaction=False).
                async def crash_execute(*args, **kw):
                    raise ConnectionError("simulated crash after XADD")

                pipe.execute = crash_execute
            return pipe

        r.pipeline = crashing_pipeline

        riot = RiotClient("RGAPI-test")
        with pytest.raises(ConnectionError, match="simulated crash"):
            await _promote_batch(r, cfg, log, riot)
        await riot.close()

        # XADD succeeded (message in stream), but player still in discover:players
        entries = await r.xrange("stream:puuid")
        assert len(entries) == 1
        assert await r.zscore("discover:players", "puuid-crash:na1") is not None
        # No seeded_at set (pipeline crashed before HSET)
        assert await r.hget("player:puuid-crash", "seeded_at") is None


class TestResolveNamesRateLimiting:
    """P10-CR-7: _resolve_names must call try_token before Riot API calls."""

    @pytest.mark.asyncio
    async def test_resolve_names__calls_try_token_before_api(self, r, cfg, log):
        """try_token is called before riot.get_account_by_puuid."""
        call_order: list[str] = []

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-ratelim"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "puuid": "puuid-ratelim",
                        "gameName": "RateLim",
                        "tagLine": "001",
                    },
                )
            )

            riot = RiotClient("RGAPI-test")

            original_get = riot.get_account_by_puuid

            async def tracking_get(*args, **kwargs):
                call_order.append("riot_api")
                return await original_get(*args, **kwargs)

            riot.get_account_by_puuid = tracking_get

            with patch("lol_discovery.main.try_token", new_callable=AsyncMock) as mock_tt:

                async def tracking_tt(*args, **kwargs):
                    call_order.append("try_token")
                    return True

                mock_tt.side_effect = tracking_tt

                result = await _resolve_names(r, riot, "puuid-ratelim", "na1", log)
            await riot.close()

        assert result == ("RateLim", "001")
        assert "try_token" in call_order, "try_token was never called"
        assert "riot_api" in call_order, "riot API was never called"
        tt_idx = call_order.index("try_token")
        riot_idx = call_order.index("riot_api")
        assert tt_idx < riot_idx, (
            f"try_token (idx={tt_idx}) must be called before riot API (idx={riot_idx})"
        )

    @pytest.mark.asyncio
    async def test_resolve_names__skips_try_token_when_cached(self, r, log):
        """When names are in Redis, no API call and no try_token needed."""
        await r.hset(
            "player:puuid-cached",
            mapping={"game_name": "Cached", "tag_line": "001"},
        )
        riot = RiotClient("RGAPI-test")

        with patch("lol_discovery.main.try_token", new_callable=AsyncMock) as mock_tt:
            result = await _resolve_names(r, riot, "puuid-cached", "na1", log)
        await riot.close()

        assert result == ("Cached", "001")
        mock_tt.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_names__try_token_receives_source_and_endpoint(self, r, log):
        """try_token is called with source='riot' and endpoint='account'."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-args"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "puuid": "puuid-args",
                        "gameName": "ArgsTest",
                        "tagLine": "002",
                    },
                )
            )
            riot = RiotClient("RGAPI-test")

            with patch("lol_discovery.main.try_token", new_callable=AsyncMock) as mock_tt:
                mock_tt.return_value = True
                await _resolve_names(r, riot, "puuid-args", "na1", log)
            await riot.close()

        mock_tt.assert_called_once_with("riot", "account")


class TestPromoteBatchStaleCleanup:
    """P16-DB-2: _promote_batch trims stale entries from players:all."""

    @pytest.mark.asyncio
    async def test_promote__trims_stale_players_all_entries(self, r, cfg, log):
        """Entries in players:all older than 30 days are removed."""
        import time as time_mod

        from lol_pipeline.constants import PLAYER_DATA_TTL_SECONDS

        now = time_mod.time()
        # Stale entry: 31 days old
        stale_score = now - PLAYER_DATA_TTL_SECONDS - 86400
        await r.zadd("players:all", {"puuid-stale": stale_score})
        # Fresh entry: 1 day old
        fresh_score = now - 86400
        await r.zadd("players:all", {"puuid-fresh": fresh_score})

        riot = RiotClient("RGAPI-test")
        await _promote_batch(r, cfg, log, riot)
        await riot.close()

        # Stale entry should be trimmed
        assert await r.zscore("players:all", "puuid-stale") is None
        # Fresh entry should remain
        assert await r.zscore("players:all", "puuid-fresh") is not None

    @pytest.mark.asyncio
    async def test_promote__no_stale_entries__no_removal(self, r, cfg, log):
        """When all entries are fresh, nothing is trimmed."""
        import time as time_mod

        await r.zadd("players:all", {"puuid-current": time_mod.time()})

        riot = RiotClient("RGAPI-test")
        await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert await r.zcard("players:all") == 1


class TestPromoteBatchPlayersAll:
    """Promotion adds player to the players:all sorted set."""

    @pytest.mark.asyncio
    async def test_promote__adds_to_players_all(self, r, cfg, log):
        """After promoting a player, players:all ZSET contains the PUUID."""
        await r.hset(
            "player:puuid-index",
            mapping={"game_name": "Indexed", "tag_line": "001"},
        )
        await r.zadd("discover:players", {"puuid-index:na1": 1700000000000.0})

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 1
        score = await r.zscore("players:all", "puuid-index")
        assert score is not None
        import time

        assert abs(score - time.time()) < 10

    @pytest.mark.asyncio
    async def test_promote__skipped_player_not_in_players_all(self, r, cfg, log):
        """Already-seeded players are skipped and NOT added to players:all."""
        await r.hset(
            "player:puuid-existing",
            mapping={
                "game_name": "Existing",
                "tag_line": "001",
                "seeded_at": "2024-01-01T00:00:00+00:00",
            },
        )
        await r.zadd("discover:players", {"puuid-existing:na1": 1700000000000.0})

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 0
        assert await r.zscore("players:all", "puuid-existing") is None


class TestPromoteBatchPlayerTTL:
    """P11-DB-2: _promote_batch sets 30-day TTL on player:{puuid} hashes."""

    @pytest.mark.asyncio
    async def test_promote_sets_player_ttl(self, r, cfg, log):
        """Promoted player:{puuid} hash has a positive TTL (30 days)."""
        await r.zadd("discover:players", {"puuid-ttltest:na1": 1700000000000.0})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-ttltest"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid-ttltest", "gameName": "TtlPlayer", "tagLine": "TTL"},
                )
            )
            riot = RiotClient("RGAPI-test")
            await _promote_batch(r, cfg, log, riot)
            await riot.close()

        ttl = await r.ttl("player:puuid-ttltest")
        assert ttl > 0, "player:{puuid} must have a TTL after promotion"

    @pytest.mark.asyncio
    async def test_promote_ttl_approx_30_days(self, r, cfg, log):
        """TTL is approximately 30 days (within 60s tolerance)."""
        await r.zadd("discover:players", {"puuid-ttl2:na1": 1700000000001.0})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-ttl2"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid-ttl2", "gameName": "AnotherPlayer", "tagLine": "AP"},
                )
            )
            riot = RiotClient("RGAPI-test")
            await _promote_batch(r, cfg, log, riot)
            await riot.close()

        ttl = await r.ttl("player:puuid-ttl2")
        # 30 days = 2592000s; allow for sub-second timing drift
        assert abs(ttl - 2592000) <= 60, f"Expected ~2592000s TTL, got {ttl}s"


class TestPromoteBatchPriority:
    """Discovery-promoted players get auto_20 priority tier."""

    @pytest.mark.asyncio
    async def test_promoted_envelope_has_auto_20_priority(self, r, cfg, log):
        """Promoted player envelope has priority='auto_20'."""
        await r.hset(
            "player:puuid-prio",
            mapping={"game_name": "PrioTest", "tag_line": "PT"},
        )
        await r.zadd("discover:players", {"puuid-prio:na1": 1700000000000.0})

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 1
        entries = await r.xrange("stream:puuid")
        assert len(entries) == 1
        from lol_pipeline.models import MessageEnvelope

        env = MessageEnvelope.from_redis_fields(entries[0][1])
        assert env.priority == "auto_20"


class TestRecrawlAfterScheduling:
    """Activity-rate re-crawl scheduling in Discovery."""

    @pytest.mark.asyncio
    async def test_already_seeded__recrawl_due__promotes(self, r, cfg, log):
        """Already-seeded player with recrawl_after in the past is re-promoted."""
        import time as time_mod

        puuid = "puuid-recrawl"
        # Set as previously seeded with recrawl_after in the past
        past_ts = str(time_mod.time() - 3600)  # 1 hour ago
        await r.hset(
            f"player:{puuid}",
            mapping={
                "game_name": "RecrawlPlayer",
                "tag_line": "RC1",
                "region": "na1",
                "seeded_at": "2024-01-01T00:00:00+00:00",
                "recrawl_after": past_ts,
            },
        )
        await r.zadd("discover:players", {f"{puuid}:na1": 1700000000000.0})

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        # Player should be re-promoted (recrawl_after has passed)
        assert promoted == 1
        assert await r.xlen("stream:puuid") == 1

    @pytest.mark.asyncio
    async def test_already_seeded__recrawl_not_due__skips(self, r, cfg, log):
        """Already-seeded player with recrawl_after in the future is NOT promoted."""
        import time as time_mod

        puuid = "puuid-notdue"
        future_ts = str(time_mod.time() + 7200)  # 2 hours from now
        await r.hset(
            f"player:{puuid}",
            mapping={
                "game_name": "NotDuePlayer",
                "tag_line": "ND1",
                "region": "na1",
                "seeded_at": "2024-01-01T00:00:00+00:00",
                "recrawl_after": future_ts,
            },
        )
        await r.zadd("discover:players", {f"{puuid}:na1": 1700000000000.0})

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        # Player should NOT be promoted (recrawl_after has not passed)
        assert promoted == 0
        assert await r.xlen("stream:puuid") == 0
        # Player kept in discover queue (not yet due — will be retried later)
        assert await r.zcard("discover:players") == 1

    @pytest.mark.asyncio
    async def test_already_seeded__no_recrawl_after__skips(self, r, cfg, log):
        """Already-seeded player without recrawl_after is skipped (existing behavior)."""
        puuid = "puuid-norecrawl"
        await r.hset(
            f"player:{puuid}",
            mapping={
                "game_name": "NoRecrawl",
                "tag_line": "NR1",
                "region": "na1",
                "seeded_at": "2024-01-01T00:00:00+00:00",
            },
        )
        await r.zadd("discover:players", {f"{puuid}:na1": 1700000000000.0})

        riot = RiotClient("RGAPI-test")
        promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 0
        assert await r.zcard("discover:players") == 0


class TestDiscoveryUsesIsSystemHalted:
    """DRY-5: Discovery uses is_system_halted() instead of raw r.get."""

    @pytest.mark.asyncio
    async def test_promote_batch__calls_is_system_halted(self, r, cfg, log):
        """_promote_batch uses is_system_halted() for halt check."""
        mock_halted = AsyncMock(return_value=True)
        with patch("lol_discovery.main.is_system_halted", mock_halted):
            result = await _promote_batch(r, cfg, log, RiotClient("RGAPI-test"))
        mock_halted.assert_called_once()
        assert result == 0

    @pytest.mark.asyncio
    async def test_main_loop__calls_is_system_halted(self, monkeypatch):
        """main() loop uses is_system_halted() for halt check."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")

        mock_halted = AsyncMock(return_value=True)
        mock_r = AsyncMock()
        mock_riot = AsyncMock()

        mock_loop = MagicMock()
        mock_loop.add_signal_handler.return_value = None

        with (
            patch("lol_discovery.main.Config") as mock_cfg,
            patch("lol_discovery.main.get_redis", return_value=mock_r),
            patch("lol_discovery.main.RiotClient", return_value=mock_riot),
            patch("lol_discovery.main.asyncio.get_running_loop", return_value=mock_loop),
            patch("lol_discovery.main.is_system_halted", mock_halted),
        ):
            mock_cfg.return_value = Config(_env_file=None)
            await main()
        mock_halted.assert_called()


class TestConfigValidationError:
    """E2: Missing env vars give actionable message, not raw pydantic traceback."""

    @pytest.mark.asyncio
    async def test_main__missing_config__exits_with_hint(self, monkeypatch, capsys):
        """Config() raises ValidationError → sys.exit(1) with .env.example hint."""
        monkeypatch.delenv("RIOT_API_KEY", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)
        from pydantic import ValidationError

        with (
            patch(
                "lol_discovery.main.Config",
                side_effect=ValidationError.from_exception_data(
                    title="Config",
                    line_errors=[],
                ),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            await main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert ".env.example" in captured.err or ".env.example" in captured.out


class TestPoisonedPuuidDeprioritized:
    """IMP-035: Score decay has a floor of 0; at 0 the member is removed."""

    @pytest.mark.asyncio
    async def test_riot_api_error_reduces_member_score(self, r, cfg, log):
        """Transient 500 error reduces discover:players score by 86_400_000."""
        initial_score = 1_700_000_000_000.0
        await r.zadd("discover:players", {"puuid-poison:na1": initial_score})

        with (
            respx.mock,
            patch("lol_discovery.main.try_token", new_callable=AsyncMock),
        ):
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-poison"
            ).mock(return_value=httpx.Response(500, text="Internal Server Error"))
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 0
        new_score = await r.zscore("discover:players", "puuid-poison:na1")
        assert new_score is not None
        assert new_score == initial_score - 86_400_000

    @pytest.mark.asyncio
    async def test_score_decay_has_floor_of_zero(self, r, cfg, log):
        """Score cannot go below 0 — at floor, member is removed from ZSET."""
        # Set score low enough that one decay would go negative
        await r.zadd("discover:players", {"puuid-floor:na1": 50_000.0})

        with (
            respx.mock,
            patch("lol_discovery.main.try_token", new_callable=AsyncMock),
        ):
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-floor"
            ).mock(return_value=httpx.Response(500, text="Internal Server Error"))
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 0
        # Member should be removed (score would have gone to 0)
        assert await r.zscore("discover:players", "puuid-floor:na1") is None
        assert await r.zcard("discover:players") == 0

    @pytest.mark.asyncio
    async def test_score_exactly_at_decay_boundary_removed(self, r, cfg, log):
        """Score exactly equal to 86_400_000 decays to 0 and member is removed."""
        await r.zadd("discover:players", {"puuid-exact:na1": 86_400_000.0})

        with (
            respx.mock,
            patch("lol_discovery.main.try_token", new_callable=AsyncMock),
        ):
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/puuid-exact"
            ).mock(return_value=httpx.Response(500, text="Internal Server Error"))
            riot = RiotClient("RGAPI-test")
            promoted = await _promote_batch(r, cfg, log, riot)
            await riot.close()

        assert promoted == 0
        # Score 86_400_000 - 86_400_000 = 0 → removed
        assert await r.zscore("discover:players", "puuid-exact:na1") is None


class TestResolveNamesThrottled:
    """IMP-069: try_token returning False skips gracefully instead of crashing."""

    @pytest.mark.asyncio
    async def test_resolve_names_throttled_skips_gracefully(self, r, log):
        """When try_token returns False, _resolve_names returns _THROTTLED without raising."""
        riot = RiotClient("RGAPI-test")
        with patch("lol_discovery.main.try_token", new_callable=AsyncMock) as mock_tt:
            mock_tt.return_value = False
            result = await _resolve_names(r, riot, "puuid-throttled", "na1", log)
        await riot.close()
        assert result is _THROTTLED

    @pytest.mark.asyncio
    async def test_resolve_names_throttled_member_stays_in_queue(self, r, cfg, log):
        """When throttled, the member is not promoted and not removed from queue."""
        await r.zadd("discover:players", {"puuid-throttled:na1": 1700000000000.0})
        riot = RiotClient("RGAPI-test")
        with patch("lol_discovery.main.try_token", new_callable=AsyncMock) as mock_tt:
            mock_tt.return_value = False
            promoted = await _promote_batch(r, cfg, log, riot)
        await riot.close()

        assert promoted == 0
        # Member must still be in the queue — not removed
        assert await r.zcard("discover:players") == 1
        score = await r.zscore("discover:players", "puuid-throttled:na1")
        assert score == 1700000000000.0
