"""Tests for the theme module (themes.py)."""

from __future__ import annotations

from dataclasses import dataclass, field

from lol_ui.themes import (
    SUPPORTED_THEMES,
    get_theme,
    get_theme_css,
    set_theme_cookie,
    theme_switcher_html,
)


@dataclass
class _FakeRequest:
    """Minimal request stand-in for get_theme() tests."""

    cookies: dict[str, str] = field(default_factory=dict)


class _FakeResponse:
    """Minimal response stand-in for set_theme_cookie() tests."""

    def __init__(self) -> None:
        self._cookies: dict[str, dict[str, object]] = {}

    def set_cookie(self, **kwargs: object) -> None:
        self._cookies[str(kwargs.get("key", ""))] = kwargs


class TestGetTheme:
    """get_theme() resolves theme from cookie / default."""

    def test_defaults_to_default(self):
        req = _FakeRequest()
        assert get_theme(req) == "default"

    def test_reads_theme_cookie(self):
        req = _FakeRequest(cookies={"theme": "artpop"})
        assert get_theme(req) == "artpop"

    def test_ignores_invalid_cookie(self):
        req = _FakeRequest(cookies={"theme": "neon"})
        assert get_theme(req) == "default"

    def test_empty_cookie_returns_default(self):
        req = _FakeRequest(cookies={"theme": ""})
        assert get_theme(req) == "default"


class TestSetThemeCookie:
    """set_theme_cookie() sets the cookie on the response."""

    def test_sets_valid_theme(self):
        resp = _FakeResponse()
        set_theme_cookie(resp, "artpop")
        cookie = resp._cookies.get("theme", {})
        assert cookie.get("value") == "artpop"
        assert cookie.get("httponly") is True
        assert cookie.get("samesite") == "lax"

    def test_invalid_theme_falls_back_to_default(self):
        resp = _FakeResponse()
        set_theme_cookie(resp, "neon")
        cookie = resp._cookies.get("theme", {})
        assert cookie.get("value") == "default"

    def test_max_age_is_set(self):
        resp = _FakeResponse()
        set_theme_cookie(resp, "default")
        cookie = resp._cookies.get("theme", {})
        assert cookie.get("max_age") == 365 * 24 * 3600


class TestGetThemeCss:
    """get_theme_css() returns CSS override for each theme."""

    def test_default_returns_empty(self):
        assert get_theme_css("default") == ""

    def test_artpop_returns_css_vars(self):
        css = get_theme_css("artpop")
        assert ":root" in css
        assert "--color-bg" in css
        assert "#0d0d0d" in css

    def test_artpop_has_decorative_shapes(self):
        css = get_theme_css("artpop")
        assert "theme-artpop::before" in css
        assert "theme-artpop::after" in css
        assert "radial-gradient" in css

    def test_artpop_has_gradient_button(self):
        css = get_theme_css("artpop")
        assert "linear-gradient" in css

    def test_unknown_theme_returns_empty(self):
        assert get_theme_css("nonexistent") == ""


class TestThemeSwitcherHtml:
    """theme_switcher_html() renders the switcher dropdown."""

    def test_contains_theme_switcher_class(self):
        html = theme_switcher_html("default")
        assert "theme-switcher" in html

    def test_default_is_selected(self):
        html = theme_switcher_html("default")
        assert 'value="default" selected' in html

    def test_artpop_is_selected_when_active(self):
        html = theme_switcher_html("artpop")
        assert 'value="artpop" selected' in html
        # default should NOT be selected
        assert 'value="default" selected' not in html

    def test_links_to_set_theme_route(self):
        html = theme_switcher_html("default")
        assert "/set-theme?theme=" in html
        assert "encodeURIComponent" in html

    def test_all_themes_present(self):
        html = theme_switcher_html("default")
        for theme in SUPPORTED_THEMES:
            assert f'value="{theme}"' in html

    def test_has_label(self):
        html = theme_switcher_html("default")
        assert "Theme:" in html
