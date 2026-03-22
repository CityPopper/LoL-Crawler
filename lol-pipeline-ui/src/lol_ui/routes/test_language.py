"""Tests for the /set-lang route."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from lol_ui.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


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
