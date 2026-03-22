"""Tests for the language detection and switcher module (language.py)."""

from __future__ import annotations

from dataclasses import dataclass, field

from lol_ui.language import get_lang, language_switcher_html, set_lang_cookie


@dataclass
class _FakeRequest:
    """Minimal request stand-in for get_lang() tests."""

    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)


class _FakeResponse:
    """Minimal response stand-in for set_lang_cookie() tests."""

    def __init__(self) -> None:
        self._cookies: dict[str, dict[str, object]] = {}

    def set_cookie(self, **kwargs: object) -> None:
        self._cookies[str(kwargs.get("key", ""))] = kwargs


class TestGetLang:
    """get_lang() resolves language from cookie / header / default."""

    def test_defaults_to_english(self):
        req = _FakeRequest()
        assert get_lang(req) == "en"

    def test_reads_lang_cookie(self):
        req = _FakeRequest(cookies={"lang": "zh-CN"})
        assert get_lang(req) == "zh-CN"

    def test_ignores_invalid_cookie(self):
        req = _FakeRequest(cookies={"lang": "fr-FR"})
        assert get_lang(req) == "en"

    def test_falls_back_to_accept_language_header(self):
        req = _FakeRequest(headers={"accept-language": "zh-CN,en;q=0.9"})
        assert get_lang(req) == "zh-CN"

    def test_accept_language__base_match(self):
        """Accept-Language 'zh' matches 'zh-CN'."""
        req = _FakeRequest(headers={"accept-language": "zh;q=0.8,en;q=0.5"})
        assert get_lang(req) == "zh-CN"

    def test_cookie_takes_precedence_over_header(self):
        req = _FakeRequest(
            cookies={"lang": "en"},
            headers={"accept-language": "zh-CN"},
        )
        assert get_lang(req) == "en"

    def test_unsupported_accept_language__defaults(self):
        req = _FakeRequest(headers={"accept-language": "de-DE,fr;q=0.9"})
        assert get_lang(req) == "en"


class TestSetLangCookie:
    """set_lang_cookie() sets the cookie on the response."""

    def test_sets_valid_lang(self):
        resp = _FakeResponse()
        set_lang_cookie(resp, "zh-CN")
        cookie = resp._cookies.get("lang", {})
        assert cookie.get("value") == "zh-CN"
        assert cookie.get("httponly") is True

    def test_invalid_lang_falls_back_to_en(self):
        resp = _FakeResponse()
        set_lang_cookie(resp, "xx-XX")
        cookie = resp._cookies.get("lang", {})
        assert cookie.get("value") == "en"


class TestLanguageSwitcherHtml:
    """language_switcher_html() renders the switcher for the nav bar."""

    def test_current_lang_is_bold(self):
        html = language_switcher_html("en")
        assert "font-weight:700" in html
        assert ">EN<" in html

    def test_other_lang_is_link(self):
        html = language_switcher_html("en")
        assert 'href="/set-lang?lang=zh-CN"' in html

    def test_zh_cn_current(self):
        html = language_switcher_html("zh-CN")
        assert 'href="/set-lang?lang=en"' in html
        # zh-CN label should be bold
        assert "\u4e2d\u6587" in html

    def test_contains_lang_switcher_class(self):
        html = language_switcher_html("en")
        assert "lang-switcher" in html
