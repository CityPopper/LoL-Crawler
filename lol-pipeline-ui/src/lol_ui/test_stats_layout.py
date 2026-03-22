"""Tests for the sticky two-column layout on the stats page (T2-5)."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_ui.css import _CSS


class TestStatsLayoutCss:
    """CSS contains the two-column stats-layout rules."""

    def test_stats_layout_grid__present(self):
        assert ".stats-layout" in _CSS

    def test_stats_layout__grid_template_columns_300px(self):
        assert "grid-template-columns: 300px 1fr" in _CSS

    def test_stats_sidebar__sticky_positioning(self):
        assert "position: sticky" in _CSS

    def test_stats_sidebar__max_height(self):
        assert "max-height: 100vh" in _CSS

    def test_stats_sidebar__overflow_auto(self):
        assert "overflow-y: auto" in _CSS

    def test_stats_sidebar__align_self_start(self):
        assert "align-self: start" in _CSS

    def test_mobile_breakpoint__single_column(self):
        # Mobile should collapse to single column
        assert "grid-template-columns: 1fr" in _CSS

    def test_mobile_breakpoint__static_sidebar(self):
        assert "position: static" in _CSS


class TestStatsLayoutHtml:
    """_build_stats_response wraps content in a two-column layout."""

    @pytest.mark.asyncio
    async def test_response_contains_stats_layout_wrapper(self):
        from lol_ui.routes.stats import _build_stats_response

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        puuid = "layout-test-puuid"
        stats = {"total_games": "50", "win_rate": "0.6"}
        resp = await _build_stats_response(
            r, puuid, "LayoutTest", "NA1", "na1", "LayoutTest#NA1", stats
        )
        body = bytes(resp.body).decode()
        assert 'class="stats-layout"' in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_response_contains_sidebar_div(self):
        from lol_ui.routes.stats import _build_stats_response

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        puuid = "sidebar-test-puuid"
        stats = {"total_games": "50", "win_rate": "0.6"}
        resp = await _build_stats_response(
            r, puuid, "SidebarTest", "NA1", "na1", "SidebarTest#NA1", stats
        )
        body = bytes(resp.body).decode()
        assert 'class="stats-sidebar"' in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_response_contains_main_div(self):
        from lol_ui.routes.stats import _build_stats_response

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        puuid = "main-test-puuid"
        stats = {"total_games": "50", "win_rate": "0.6"}
        resp = await _build_stats_response(
            r, puuid, "MainTest", "NA1", "na1", "MainTest#NA1", stats
        )
        body = bytes(resp.body).decode()
        assert 'class="stats-main"' in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_sidebar_before_main(self):
        """Sidebar div appears before main div in the HTML."""
        from lol_ui.routes.stats import _build_stats_response

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        puuid = "order-test-puuid"
        stats = {"total_games": "50", "win_rate": "0.6"}
        resp = await _build_stats_response(
            r, puuid, "OrderTest", "NA1", "na1", "OrderTest#NA1", stats
        )
        body = bytes(resp.body).decode()
        sidebar_pos = body.index("stats-sidebar")
        main_pos = body.index("stats-main")
        assert sidebar_pos < main_pos
        await r.aclose()

    @pytest.mark.asyncio
    async def test_match_history_in_main_column(self):
        """Match history container should be inside the main column, not sidebar."""
        from lol_ui.routes.stats import _build_stats_response

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        puuid = "history-col-test-puuid"
        stats = {"total_games": "50", "win_rate": "0.6"}
        resp = await _build_stats_response(
            r, puuid, "HistTest", "NA1", "na1", "HistTest#NA1", stats
        )
        body = bytes(resp.body).decode()
        # Match history section should appear after stats-main
        main_pos = body.index("stats-main")
        history_pos = body.index("match-history-container")
        assert history_pos > main_pos
        await r.aclose()
