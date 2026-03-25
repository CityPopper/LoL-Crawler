"""Unit tests for lol_pipeline.helpers — shared DRY utilities."""

from __future__ import annotations

from unittest.mock import patch

import fakeredis.aioredis
import pytest

from lol_pipeline.helpers import (
    _MAX_GAME_NAME_LEN,
    _MAX_TAG_LINE_LEN,
    consumer_id,
    is_system_halted,
    name_cache_key,
    validate_name_lengths,
)
from lol_pipeline.models import MessageEnvelope


class TestNameCacheKey:
    def test_basic_format(self):
        assert name_cache_key("Player", "NA1") == "player:name:player#na1"

    def test_lowercases_both_parts(self):
        assert name_cache_key("UPPER", "CASE") == "player:name:upper#case"

    def test_preserves_special_chars(self):
        assert name_cache_key("Foo Bar", "Tag") == "player:name:foo bar#tag"

    def test_empty_strings(self):
        assert name_cache_key("", "") == "player:name:#"

    def test_unicode_handling(self):
        result = name_cache_key("Player", "EUW1")
        assert result == "player:name:player#euw1"

    def test_truncates_oversized_game_name(self):
        """I2-H15: game_name exceeding 16 chars is truncated."""
        long_name = "A" * 32
        result = name_cache_key(long_name, "NA1")
        # game_name portion must be at most 16 chars (lowercased)
        assert result == f"player:name:{'a' * 16}#na1"

    def test_rejects_oversized_tag_line(self):
        """I2-H15: tag_line exceeding 16 chars raises ValueError."""
        long_tag = "B" * 32
        with pytest.raises(ValueError, match="tag_line exceeds maximum length"):
            name_cache_key("Player", long_tag)

    def test_rejects_oversized_game_name_over_64(self):
        """I2-H15: game_name exceeding 64 chars raises ValueError."""
        with pytest.raises(ValueError, match="game_name exceeds maximum length"):
            name_cache_key("X" * 100, "Y" * 5)

    def test_strips_null_bytes_from_game_name(self):
        """I2-H15: null bytes in game_name are stripped."""
        result = name_cache_key("Play\x00er", "NA1")
        assert result == "player:name:player#na1"

    def test_strips_null_bytes_from_tag_line(self):
        """I2-H15: null bytes in tag_line are stripped."""
        result = name_cache_key("Player", "N\x00A1")
        assert result == "player:name:player#na1"

    def test_strips_control_characters(self):
        """I2-H15: control characters (U+0000-U+001F, U+007F) are stripped."""
        result = name_cache_key("Pl\x01ay\x1fer", "\x7fNA1")
        assert result == "player:name:player#na1"

    def test_sanitize_then_truncate(self):
        """I2-H15: sanitization happens before truncation."""
        # 16 valid chars + null bytes scattered throughout
        name_with_nulls = "\x00A" * 16 + "B"  # 16 A's + 1 B after stripping
        result = name_cache_key(name_with_nulls, "NA1")
        # After stripping nulls: "A" * 16 + "B" = 17 chars → truncated to 16
        assert result == f"player:name:{'a' * 16}#na1"

    def test_exact_16_chars_not_truncated(self):
        """Names exactly at the 16-char limit pass through unchanged."""
        name = "A" * 16
        tag = "B" * 16
        result = name_cache_key(name, tag)
        assert result == f"player:name:{'a' * 16}#{'b' * 16}"


