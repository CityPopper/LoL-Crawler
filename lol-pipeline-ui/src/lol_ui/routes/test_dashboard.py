"""Tests for dashboard route — language rendering."""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

from lol_ui.main import app


@pytest.fixture
async def client():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.r = r
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await r.aclose()


class TestDashboardChinese:
    """When lang=zh-CN cookie is set, dashboard text must be in Chinese."""

    @pytest.mark.asyncio
    async def test_zh_cn__system_status(self, client: AsyncClient) -> None:
        resp = await client.get("/", cookies={"lang": "zh-CN"})
        body = bytes(resp.content).decode()
        # "System Status" should be translated, not English
        assert "System Status" not in body
        assert "系统状态" in body

    @pytest.mark.asyncio
    async def test_zh_cn__dashboard_title(self, client: AsyncClient) -> None:
        resp = await client.get("/", cookies={"lang": "zh-CN"})
        body = bytes(resp.content).decode()
        assert "仪表盘" in body

    @pytest.mark.asyncio
    async def test_zh_cn__players_tracked(self, client: AsyncClient) -> None:
        resp = await client.get("/", cookies={"lang": "zh-CN"})
        body = bytes(resp.content).decode()
        assert "Players Tracked" not in body

    @pytest.mark.asyncio
    async def test_en__shows_english(self, client: AsyncClient) -> None:
        resp = await client.get("/", cookies={"lang": "en"})
        body = bytes(resp.content).decode()
        assert "System Status" in body
        assert "Dashboard" in body

    @pytest.mark.asyncio
    async def test_zh_cn__nav_in_chinese(self, client: AsyncClient) -> None:
        """Nav links must render in Chinese when zh-CN is active."""
        resp = await client.get("/", cookies={"lang": "zh-CN"})
        body = bytes(resp.content).decode()
        # English nav labels must NOT appear
        assert ">Dashboard<" not in body
        assert ">Stats<" not in body
        assert ">Champions<" not in body
        assert ">Streams<" not in body
        assert ">DLQ<" not in body
        assert ">Logs<" not in body
        # Chinese nav labels must appear
        assert "仪表盘" in body  # Dashboard
        assert "数据" in body  # Stats
        assert "英雄" in body  # Champions
        assert "流" in body  # Streams
        assert "死信" in body  # DLQ
        assert "日志" in body  # Logs

    @pytest.mark.asyncio
    async def test_zh_cn__footer_in_chinese(self, client: AsyncClient) -> None:
        """Footer disclaimer must render in Chinese when zh-CN is active."""
        resp = await client.get("/", cookies={"lang": "zh-CN"})
        body = bytes(resp.content).decode()
        # English footer fragments must NOT appear
        assert "endorsed" not in body
        assert "reflect the views" not in body

    @pytest.mark.asyncio
    async def test_zh_cn__redis_error_in_chinese(self, client: AsyncClient) -> None:
        """Redis error page must render in Chinese when zh-CN is active."""
        # Force a Redis error by closing the connection

        from lol_ui.language import _current_lang

        # We test the error handler rendering directly
        from lol_ui.rendering import _page
        from lol_ui.strings import t

        token = _current_lang.set("zh-CN")
        try:
            body = _page(t("error_title"), f"<p>{t('redis_error')}</p>")
            assert "Cannot connect to Redis" not in body
        finally:
            _current_lang.reset(token)

    @pytest.mark.asyncio
    async def test_zh_cn__stats_form_labels_in_chinese(self, client: AsyncClient) -> None:
        """Stats form labels must render in Chinese when zh-CN is active."""
        resp = await client.get("/stats", cookies={"lang": "zh-CN"})
        body = bytes(resp.content).decode()
        # English form labels must NOT appear
        assert "Riot ID:" not in body
        assert "Region:" not in body
        assert ">Look Up<" not in body
        assert ">Player Stats<" not in body

    @pytest.mark.asyncio
    async def test_en__nav_in_english(self, client: AsyncClient) -> None:
        """Nav links must render in English when en is active."""
        resp = await client.get("/", cookies={"lang": "en"})
        body = bytes(resp.content).decode()
        assert ">Dashboard<" in body
        assert ">Stats<" in body
        assert ">Champions<" in body

    @pytest.mark.asyncio
    async def test_en__footer_in_english(self, client: AsyncClient) -> None:
        """Footer disclaimer must render in English when en is active."""
        resp = await client.get("/", cookies={"lang": "en"})
        body = bytes(resp.content).decode()
        assert "endorsed" in body


class TestDashboardHashEncoding:
    """Dashboard lookup form must encode # so the browser doesn't strip it."""

    @pytest.mark.asyncio
    async def test_dashboard_form__has_hash_encoding_js(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        body = bytes(resp.content).decode()
        assert "encodeURIComponent" in body
