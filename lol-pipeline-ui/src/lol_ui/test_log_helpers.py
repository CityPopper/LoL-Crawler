"""Tests for log_helpers.py — expandable entries, service filter, DRY imports."""

from __future__ import annotations

import json
from pathlib import Path

from lol_ui.log_helpers import _merged_log_lines, _render_log_lines


class TestRenderLogLinesExpandable:
    """Log lines render with expandable detail sections."""

    def test_entry_with_extra__has_detail_section(self):
        lines = [
            json.dumps(
                {
                    "timestamp": "2025-01-01T00:00:00",
                    "level": "ERROR",
                    "logger": "crawler",
                    "message": "fail",
                    "puuid": "abc123",
                }
            )
        ]
        result = _render_log_lines(lines)
        assert "log-entry" in result
        assert "log-detail" in result
        assert "puuid=abc123" in result

    def test_entry_without_extra__no_detail_section(self):
        lines = [
            json.dumps(
                {
                    "timestamp": "2025-01-01T00:00:00",
                    "level": "INFO",
                    "logger": "ui",
                    "message": "started",
                }
            )
        ]
        result = _render_log_lines(lines)
        assert "log-entry" in result
        assert "log-detail" not in result

    def test_entry__has_expand_hint_when_extra(self):
        lines = [
            json.dumps(
                {
                    "message": "test",
                    "level": "INFO",
                    "extra_field": "val",
                }
            )
        ]
        result = _render_log_lines(lines)
        assert "log-expand-hint" in result

    def test_entry__no_expand_hint_when_no_extra(self):
        lines = [
            json.dumps(
                {
                    "timestamp": "2025-01-01T00:00:00",
                    "level": "INFO",
                    "logger": "ui",
                    "message": "clean",
                }
            )
        ]
        result = _render_log_lines(lines)
        assert "log-expand-hint" not in result

    def test_entry__onclick_toggles_open(self):
        lines = [
            json.dumps(
                {
                    "message": "test",
                    "level": "INFO",
                    "key": "val",
                }
            )
        ]
        result = _render_log_lines(lines)
        assert "classList.toggle" in result
        assert "'open'" in result

    def test_empty_list__shows_empty_state(self):
        result = _render_log_lines([])
        assert "No log entries" in result


class TestMergedLogLinesServiceFilter:
    """_merged_log_lines supports filtering by service name."""

    def test_filter_by_service__reads_only_that_file(self, tmp_path: Path) -> None:
        (tmp_path / "crawler.log").write_text(
            json.dumps({"timestamp": "2025-01-01T00:00:01", "message": "crawl"}) + "\n"
        )
        (tmp_path / "fetcher.log").write_text(
            json.dumps({"timestamp": "2025-01-01T00:00:02", "message": "fetch"}) + "\n"
        )
        result = _merged_log_lines(tmp_path, 10, "crawler")
        assert len(result) == 1
        assert "crawl" in result[0]

    def test_empty_filter__reads_all_files(self, tmp_path: Path) -> None:
        (tmp_path / "crawler.log").write_text(
            json.dumps({"timestamp": "2025-01-01T00:00:01", "message": "crawl"}) + "\n"
        )
        (tmp_path / "fetcher.log").write_text(
            json.dumps({"timestamp": "2025-01-01T00:00:02", "message": "fetch"}) + "\n"
        )
        result = _merged_log_lines(tmp_path, 10, "")
        assert len(result) == 2

    def test_nonexistent_service__returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "crawler.log").write_text(
            json.dumps({"timestamp": "2025-01-01T00:00:01", "message": "crawl"}) + "\n"
        )
        result = _merged_log_lines(tmp_path, 10, "nonexistent")
        assert result == []


class TestLogHelpersDryImports:
    """log_helpers.py uses constants from constants.py and rendering.py (no local copies)."""

    def test_log_level_css__imported_from_constants(self):
        from lol_ui import constants, log_helpers

        assert log_helpers._LOG_LEVEL_CSS is constants._LOG_LEVEL_CSS  # type: ignore[attr-defined]

    def test_est_bytes__imported_from_constants(self):
        from lol_ui import constants, log_helpers

        assert log_helpers._EST_BYTES_PER_LOG_LINE is constants._EST_BYTES_PER_LOG_LINE  # type: ignore[attr-defined]

    def test_empty_state__imported_from_rendering(self):
        from lol_ui import log_helpers, rendering

        assert log_helpers._empty_state is rendering._empty_state  # type: ignore[attr-defined]
