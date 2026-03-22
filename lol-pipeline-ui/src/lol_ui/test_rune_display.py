"""Tests for rune_display.py — rune page rendering with DDragon icons."""

from __future__ import annotations

import json

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_ui.ddragon import _DDRAGON_VERSION_KEY
from lol_ui.rune_display import (
    _DDRAGON_RUNES_KEY,
    _STAT_SHARD_LABELS,
    _build_rune_lookup,
    _get_runes_data,
    _parse_int_list,
    _parse_str_list,
    _rune_icon_html,
    _rune_page_html,
    _stat_shard_html,
)

# Sample runesReforged.json structure for testing
_SAMPLE_RUNES = [
    {
        "id": 8100,
        "name": "Domination",
        "icon": "perk-images/Styles/7200_Domination.png",
        "slots": [
            {
                "runes": [
                    {
                        "id": 8112,
                        "name": "Electrocute",
                        "icon": "perk-images/Styles/Domination/Electrocute/Electrocute.png",
                    },
                ]
            },
            {
                "runes": [
                    {
                        "id": 8126,
                        "name": "Cheap Shot",
                        "icon": "perk-images/Styles/Domination/CheapShot/CheapShot.png",
                    },
                ]
            },
            {
                "runes": [
                    {
                        "id": 8138,
                        "name": "Eyeball Collection",
                        "icon": "perk-images/Styles/Domination/EyeballCollection.png",
                    },
                ]
            },
            {
                "runes": [
                    {
                        "id": 8135,
                        "name": "Treasure Hunter",
                        "icon": "perk-images/Styles/Domination/TreasureHunter.png",
                    },
                ]
            },
        ],
    },
    {
        "id": 8300,
        "name": "Inspiration",
        "icon": "perk-images/Styles/7203_Whimsy.png",
        "slots": [
            {
                "runes": [
                    {
                        "id": 8351,
                        "name": "Glacial Augment",
                        "icon": "perk-images/Styles/Inspiration/GlacialAugment.png",
                    },
                ]
            },
            {
                "runes": [
                    {
                        "id": 8313,
                        "name": "Perfect Timing",
                        "icon": "perk-images/Styles/Inspiration/PerfectTiming.png",
                    },
                ]
            },
            {
                "runes": [
                    {
                        "id": 8321,
                        "name": "Future's Market",
                        "icon": "perk-images/Styles/Inspiration/FuturesMarket.png",
                    },
                ]
            },
        ],
    },
]


@pytest.fixture
def rune_lookup():
    return _build_rune_lookup(_SAMPLE_RUNES)


class TestBuildRuneLookup:
    """_build_rune_lookup creates a flat perk_id -> info mapping."""

    def test_keystone_marked_correctly(self, rune_lookup):
        assert rune_lookup[8112]["is_keystone"] == "1"

    def test_non_keystone_marked_correctly(self, rune_lookup):
        assert rune_lookup[8126]["is_keystone"] == "0"

    def test_tree_itself_included(self, rune_lookup):
        assert 8100 in rune_lookup
        assert rune_lookup[8100]["name"] == "Domination"

    def test_all_runes_included(self, rune_lookup):
        assert 8112 in rune_lookup  # Electrocute
        assert 8126 in rune_lookup  # Cheap Shot
        assert 8138 in rune_lookup  # Eyeball Collection
        assert 8135 in rune_lookup  # Treasure Hunter
        assert 8351 in rune_lookup  # Glacial Augment

    def test_rune_has_name_and_icon(self, rune_lookup):
        assert rune_lookup[8112]["name"] == "Electrocute"
        assert "Electrocute" in rune_lookup[8112]["icon"]

    def test_rune_has_tree_name(self, rune_lookup):
        assert rune_lookup[8112]["tree"] == "Domination"
        assert rune_lookup[8351]["tree"] == "Inspiration"

    def test_empty_data__returns_empty_dict(self):
        assert _build_rune_lookup([]) == {}


class TestRuneIconHtml:
    """_rune_icon_html renders a single rune icon."""

    def test_valid_perk__renders_img(self, rune_lookup):
        result = _rune_icon_html(8112, rune_lookup, "14.10.1")
        assert "<img" in result
        assert "Electrocute" in result
        assert "ddragon.leagueoflegends.com" in result

    def test_large_flag__adds_lg_class(self, rune_lookup):
        result = _rune_icon_html(8112, rune_lookup, "14.10.1", large=True)
        assert "rune-icon--lg" in result

    def test_normal_size__uses_base_class(self, rune_lookup):
        result = _rune_icon_html(8126, rune_lookup, "14.10.1")
        assert 'class="rune-icon"' in result

    def test_unknown_perk__renders_empty(self, rune_lookup):
        result = _rune_icon_html(9999, rune_lookup, "14.10.1")
        assert "rune-icon--empty" in result

    def test_no_version__renders_empty(self, rune_lookup):
        result = _rune_icon_html(8112, rune_lookup, None)
        assert "rune-icon--empty" in result

    def test_has_onerror_fallback(self, rune_lookup):
        result = _rune_icon_html(8112, rune_lookup, "14.10.1")
        assert "onerror" in result

    def test_has_title_attribute(self, rune_lookup):
        result = _rune_icon_html(8112, rune_lookup, "14.10.1")
        assert 'title="Electrocute"' in result


