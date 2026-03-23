"""Tests for lol_pipeline.i18n — shared domain vocabulary."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.i18n import DDRAGON_LOCALE_MAP, DOMAIN_STRINGS, label, track_missing


class TestLabel:
    """label() returns localized domain terms with fallback chain."""

    def test_english_role__returns_display_name(self):
        assert label("role", "TOP") == "Top"

    def test_english_role__all_roles_present(self):
        for key in ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"):
            result = label("role", key)
            assert result != key  # should be a display name, not the raw key

    def test_chinese_role__returns_chinese(self):
        assert label("role", "TOP", "zh-CN") == "\u4e0a\u5355"

    def test_chinese_tier__returns_chinese(self):
        assert label("tier", "CHALLENGER", "zh-CN") == "\u738b\u8005"

    def test_english_queue__returns_display_name(self):
        assert label("queue", "420") == "Ranked Solo/Duo"

    def test_chinese_queue__returns_chinese(self):
        assert label("queue", "420", "zh-CN") == "\u5355\u53cc\u6392\u4f4d"

    def test_unknown_key__falls_back_to_key(self):
        assert label("role", "NONEXISTENT") == "NONEXISTENT"

    def test_unknown_domain__falls_back_to_key(self):
        assert label("bogus_domain", "anything") == "anything"

    def test_unknown_lang__falls_back_to_english(self):
        assert label("role", "TOP", "fr") == "Top"

    def test_status_domain__english(self):
        assert label("status", "running") == "Running"

    def test_status_domain__chinese(self):
        assert label("status", "halted", "zh-CN") == "\u5df2\u505c\u6b62"

    def test_failure_code_domain__english(self):
        assert label("failure_code", "http_429") == "Rate Limited"

    def test_failure_code_domain__chinese(self):
        assert label("failure_code", "parse_error", "zh-CN") == "\u89e3\u6790\u9519\u8bef"


class TestDdragonLocaleMap:
    """DDRAGON_LOCALE_MAP maps our lang codes to DDragon locale strings."""

    def test_english_maps_to_en_us(self):
        assert DDRAGON_LOCALE_MAP["en"] == "en_US"

    def test_chinese_maps_to_zh_cn(self):
        assert DDRAGON_LOCALE_MAP["zh-CN"] == "zh_CN"


class TestDomainStringsCompleteness:
    """Every domain has the same keys in all language variants."""

    @pytest.mark.parametrize("domain", list(DOMAIN_STRINGS.keys()))
    def test_all_langs_have_same_keys(self, domain):
        lang_dicts = DOMAIN_STRINGS[domain]
        en_keys = set(lang_dicts["en"].keys())
        for lang, strings in lang_dicts.items():
            missing = en_keys - set(strings.keys())
            extra = set(strings.keys()) - en_keys
            assert not missing, f"{domain}/{lang} missing keys: {missing}"
            assert not extra, f"{domain}/{lang} extra keys: {extra}"


class TestTrackMissing:
    """track_missing() records missing translations in Redis."""

    @pytest.fixture
    async def r(self):
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        yield redis
        await redis.aclose()

    async def test_adds_entry_to_redis_set(self, r):
        await track_missing(r, "zh-CN", "role", "NEW_ROLE")
        members = await r.smembers("i18n:missing:zh-CN")
        assert "role:NEW_ROLE" in members

    async def test_idempotent__no_duplicates(self, r):
        await track_missing(r, "zh-CN", "role", "NEW_ROLE")
        await track_missing(r, "zh-CN", "role", "NEW_ROLE")
        count = await r.scard("i18n:missing:zh-CN")
        assert count == 1

    async def test_multiple_domains__tracked_separately(self, r):
        await track_missing(r, "fr", "role", "TOP")
        await track_missing(r, "fr", "tier", "SILVER")
        members = await r.smembers("i18n:missing:fr")
        assert members == {"role:TOP", "tier:SILVER"}
