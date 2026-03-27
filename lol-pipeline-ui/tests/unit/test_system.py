"""Tests for UI-SYS-1: /system page, /streams redirect, and system fragments."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestSystemPage:
    """GET /system returns the combined system page."""

    @pytest.mark.asyncio
    async def test_system_page__returns_200(self):
        """GET /system returns 200 with 'System' in the body."""
        import fakeredis.aioredis

        from lol_ui.routes.system import show_system

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        request = MagicMock()
        request.app.state.r = r

        resp = await show_system(request)
        body = resp.body.decode()

        assert resp.status_code == 200
        assert "System" in body
        await r.aclose()


class TestStreamsRedirect:
    """GET /streams returns 301 redirect to /system."""

    @pytest.mark.asyncio
    async def test_streams_redirect__301_to_system(self):
        """GET /streams returns 301 pointing to /system."""
        from lol_ui.routes.system import streams_redirect

        resp = await streams_redirect()

        assert resp.status_code == 301
        assert resp.headers["location"] == "/system"


class TestSystemFragmentMetrics:
    """GET /system/fragment/metrics returns a metrics table."""

    @pytest.mark.asyncio
    async def test_system_fragment_metrics__returns_table(self):
        """Fragment returns HTML table with source name in a row."""
        import fakeredis.aioredis

        from lol_ui.routes.system import system_fragment_metrics

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Seed one minute bucket for the "riot" source
        import time

        now_ms = int(time.time() * 1000)
        bucket = now_ms // 60_000
        await r.set(f"ratelimit:riot:rpm:{bucket}", "42")

        request = MagicMock()
        request.app.state.r = r

        resp = await system_fragment_metrics(request)
        body = resp.body.decode()

        assert "<table" in body
        assert "riot" in body
        assert "42" in body
        await r.aclose()


class TestSystemFragmentRatelimiter:
    """GET /system/fragment/ratelimiter returns source status."""

    @pytest.mark.asyncio
    async def test_system_fragment_ratelimiter__shows_sources(self):
        """Fragment returns HTML with known source names."""
        import fakeredis.aioredis

        from lol_ui.routes.system import system_fragment_ratelimiter

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        request = MagicMock()
        request.app.state.r = r

        resp = await system_fragment_ratelimiter(request)
        body = resp.body.decode()

        assert "<table" in body
        assert "riot" in body
        assert "crawler" in body
        assert "fetcher" in body
        await r.aclose()
