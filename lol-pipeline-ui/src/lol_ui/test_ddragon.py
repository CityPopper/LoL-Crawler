"""Tests for ddragon.py — DDragon cache helper and version/champion lookups."""

from __future__ import annotations

import json

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_ui.ddragon import (
    _DDRAGON_CHAMPION_IDS_KEY,
    _DDRAGON_MAX_RESPONSE_BYTES,
    _DDRAGON_VERSION_KEY,
    _get_champion_id_map,
    _get_ddragon_json,
    _get_ddragon_version,
    _validate_ddragon_version,
)


class TestValidateDdragonVersion:
    """_validate_ddragon_version checks semver format."""

    def test_valid_version(self):
        assert _validate_ddragon_version("14.10.1") is True

    def test_valid_version_short(self):
        assert _validate_ddragon_version("1.0.0") is True

    def test_invalid_missing_patch(self):
        assert _validate_ddragon_version("14.10") is False

    def test_invalid_letters(self):
        assert _validate_ddragon_version("abc") is False

    def test_invalid_empty(self):
        assert _validate_ddragon_version("") is False

    def test_invalid_injection(self):
        assert _validate_ddragon_version("14.10.1/../../etc/passwd") is False


class TestGetDdragonJson:
    """_get_ddragon_json fetches and caches JSON from DDragon."""

    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    @respx.mock
    async def test_returns_cached_data(self, r):
        data = {"key": "value"}
        await r.set("test:cache", json.dumps(data), ex=3600)
        result = await _get_ddragon_json(r, "test:cache", "https://example.com/data.json")
        assert result == data

    @respx.mock
    async def test_fetches_and_caches_on_miss(self, r):
        data = {"items": [1, 2, 3]}
        respx.get("https://example.com/data.json").mock(return_value=httpx.Response(200, json=data))
        result = await _get_ddragon_json(r, "test:cache", "https://example.com/data.json")
        assert result == data
        # Verify cached
        cached = await r.get("test:cache")
        assert cached is not None
        assert json.loads(cached) == data

    @respx.mock
    async def test_returns_none_on_http_error(self, r):
        respx.get("https://example.com/fail.json").mock(return_value=httpx.Response(500))
        result = await _get_ddragon_json(r, "test:cache", "https://example.com/fail.json")
        assert result is None

    @respx.mock
    async def test_returns_none_on_oversized_response(self, r):
        big_data = "x" * (_DDRAGON_MAX_RESPONSE_BYTES + 1)
        respx.get("https://example.com/big.json").mock(
            return_value=httpx.Response(200, content=big_data.encode())
        )
        result = await _get_ddragon_json(r, "test:cache", "https://example.com/big.json")
        assert result is None

    @respx.mock
    async def test_custom_ttl(self, r):
        data = {"ttl": "custom"}
        respx.get("https://example.com/ttl.json").mock(return_value=httpx.Response(200, json=data))
        await _get_ddragon_json(r, "test:ttl", "https://example.com/ttl.json", ttl=7200)
        ttl_val = await r.ttl("test:ttl")
        assert ttl_val > 0
        assert ttl_val <= 7200


class TestGetDdragonVersion:
    """_get_ddragon_version returns the latest DDragon version."""

    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    @respx.mock
    async def test_returns_cached_version(self, r):
        await r.set(_DDRAGON_VERSION_KEY, "14.10.1", ex=3600)
        result = await _get_ddragon_version(r)
        assert result == "14.10.1"

    @respx.mock
    async def test_fetches_version_on_cache_miss(self, r):
        respx.get("https://ddragon.leagueoflegends.com/api/versions.json").mock(
            return_value=httpx.Response(200, json=["14.10.1", "14.9.1"])
        )
        result = await _get_ddragon_version(r)
        assert result == "14.10.1"

    @respx.mock
    async def test_returns_none_on_invalid_version_format(self, r):
        respx.get("https://ddragon.leagueoflegends.com/api/versions.json").mock(
            return_value=httpx.Response(200, json=["bad-version"])
        )
        result = await _get_ddragon_version(r)
        assert result is None

    @respx.mock
    async def test_returns_none_on_network_error(self, r):
        respx.get("https://ddragon.leagueoflegends.com/api/versions.json").mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        result = await _get_ddragon_version(r)
        assert result is None

    @respx.mock
    async def test_rejects_cached_invalid_format(self, r):
        await r.set(_DDRAGON_VERSION_KEY, "not-a-version")
        # Should fall through to HTTP fetch, which also fails (no route mocked)
        result = await _get_ddragon_version(r)
        assert result is None


class TestGetChampionIdMap:
    """_get_champion_id_map returns numeric-id to champion-name mapping."""

    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    @respx.mock
    async def test_returns_cached_mapping(self, r):
        mapping = {"1": "Annie", "2": "Olaf"}
        await r.set(_DDRAGON_CHAMPION_IDS_KEY, json.dumps(mapping), ex=3600)
        result = await _get_champion_id_map(r)
        assert result == mapping

    @respx.mock
    async def test_returns_empty_dict_on_no_version(self, r):
        respx.get("https://ddragon.leagueoflegends.com/api/versions.json").mock(
            return_value=httpx.Response(500)
        )
        result = await _get_champion_id_map(r)
        assert result == {}
