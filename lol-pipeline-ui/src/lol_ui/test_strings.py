"""Tests for the localization module (strings.py)."""

from __future__ import annotations

import lol_ui.strings as mod


class TestT:
    """t() returns HTML-escaped localized strings."""

    def test_returns_english_string_for_known_key(self):
        assert mod.t("win") == "Win"
        assert mod.t("grade_s") == "Exceptional"
        assert mod.t("no_timeline_data") == "Timeline data unavailable for this match."

    def test_returns_key_itself_for_unknown_key(self):
        assert mod.t("totally_nonexistent_key_xyz") == "totally_nonexistent_key_xyz"

    def test_output_is_html_escaped(self):
        # Temporarily inject a string with HTML-special characters
        mod._STRINGS["en"]["_test_escape"] = '<script>alert("xss")</script>'
        mod._STRINGS["zh-CN"]["_test_escape"] = '<script>alert("xss")</script>'
        try:
            result = mod.t("_test_escape")
            assert "<script>" not in result
            assert "&lt;script&gt;" in result
        finally:
            # Clean up injected key
            del mod._STRINGS["en"]["_test_escape"]
            del mod._STRINGS["zh-CN"]["_test_escape"]

    def test_lang_param__returns_chinese_string(self):
        result = mod.t("win", lang="zh-CN")
        assert result == "\u80dc\u5229"

    def test_lang_param__defaults_to_english(self):
        assert mod.t("win") == "Win"
        assert mod.t("win", lang="en") == "Win"

    def test_lang_param__unknown_lang_falls_back_to_english(self):
        assert mod.t("win", lang="xx-XX") == "Win"


class TestTRaw:
    """t_raw() returns unescaped strings."""

    def test_returns_unescaped_string(self):
        mod._STRINGS["en"]["_test_raw"] = '<b>bold</b> & "quoted"'
        mod._STRINGS["zh-CN"]["_test_raw"] = '<b>bold</b> & "quoted"'
        try:
            result = mod.t_raw("_test_raw")
            assert result == '<b>bold</b> & "quoted"'
        finally:
            del mod._STRINGS["en"]["_test_raw"]
            del mod._STRINGS["zh-CN"]["_test_raw"]

    def test_returns_key_for_unknown(self):
        assert mod.t_raw("missing_key_abc") == "missing_key_abc"

    def test_lang_param__returns_chinese_raw(self):
        result = mod.t_raw("win", lang="zh-CN")
        assert result == "\u80dc\u5229"


class TestKeyParity:
    """All keys in en must exist in zh-CN and vice versa."""

    def test_en_keys_exist_in_zh_cn(self):
        en_keys = set(mod._STRINGS["en"].keys())
        zh_keys = set(mod._STRINGS["zh-CN"].keys())
        missing = en_keys - zh_keys
        assert not missing, f"Keys in en but missing from zh-CN: {missing}"

    def test_zh_cn_keys_exist_in_en(self):
        en_keys = set(mod._STRINGS["en"].keys())
        zh_keys = set(mod._STRINGS["zh-CN"].keys())
        extra = zh_keys - en_keys
        assert not extra, f"Keys in zh-CN but missing from en: {extra}"

    def test_no_placeholder_strings_in_zh_cn(self):
        """All zh-CN values must be real translations, not [CN] placeholders."""
        placeholders = {k: v for k, v in mod._STRINGS["zh-CN"].items() if v.startswith("[CN]")}
        assert not placeholders, f"zh-CN still has placeholders: {placeholders}"


class TestSupportedLanguages:
    """SUPPORTED_LANGUAGES list is accurate."""

    def test_contains_en_and_zh_cn(self):
        assert "en" in mod.SUPPORTED_LANGUAGES
        assert "zh-CN" in mod.SUPPORTED_LANGUAGES

    def test_matches_string_table_keys(self):
        assert set(mod.SUPPORTED_LANGUAGES) == set(mod._STRINGS.keys())
