"""Tests for routes/logs.py — service filter, clear button, expandable entries."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

from lol_ui.routes.logs import _service_filter_html, logs_fragment, show_logs


class TestServiceFilterHtml:
    """_service_filter_html renders a dropdown with service options."""

    def test_default__no_selection(self):
        result = _service_filter_html("")
        assert '<option value="">All services</option>' in result
        assert "selected" not in result.split("All services")[1].split("</option>")[0]

    def test_selected_service__has_selected_attr(self):
        result = _service_filter_html("crawler")
        assert 'value="crawler" selected' in result

    def test_all_services_present(self):
        result = _service_filter_html("")
        for svc in [
            "crawler",
            "fetcher",
            "parser",
            "analyzer",
            "recovery",
            "delay-scheduler",
            "discovery",
            "ui",
        ]:
            assert svc in result


class TestLogsFragmentServiceFilter:
    """logs_fragment passes service query param to _merged_log_lines."""

    @pytest.mark.asyncio
    async def test_fragment__passes_service_filter(self, tmp_path):
        log_file = tmp_path / "crawler.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        mock_cfg = MagicMock()
        mock_cfg.log_dir = str(tmp_path)
        request = MagicMock()
        request.app.state.cfg = mock_cfg
        request.query_params = {"service": "crawler"}

        captured = {}
        original_to_thread = __import__("asyncio").to_thread

        async def tracking_to_thread(func, *args, **kwargs):
            captured["args"] = args
            return await original_to_thread(func, *args, **kwargs)

        with patch("lol_ui.routes.logs.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = tracking_to_thread
            await logs_fragment(request)

        # Third positional arg to _merged_log_lines is service_filter
        assert captured["args"][2] == "crawler"

    @pytest.mark.asyncio
    async def test_fragment__invalid_service_sanitized(self, tmp_path):
        log_file = tmp_path / "svc.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        mock_cfg = MagicMock()
        mock_cfg.log_dir = str(tmp_path)
        request = MagicMock()
        request.app.state.cfg = mock_cfg
        request.query_params = {"service": "../../../etc/passwd"}

        captured = {}
        original_to_thread = __import__("asyncio").to_thread

        async def tracking_to_thread(func, *args, **kwargs):
            captured["args"] = args
            return await original_to_thread(func, *args, **kwargs)

        with patch("lol_ui.routes.logs.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = tracking_to_thread
            await logs_fragment(request)

        # Invalid service name should be sanitized to ""
        assert captured["args"][2] == ""


class TestShowLogsClearButton:
    """show_logs renders a Clear button."""

    @pytest.mark.asyncio
    async def test_show_logs__has_clear_button(self, tmp_path):
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        log_file = tmp_path / "svc.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        mock_cfg = MagicMock()
        mock_cfg.log_dir = str(tmp_path)
        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = mock_cfg
        request.query_params = {}

        resp = await show_logs(request)
        body = bytes(resp.body).decode()
        assert 'id="clear-btn"' in body
        assert "Clear" in body
        await r.aclose()


class TestShowLogsServiceFilterDropdown:
    """show_logs renders a service filter dropdown."""

    @pytest.mark.asyncio
    async def test_show_logs__has_service_dropdown(self, tmp_path):
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        log_file = tmp_path / "svc.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        mock_cfg = MagicMock()
        mock_cfg.log_dir = str(tmp_path)
        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = mock_cfg
        request.query_params = {}

        resp = await show_logs(request)
        body = bytes(resp.body).decode()
        assert 'id="svc-filter"' in body
        assert "crawler" in body
        assert "All services" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_logs__js_uses_service_filter(self, tmp_path):
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        log_file = tmp_path / "svc.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        mock_cfg = MagicMock()
        mock_cfg.log_dir = str(tmp_path)
        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = mock_cfg
        request.query_params = {}

        resp = await show_logs(request)
        body = bytes(resp.body).decode()
        # JS should pass service param in fetch URL
        assert "service=" in body
        assert "svcSelect.value" in body
        await r.aclose()


class TestLogsUsesIsSystemHalted:
    """DRY-5: Logs route uses is_system_halted() instead of raw r.get."""

    @pytest.mark.asyncio
    async def test_show_logs__calls_is_system_halted(self, tmp_path):
        """show_logs uses is_system_halted() for halt check."""
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        log_file = tmp_path / "svc.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        mock_cfg = MagicMock()
        mock_cfg.log_dir = str(tmp_path)
        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = mock_cfg
        request.query_params = {}

        mock_halted = AsyncMock(return_value=False)
        with patch("lol_ui.routes.logs.is_system_halted", mock_halted):
            await show_logs(request)
        mock_halted.assert_called_once()
        await r.aclose()
