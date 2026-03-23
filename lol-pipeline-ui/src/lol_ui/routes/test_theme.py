"""Tests for the /set-theme route and theme rendering integration."""

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


class TestSetThemeRoute:
    """GET /set-theme sets a cookie and redirects."""

    @pytest.mark.asyncio
    async def test_sets_cookie_and_redirects(self, client):
        resp = await client.get(
            "/set-theme?theme=artpop",
            headers={"referer": "/stats"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location") == "/stats"
        cookie_header = resp.headers.get("set-cookie", "")
        assert "theme=artpop" in cookie_header

    @pytest.mark.asyncio
    async def test_invalid_theme_defaults_to_default(self, client):
        resp = await client.get(
            "/set-theme?theme=invalid",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        cookie_header = resp.headers.get("set-cookie", "")
        assert "theme=default" in cookie_header

    @pytest.mark.asyncio
    async def test_no_referer__redirects_to_home(self, client):
        resp = await client.get(
            "/set-theme?theme=artpop",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers.get("location") == "/"

    @pytest.mark.asyncio
    async def test_open_redirect_blocked(self, client):
        resp = await client.get(
            "/set-theme?theme=artpop",
            headers={"referer": "https://evil.example.com"},
            follow_redirects=False,
        )
        assert resp.headers.get("location") == "/"


class TestThemeRendering:
    """Theme cookie changes the rendered HTML."""

    @pytest.mark.asyncio
    async def test_default_theme__no_body_class(self, client):
        resp = await client.get("/")
        body = resp.text
        assert "<body>" in body
        assert "theme-artpop" not in body

    @pytest.mark.asyncio
    async def test_artpop_cookie__adds_body_class(self, client):
        resp = await client.get("/", cookies={"theme": "artpop"})
        body = resp.text
        assert 'class="theme-artpop"' in body

    @pytest.mark.asyncio
    async def test_artpop_cookie__injects_theme_css(self, client):
        resp = await client.get("/", cookies={"theme": "artpop"})
        body = resp.text
        # Art Pop CSS vars should be present
        assert "#0d0d0d" in body
        assert "theme-artpop::before" in body

    @pytest.mark.asyncio
    async def test_theme_switcher_present(self, client):
        resp = await client.get("/")
        body = resp.text
        assert "theme-switcher" in body
        assert "Theme:" in body

    @pytest.mark.asyncio
    async def test_artpop_theme_switcher_shows_artpop_selected(self, client):
        resp = await client.get("/", cookies={"theme": "artpop"})
        body = resp.text
        assert 'value="artpop" selected' in body

    @pytest.mark.asyncio
    async def test_invalid_cookie__falls_back_to_default(self, client):
        resp = await client.get("/", cookies={"theme": "neon"})
        body = resp.text
        # Should render as default theme (no body class)
        assert "<body>" in body
        assert "theme-neon" not in body
