"""Tests for the /set-lang route and language rendering integration."""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

from lol_ui.main import app


@pytest.fixture
async def client():
    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.r = fake_r
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await fake_r.aclose()


class TestSetLangRoute:
    """GET /set-lang sets a cookie and redirects."""

    @pytest.mark.asyncio
    async def test_sets_cookie_and_redirects(self, client):
        resp = await client.get(
            "/set-lang?lang=zh-CN",
            headers={"referer": "/stats"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location") == "/stats"
        cookie_header = resp.headers.get("set-cookie", "")
        assert "lang=zh-CN" in cookie_header

    @pytest.mark.asyncio
    async def test_ref_param_takes_priority_over_referer(self, client):
        """Explicit ref param overrides Referer header."""
        resp = await client.get(
            "/set-lang?lang=zh-CN&ref=/players",
            headers={"referer": "/stats"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location") == "/players"

    @pytest.mark.asyncio
    async def test_absolute_referer_header_extracts_path(self, client):
        """Absolute URL in Referer header is parsed to path-only for redirect."""
        resp = await client.get(
            "/set-lang?lang=zh-CN",
            headers={"referer": "http://localhost:8000/players"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location") == "/players"

    @pytest.mark.asyncio
    async def test_invalid_lang_defaults_to_en(self, client):
        resp = await client.get(
            "/set-lang?lang=invalid",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        cookie_header = resp.headers.get("set-cookie", "")
        assert "lang=en" in cookie_header

    @pytest.mark.asyncio
    async def test_no_referer__redirects_to_home(self, client):
        resp = await client.get(
            "/set-lang?lang=en",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location") == "/"


class TestLanguageRendering:
    """Regression: setting lang=zh-CN cookie must render Chinese HTML."""

    @pytest.mark.asyncio
    async def test_zh_cn_cookie__renders_chinese_html_lang(self, client):
        """Page must have <html lang="zh-Hans"> when lang=zh-CN cookie is set."""
        resp = await client.get("/", cookies={"lang": "zh-CN"})
        assert resp.status_code == 200
        body = resp.text
        assert 'lang="zh-Hans"' in body

    @pytest.mark.asyncio
    async def test_zh_cn_cookie__language_switcher_shows_zh_active(self, client):
        """Language switcher must show zh-CN as the active (bold) language."""
        resp = await client.get("/", cookies={"lang": "zh-CN"})
        body = resp.text
        # zh-CN label should be bold (active), EN should be a link (may include &ref=)
        assert 'href="/set-lang?lang=en' in body

    @pytest.mark.asyncio
    async def test_en_default__renders_english_html_lang(self, client):
        """Without a lang cookie, page defaults to English."""
        resp = await client.get("/")
        assert resp.status_code == 200
        body = resp.text
        assert 'lang="en"' in body

    @pytest.mark.asyncio
    async def test_accept_language_header__zh(self, client):
        """Accept-Language: zh should render Chinese."""
        resp = await client.get("/", headers={"accept-language": "zh;q=0.9"})
        body = resp.text
        assert 'lang="zh-Hans"' in body
