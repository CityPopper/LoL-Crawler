"""Tests for spell_display.py — summoner spell icon rendering."""

from __future__ import annotations

import json

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_ui.ddragon import _DDRAGON_VERSION_KEY
from lol_ui.spell_display import (
    _DDRAGON_SUMMONERS_KEY,
    _get_summoner_spell_map,
    _summoner_spell_icon_html,
    _summoner_spell_icons_html,
)


class TestSummonerSpellIconHtml:
    """_summoner_spell_icon_html renders a single spell icon."""

    def test_valid_spell__renders_img(self):
        spell_map = {"4": "SummonerFlash.png", "14": "SummonerDot.png"}
        result = _summoner_spell_icon_html("4", spell_map, "14.10.1")
        assert "SummonerFlash.png" in result
        assert 'class="spell-icon"' in result
        assert "<img" in result

    def test_unknown_spell_id__renders_empty(self):
        spell_map = {"4": "SummonerFlash.png"}
        result = _summoner_spell_icon_html("999", spell_map, "14.10.1")
        assert "spell-icon--empty" in result

    def test_no_version__renders_empty(self):
        spell_map = {"4": "SummonerFlash.png"}
        result = _summoner_spell_icon_html("4", spell_map, None)
        assert "spell-icon--empty" in result

    def test_zero_spell_id__renders_empty(self):
        spell_map = {"4": "SummonerFlash.png"}
        result = _summoner_spell_icon_html("0", spell_map, "14.10.1")
        assert "spell-icon--empty" in result

    def test_empty_spell_id__renders_empty(self):
        spell_map = {"4": "SummonerFlash.png"}
        result = _summoner_spell_icon_html("", spell_map, "14.10.1")
        assert "spell-icon--empty" in result

    def test_has_onerror_fallback(self):
        spell_map = {"4": "SummonerFlash.png"}
        result = _summoner_spell_icon_html("4", spell_map, "14.10.1")
        assert "onerror" in result

    def test_uses_ddragon_cdn_url(self):
        spell_map = {"4": "SummonerFlash.png"}
        result = _summoner_spell_icon_html("4", spell_map, "14.10.1")
        assert "ddragon.leagueoflegends.com/cdn/14.10.1/img/spell/" in result


class TestSummonerSpellIconsHtml:
    """_summoner_spell_icons_html renders two icons."""

    def test_renders_two_icons(self):
        spell_map = {"4": "SummonerFlash.png", "14": "SummonerDot.png"}
        result = _summoner_spell_icons_html("4", "14", spell_map, "14.10.1")
        assert "SummonerFlash.png" in result
        assert "SummonerDot.png" in result
        assert "spell-pair" in result

    def test_both_empty__renders_two_empty_slots(self):
        result = _summoner_spell_icons_html("0", "0", {}, "14.10.1")
        assert result.count("spell-icon--empty") == 2

    def test_mixed_valid_and_empty(self):
        spell_map = {"4": "SummonerFlash.png"}
        result = _summoner_spell_icons_html("4", "999", spell_map, "14.10.1")
        assert "SummonerFlash.png" in result
        assert "spell-icon--empty" in result


class TestGetSummonerSpellMap:
    """_get_summoner_spell_map fetches and caches spell data."""

    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    async def test_returns_cached_map(self, r):
        mapping = {"4": "SummonerFlash.png", "14": "SummonerDot.png"}
        await r.set(_DDRAGON_SUMMONERS_KEY, json.dumps(mapping), ex=3600)
        result = await _get_summoner_spell_map(r)
        assert result == mapping

    @respx.mock
    async def test_returns_empty_dict_on_no_version(self, r):
        respx.get("https://ddragon.leagueoflegends.com/api/versions.json").mock(
            return_value=httpx.Response(500)
        )
        result = await _get_summoner_spell_map(r)
        assert result == {}

    @respx.mock
    async def test_fetches_and_caches_on_miss(self, r):
        await r.set(_DDRAGON_VERSION_KEY, "14.10.1", ex=3600)
        summoner_data = {
            "data": {
                "SummonerFlash": {
                    "key": "4",
                    "image": {"full": "SummonerFlash.png"},
                },
                "SummonerDot": {
                    "key": "14",
                    "image": {"full": "SummonerDot.png"},
                },
            }
        }
        respx.get("https://ddragon.leagueoflegends.com/cdn/14.10.1/data/en_US/summoner.json").mock(
            return_value=httpx.Response(200, json=summoner_data)
        )
        result = await _get_summoner_spell_map(r)
        assert result["4"] == "SummonerFlash.png"
        assert result["14"] == "SummonerDot.png"