class TestValidateNameLengths:
    """I2-H15: validate_name_lengths guards against Redis key injection."""

    def test_valid_names_pass(self):
        """Normal-length names do not raise."""
        validate_name_lengths("Player", "NA1")  # no exception

    def test_game_name_at_limit_passes(self):
        """game_name exactly at 64 chars passes."""
        validate_name_lengths("A" * _MAX_GAME_NAME_LEN, "NA1")

    def test_tag_line_at_limit_passes(self):
        """tag_line exactly at 16 chars passes."""
        validate_name_lengths("Player", "B" * _MAX_TAG_LINE_LEN)

    def test_game_name_over_limit_raises(self):
        """game_name exceeding 64 chars raises ValueError with clear message."""
        with pytest.raises(ValueError, match=r"game_name exceeds maximum length \(65 > 64\)"):
            validate_name_lengths("A" * 65, "NA1")

    def test_tag_line_over_limit_raises(self):
        """tag_line exceeding 16 chars raises ValueError with clear message."""
        with pytest.raises(ValueError, match=r"tag_line exceeds maximum length \(17 > 16\)"):
            validate_name_lengths("Player", "B" * 17)

    def test_both_over_limit_raises_game_name_first(self):
        """When both exceed limits, game_name is checked first."""
        with pytest.raises(ValueError, match="game_name"):
            validate_name_lengths("A" * 100, "B" * 100)

    def test_empty_strings_pass(self):
        """Empty strings are valid (length 0 < limit)."""
        validate_name_lengths("", "")

    def test_game_name_just_over_limit(self):
        """Boundary: game_name at 65 chars raises."""
        with pytest.raises(ValueError, match="game_name"):
            validate_name_lengths("X" * (_MAX_GAME_NAME_LEN + 1), "NA1")

    def test_tag_line_just_over_limit(self):
        """Boundary: tag_line at 17 chars raises."""
        with pytest.raises(ValueError, match="tag_line"):
            validate_name_lengths("Player", "Y" * (_MAX_TAG_LINE_LEN + 1))

    def test_constants_match_expected_values(self):
        """Constants are set to the documented limits."""
        assert _MAX_GAME_NAME_LEN == 64
        assert _MAX_TAG_LINE_LEN == 16


class TestIsSystemHalted:
    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    @pytest.mark.asyncio
    async def test_returns_false_when_not_set(self, r):
        assert await is_system_halted(r) is False

    @pytest.mark.asyncio
    async def test_returns_true_when_set(self, r):
        await r.set("system:halted", "1")
        assert await is_system_halted(r) is True

    @pytest.mark.asyncio
    async def test_returns_false_after_delete(self, r):
        await r.set("system:halted", "1")
        await r.delete("system:halted")
        assert await is_system_halted(r) is False


class TestRegisterPlayer:
    """DRY-3: register_player writes player hash, TTL, players:all ZADD, and trim."""

    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    @pytest.mark.asyncio
    async def test_sets_player_hash_fields(self, r):
        """register_player writes game_name, tag_line, region, seeded_at to player:{puuid}."""
        from lol_pipeline.helpers import register_player

        await register_player(
            r,
            puuid="puuid-001",
            region="na1",
            game_name="Faker",
            tag_line="KR1",
            players_all_max=100,
        )
        assert await r.hget("player:puuid-001", "game_name") == "Faker"
        assert await r.hget("player:puuid-001", "tag_line") == "KR1"
        assert await r.hget("player:puuid-001", "region") == "na1"
        seeded_at = await r.hget("player:puuid-001", "seeded_at")
        assert seeded_at is not None

    @pytest.mark.asyncio
    async def test_sets_player_ttl(self, r):
        """register_player sets EXPIRE on player:{puuid}."""
        from lol_pipeline.helpers import register_player

        await register_player(
            r, puuid="puuid-002", region="euw1",
            game_name="Test", tag_line="EUW", players_all_max=100,
        )
        ttl = await r.ttl("player:puuid-002")
        assert ttl > 0

    @pytest.mark.asyncio
    async def test_adds_to_players_all(self, r):
        """register_player adds puuid to players:all ZSET."""
        from lol_pipeline.helpers import register_player

        await register_player(
            r, puuid="puuid-003", region="kr",
            game_name="A", tag_line="B", players_all_max=100,
        )
        score = await r.zscore("players:all", "puuid-003")
        assert score is not None

    @pytest.mark.asyncio
    async def test_trims_players_all(self, r):
        """register_player trims players:all to players_all_max entries."""
        from lol_pipeline.helpers import register_player

        # Pre-fill with 5 entries
        for i in range(5):
            await r.zadd("players:all", {f"old-{i}": float(i)})

        # Register with max=3 — should trim to 3 entries
        await register_player(
            r, puuid="puuid-new", region="na1",
            game_name="New", tag_line="NA1", players_all_max=3,
        )
        count = await r.zcard("players:all")
        assert count <= 3

    @pytest.mark.asyncio
    async def test_pipeline_batches_writes(self, r):
        """register_player uses a pipeline to batch all writes atomically."""
        from lol_pipeline.helpers import register_player

        # Smoke test — verifying it completes without error and all side effects occur
        await register_player(
            r, puuid="puuid-batch", region="na1",
            game_name="Batch", tag_line="T1", players_all_max=100,
        )
        assert await r.hget("player:puuid-batch", "game_name") == "Batch"
        assert await r.zscore("players:all", "puuid-batch") is not None
        assert await r.ttl("player:puuid-batch") > 0


