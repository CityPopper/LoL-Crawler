"""Unit tests for lol_pipeline.helpers — shared DRY utilities."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.helpers import (
    _MAX_GAME_NAME_LEN,
    _MAX_TAG_LINE_LEN,
    is_system_halted,
    name_cache_key,
    validate_name_lengths,
)


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
