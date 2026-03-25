"""Tests for ddragon.py — DDragon cache helper and version/champion lookups."""

from __future__ import annotations

import json

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_ui.ddragon import (  # type: ignore[attr-defined]
    _DDRAGON_CHAMPION_IDS_KEY,
    _DDRAGON_CHAMPION_NAMES_KEY_PREFIX,
    _DDRAGON_MAX_RESPONSE_BYTES,
    _DDRAGON_VERSION_KEY,
    _get_champion_id_map,
    _get_ddragon_json,
    _get_ddragon_version,
    _mem_cache,
    _validate_ddragon_version,
    get_champion_name_map,
    localize_champion_name,
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
        # Verify cached in memory (not Redis)
        assert "test:cache" in _mem_cache
        cached_data, _expiry = _mem_cache["test:cache"]
        assert cached_data == data

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
        import time

        data = {"ttl": "custom"}
        respx.get("https://example.com/ttl.json").mock(return_value=httpx.Response(200, json=data))
        before = time.monotonic()
        await _get_ddragon_json(r, "test:ttl", "https://example.com/ttl.json", ttl=7200)
        # Verify in-memory cache entry has correct TTL
        assert "test:ttl" in _mem_cache
        _cached_data, expiry = _mem_cache["test:ttl"]
        remaining = expiry - before
        assert remaining > 0
        assert remaining <= 7200 + 1  # +1s tolerance for monotonic clock skew


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


# --- Champion name map fixtures ---

_CHAMPION_JSON_EN = {
    "data": {
        "Annie": {"id": "Annie", "key": "1", "name": "Annie"},
        "MonkeyKing": {"id": "MonkeyKing", "key": "62", "name": "Wukong"},
        "Jinx": {"id": "Jinx", "key": "222", "name": "Jinx"},
    }
}

_CHAMPION_JSON_ZH_CN = {
    "data": {
        "Annie": {"id": "Annie", "key": "1", "name": "\u5b89\u59ae"},
        "MonkeyKing": {"id": "MonkeyKing", "key": "62", "name": "\u5b59\u609f\u7a7a"},
        "Jinx": {"id": "Jinx", "key": "222", "name": "\u91d1\u514b\u4e1d"},
    }
}


class TestGetChampionNameMap:
    """get_champion_name_map returns {english_id: localized_display_name}."""

    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    @respx.mock
    async def test_returns_cached_mapping(self, r):
        mapping = {"Annie": "Annie", "MonkeyKing": "Wukong"}
        cache_key = f"{_DDRAGON_CHAMPION_NAMES_KEY_PREFIX}:en_US"
        await r.set(cache_key, json.dumps(mapping), ex=3600)
        result = await get_champion_name_map(r, "en")
        assert result == mapping

    @respx.mock
    async def test_fetches_english_names(self, r):
        await r.set(_DDRAGON_VERSION_KEY, "14.10.1", ex=3600)
        respx.get("https://ddragon.leagueoflegends.com/cdn/14.10.1/data/en_US/champion.json").mock(
            return_value=httpx.Response(200, json=_CHAMPION_JSON_EN)
        )
        result = await get_champion_name_map(r, "en")
        assert result["Annie"] == "Annie"
        assert result["MonkeyKing"] == "Wukong"
        assert result["Jinx"] == "Jinx"

    @respx.mock
    async def test_fetches_chinese_names(self, r):
        await r.set(_DDRAGON_VERSION_KEY, "14.10.1", ex=3600)
        respx.get("https://ddragon.leagueoflegends.com/cdn/14.10.1/data/zh_CN/champion.json").mock(
            return_value=httpx.Response(200, json=_CHAMPION_JSON_ZH_CN)
        )
        result = await get_champion_name_map(r, "zh-CN")
        assert result["Annie"] == "\u5b89\u59ae"
        assert result["MonkeyKing"] == "\u5b59\u609f\u7a7a"

    @respx.mock
    async def test_caches_result_in_mem_cache(self, r):
        await r.set(_DDRAGON_VERSION_KEY, "14.10.1", ex=3600)
        respx.get("https://ddragon.leagueoflegends.com/cdn/14.10.1/data/en_US/champion.json").mock(
            return_value=httpx.Response(200, json=_CHAMPION_JSON_EN)
        )
        await get_champion_name_map(r, "en")
        cache_key = f"{_DDRAGON_CHAMPION_NAMES_KEY_PREFIX}:en_US"
        assert cache_key in _mem_cache
        cached_data, _expiry = _mem_cache[cache_key]
        assert "Annie" in cached_data

    @respx.mock
    async def test_falls_back_to_english_on_locale_failure(self, r):
        await r.set(_DDRAGON_VERSION_KEY, "14.10.1", ex=3600)
        # zh_CN fails
        respx.get("https://ddragon.leagueoflegends.com/cdn/14.10.1/data/zh_CN/champion.json").mock(
            return_value=httpx.Response(500)
        )
        # en_US succeeds
        respx.get("https://ddragon.leagueoflegends.com/cdn/14.10.1/data/en_US/champion.json").mock(
            return_value=httpx.Response(200, json=_CHAMPION_JSON_EN)
        )
        result = await get_champion_name_map(r, "zh-CN")
        assert result["Annie"] == "Annie"

    @respx.mock
    async def test_returns_empty_dict_when_no_version(self, r):
        respx.get("https://ddragon.leagueoflegends.com/api/versions.json").mock(
            return_value=httpx.Response(500)
        )
        result = await get_champion_name_map(r, "en")
        assert result == {}

    @respx.mock
    async def test_default_lang_is_english(self, r):
        mapping = {"Jinx": "Jinx"}
        cache_key = f"{_DDRAGON_CHAMPION_NAMES_KEY_PREFIX}:en_US"
        await r.set(cache_key, json.dumps(mapping), ex=3600)
        result = await get_champion_name_map(r)
        assert result == mapping


class TestLocalizeChampionName:
    """localize_champion_name looks up display names with safe fallback."""

    def test_found_in_map__returns_localized(self):
        name_map = {"MonkeyKing": "Wukong", "Jinx": "Jinx"}
        assert localize_champion_name(name_map, "MonkeyKing") == "Wukong"

    def test_not_found__returns_champion_id_unchanged(self):
        name_map = {"Jinx": "Jinx"}
        assert localize_champion_name(name_map, "UnknownChamp") == "UnknownChamp"

    def test_empty_map__returns_champion_id(self):
        assert localize_champion_name({}, "Annie") == "Annie"

    def test_chinese_name__returns_chinese(self):
        name_map = {"Annie": "\u5b89\u59ae"}
        assert localize_champion_name(name_map, "Annie") == "\u5b89\u59ae"


class TestDdragonFetchErrorLogging:
    """E1: DDragon fetch errors should emit WARNING logs, not silently swallow."""

    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    @respx.mock
    async def test_get_ddragon_json__network_error__logs_warning(self, r, caplog):
        """_get_ddragon_json logs WARNING on fetch failure."""
        import logging

        # Ensure propagation is enabled so caplog (root handler) captures records,
        # even when the structured JSON logger has set propagate=False on parent "ui".
        logging.getLogger("ui.ddragon")
        parent = logging.getLogger("ui")
        orig_propagate = parent.propagate
        parent.propagate = True
        try:
            respx.get("https://example.com/fail.json").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            with caplog.at_level(logging.WARNING, logger="ui.ddragon"):
                result = await _get_ddragon_json(r, "test:cache", "https://example.com/fail.json")
            assert result is None
            assert any("DDragon fetch failed" in rec.message for rec in caplog.records)
        finally:
            parent.propagate = orig_propagate

    @respx.mock
    async def test_get_ddragon_version__network_error__logs_warning(self, r, caplog):
        """_get_ddragon_version logs WARNING on fetch failure."""
        import logging

        # Ensure propagation is enabled so caplog (root handler) captures records.
        parent = logging.getLogger("ui")
        orig_propagate = parent.propagate
        parent.propagate = True
        try:
            respx.get("https://ddragon.leagueoflegends.com/api/versions.json").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            with caplog.at_level(logging.WARNING, logger="ui.ddragon"):
                result = await _get_ddragon_version(r)
            assert result is None
            assert any("DDragon version fetch failed" in rec.message for rec in caplog.records)
        finally:
            parent.propagate = orig_propagate
