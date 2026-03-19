"""Unit tests for lol_pipeline.helpers — shared DRY utilities."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.helpers import is_system_halted, name_cache_key


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
