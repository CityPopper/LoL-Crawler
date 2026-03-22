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
        mod._STRINGS["zh-CN"]["_test_escape"] = '[CN] <script>alert("xss")</script>'
        try:
            result = mod.t("_test_escape")
            assert "<script>" not in result
            assert "&lt;script&gt;" in result
        finally:
            # Clean up injected key
            del mod._STRINGS["en"]["_test_escape"]
            del mod._STRINGS["zh-CN"]["_test_escape"]


class TestTRaw:
    """t_raw() returns unescaped strings."""

    def test_returns_unescaped_string(self):
        mod._STRINGS["en"]["_test_raw"] = '<b>bold</b> & "quoted"'
        mod._STRINGS["zh-CN"]["_test_raw"] = '[CN] <b>bold</b> & "quoted"'
        try:
            result = mod.t_raw("_test_raw")
            assert result == '<b>bold</b> & "quoted"'
        finally:
            del mod._STRINGS["en"]["_test_raw"]
            del mod._STRINGS["zh-CN"]["_test_raw"]

    def test_returns_key_for_unknown(self):
        assert mod.t_raw("missing_key_abc") == "missing_key_abc"


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