class TestStatShardHtml:
    """_stat_shard_html renders stat shards as text labels."""

    def test_known_shard__shows_label(self):
        result = _stat_shard_html("5008")
        assert "+9 Adaptive Force" in result
        assert "rune-shard" in result

    def test_unknown_shard__shows_fallback(self):
        result = _stat_shard_html("9999")
        assert "Shard 9999" in result

    def test_all_known_shards_have_labels(self):
        for shard_id in _STAT_SHARD_LABELS:
            result = _stat_shard_html(shard_id)
            assert _STAT_SHARD_LABELS[shard_id] in result


class TestRunePageHtml:
    """_rune_page_html renders the full rune page for a participant."""

    def test_keystone_only__degrades_gracefully(self, rune_lookup):
        part = {"perk_keystone": "8112", "perk_primary_style": "8100"}
        result = _rune_page_html(part, rune_lookup, "14.10.1")
        assert "rune-page" in result
        assert "Electrocute" in result
        # No sub-selections shown
        assert "rune-path--secondary" not in result

    def test_full_data__shows_primary_and_secondary(self, rune_lookup):
        part = {
            "perk_keystone": "8112",
            "perk_primary_style": "8100",
            "perk_sub_style": "8300",
            "perk_primary_selections": json.dumps([8126, 8138, 8135]),
            "perk_sub_selections": json.dumps([8313, 8321]),
            "perk_stat_shards": json.dumps([5008, 5008, 5001]),
        }
        result = _rune_page_html(part, rune_lookup, "14.10.1")
        assert "rune-page" in result
        assert "Electrocute" in result  # keystone
        assert "Cheap Shot" in result  # primary selection
        assert "Domination" in result  # primary tree label
        assert "Inspiration" in result  # secondary tree label
        assert "Perfect Timing" in result  # sub selection
        assert "+9 Adaptive Force" in result  # stat shard

    def test_no_keystone__returns_empty(self, rune_lookup):
        part: dict[str, str] = {}
        result = _rune_page_html(part, rune_lookup, "14.10.1")
        assert result == ""

    def test_stat_shards_shown(self, rune_lookup):
        part = {
            "perk_keystone": "8112",
            "perk_primary_style": "8100",
            "perk_stat_shards": json.dumps([5005, 5002, 5003]),
        }
        result = _rune_page_html(part, rune_lookup, "14.10.1")
        assert "+10% Attack Speed" in result
        assert "+6 Armor" in result
        assert "+8 Magic Resist" in result

    def test_primary_tree_label_shown(self, rune_lookup):
        part = {"perk_keystone": "8112", "perk_primary_style": "8100"}
        result = _rune_page_html(part, rune_lookup, "14.10.1")
        assert "Domination" in result

    def test_keystone_rendered_large(self, rune_lookup):
        part = {"perk_keystone": "8112", "perk_primary_style": "8100"}
        result = _rune_page_html(part, rune_lookup, "14.10.1")
        assert "rune-icon--lg" in result


class TestParseIntList:
    """_parse_int_list parses JSON arrays of ints."""

    def test_valid_json(self):
        assert _parse_int_list("[1, 2, 3]") == [1, 2, 3]

    def test_empty_string(self):
        assert _parse_int_list("") == []

    def test_empty_array(self):
        assert _parse_int_list("[]") == []

    def test_invalid_json(self):
        assert _parse_int_list("not json") == []


class TestParseStrList:
    """_parse_str_list parses JSON arrays of strings."""

    def test_valid_json(self):
        assert _parse_str_list('["a", "b"]') == ["a", "b"]

    def test_numeric_values_coerced(self):
        assert _parse_str_list("[5008, 5001]") == ["5008", "5001"]

    def test_empty_string(self):
        assert _parse_str_list("") == []


class TestGetRunesData:
    """_get_runes_data fetches runesReforged.json from DDragon."""

    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    @respx.mock
    async def test_returns_cached_data(self, r):
        await r.set(_DDRAGON_VERSION_KEY, "14.10.1", ex=3600)
        await r.set(_DDRAGON_RUNES_KEY, json.dumps(_SAMPLE_RUNES), ex=3600)
        result = await _get_runes_data(r)
        assert len(result) == 2
        assert result[0]["name"] == "Domination"

    @respx.mock
    async def test_returns_empty_on_no_version(self, r):
        respx.get("https://ddragon.leagueoflegends.com/api/versions.json").mock(
            return_value=httpx.Response(500)
        )
        result = await _get_runes_data(r)
        assert result == []