class TestHandleRiotApiError:
    """DRY-6: handle_riot_api_error routes Riot API errors for consumers."""

    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    def _make_envelope(self) -> MessageEnvelope:
        return MessageEnvelope(
            source_stream="stream:test",
            type="test",
            payload={"key": "val"},
            max_attempts=5,
        )

    @pytest.mark.asyncio
    async def test_auth_error__sets_system_halted(self, r):
        """AuthError (403) sets system:halted and does NOT ack."""
        from lol_pipeline.helpers import handle_riot_api_error
        from lol_pipeline.riot_api import AuthError

        env = self._make_envelope()
        # Publish and consume so msg_id is in PEL
        from lol_pipeline.streams import ack, consume, publish

        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "test-group", "c1", block=0)
        msg_id = msgs[0][0]

        exc = AuthError("forbidden")
        result = await handle_riot_api_error(
            r, exc=exc, envelope=env, msg_id=msg_id,
            failed_by="test-svc", in_stream="stream:test", group="test-group",
        )
        assert await r.get("system:halted") == "1"
        # Should NOT ack — message stays in PEL
        assert result == "halted"

    @pytest.mark.asyncio
    async def test_not_found_error__acks(self, r):
        """NotFoundError (404) acks the message, returns 'discarded'."""
        from lol_pipeline.helpers import handle_riot_api_error
        from lol_pipeline.riot_api import NotFoundError
        from lol_pipeline.streams import consume, publish

        env = self._make_envelope()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "test-group", "c1", block=0)
        msg_id = msgs[0][0]

        exc = NotFoundError("not found")
        result = await handle_riot_api_error(
            r, exc=exc, envelope=env, msg_id=msg_id,
            failed_by="test-svc", in_stream="stream:test", group="test-group",
        )
        assert result == "discarded"
        # Message should be ACK'd
        pending = await r.xpending("stream:test", "test-group")
        assert pending["pending"] == 0

    @pytest.mark.asyncio
    async def test_rate_limit_error__nacks_to_dlq(self, r):
        """RateLimitError (429) nacks to DLQ with http_429 code and acks."""
        from lol_pipeline.helpers import handle_riot_api_error
        from lol_pipeline.riot_api import RateLimitError
        from lol_pipeline.streams import consume, publish

        env = self._make_envelope()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "test-group", "c1", block=0)
        msg_id = msgs[0][0]

        exc = RateLimitError(retry_after_ms=30000)
        result = await handle_riot_api_error(
            r, exc=exc, envelope=env, msg_id=msg_id,
            failed_by="test-svc", in_stream="stream:test", group="test-group",
        )
        assert result == "dlq"
        assert await r.xlen("stream:dlq") == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["failure_code"] == "http_429"
        assert entries[0][1]["failed_by"] == "test-svc"

    @pytest.mark.asyncio
    async def test_server_error__nacks_to_dlq(self, r):
        """ServerError (5xx) nacks to DLQ with http_5xx code and acks."""
        from lol_pipeline.helpers import handle_riot_api_error
        from lol_pipeline.riot_api import ServerError
        from lol_pipeline.streams import consume, publish

        env = self._make_envelope()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "test-group", "c1", block=0)
        msg_id = msgs[0][0]

        exc = ServerError("internal error", status_code=500)
        result = await handle_riot_api_error(
            r, exc=exc, envelope=env, msg_id=msg_id,
            failed_by="test-svc", in_stream="stream:test", group="test-group",
        )
        assert result == "dlq"
        assert await r.xlen("stream:dlq") == 1
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["failure_code"] == "http_5xx"
        assert entries[0][1]["failed_by"] == "test-svc"

    @pytest.mark.asyncio
    async def test_rate_limit__propagates_retry_after_ms(self, r):
        """RateLimitError passes retry_after_ms to DLQ envelope."""
        from lol_pipeline.helpers import handle_riot_api_error
        from lol_pipeline.riot_api import RateLimitError
        from lol_pipeline.streams import consume, publish

        env = self._make_envelope()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "test-group", "c1", block=0)
        msg_id = msgs[0][0]

        exc = RateLimitError(retry_after_ms=5000)
        await handle_riot_api_error(
            r, exc=exc, envelope=env, msg_id=msg_id,
            failed_by="fetcher", in_stream="stream:test", group="test-group",
        )
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["retry_after_ms"] == "5000"

    @pytest.mark.asyncio
    async def test_server_error__no_retry_after(self, r):
        """ServerError does NOT set retry_after_ms (null)."""
        from lol_pipeline.helpers import handle_riot_api_error
        from lol_pipeline.riot_api import ServerError
        from lol_pipeline.streams import consume, publish

        env = self._make_envelope()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "test-group", "c1", block=0)
        msg_id = msgs[0][0]

        exc = ServerError("error", status_code=503)
        await handle_riot_api_error(
            r, exc=exc, envelope=env, msg_id=msg_id,
            failed_by="crawler", in_stream="stream:test", group="test-group",
        )
        entries = await r.xrange("stream:dlq")
        assert entries[0][1]["retry_after_ms"] == "null"

    @pytest.mark.asyncio
    async def test_server_error__log_mentions_dlq(self, r):
        """E4: ServerError log message should mention DLQ for operator context."""
        import logging

        from lol_pipeline.helpers import handle_riot_api_error
        from lol_pipeline.riot_api import ServerError
        from lol_pipeline.streams import consume, publish

        env = self._make_envelope()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "test-group", "c1", block=0)
        msg_id = msgs[0][0]

        log = logging.getLogger("test.e4")
        exc = ServerError("internal error", status_code=500)
        with patch.object(log, "error") as mock_error:
            await handle_riot_api_error(
                r, exc=exc, envelope=env, msg_id=msg_id,
                failed_by="test-svc", in_stream="stream:test", group="test-group",
                log=log,
            )
            mock_error.assert_called_once()
            logged_msg = mock_error.call_args[0][0]
            assert "DLQ" in logged_msg

    @pytest.mark.asyncio
    async def test_rate_limit_error__log_mentions_dlq(self, r):
        """E4: RateLimitError log message should also mention DLQ."""
        import logging

        from lol_pipeline.helpers import handle_riot_api_error
        from lol_pipeline.riot_api import RateLimitError
        from lol_pipeline.streams import consume, publish

        env = self._make_envelope()
        await publish(r, "stream:test", env)
        msgs = await consume(r, "stream:test", "test-group", "c1", block=0)
        msg_id = msgs[0][0]

        log = logging.getLogger("test.e4.ratelimit")
        exc = RateLimitError(retry_after_ms=5000)
        with patch.object(log, "error") as mock_error:
            await handle_riot_api_error(
                r, exc=exc, envelope=env, msg_id=msg_id,
                failed_by="test-svc", in_stream="stream:test", group="test-group",
                log=log,
            )
            mock_error.assert_called_once()
            logged_msg = mock_error.call_args[0][0]
            assert "DLQ" in logged_msg


class TestConsumerId:
    """consumer_id() returns a unique hostname-pid string for stream consumers."""

    def test_returns_string(self):
        """consumer_id() returns a str."""
        result = consumer_id()
        assert isinstance(result, str)

    def test_contains_hostname(self):
        """Result contains the machine's hostname."""
        import socket

        hostname = socket.gethostname()
        result = consumer_id()
        assert hostname in result

    def test_contains_pid(self):
        """Result contains the current process ID."""
        import os

        pid = str(os.getpid())
        result = consumer_id()
        assert pid in result

    def test_format_hostname_dash_pid(self):
        """With mocked hostname and pid, result is exactly 'hostname-pid'."""
        with patch("lol_pipeline.helpers.socket.gethostname", return_value="myhost"), \
             patch("lol_pipeline.helpers.os.getpid", return_value=12345):
            assert consumer_id() == "myhost-12345"
