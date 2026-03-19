"""Unit tests for lol_ui.main — helper functions and data loading."""

from __future__ import annotations

import html
import json
from pathlib import Path

from lol_ui.main import (
    _load_lcu_data,
    _lcu_stats_section,
    _match_history_section,
    _page,
    _stats_form,
    _stats_table,
)


class TestLoadLcuData:
    def test_missing_directory(self, tmp_path):
        result = _load_lcu_data(str(tmp_path / "nonexistent"))
        assert result == {}

    def test_empty_directory(self, tmp_path):
        result = _load_lcu_data(str(tmp_path))
        assert result == {}

    def test_valid_jsonl_files(self, tmp_path):
        f = tmp_path / "puuid1.jsonl"
        f.write_text(json.dumps({"game_id": 1, "win": True}) + "\n"
                     + json.dumps({"game_id": 2, "win": False}) + "\n")
        result = _load_lcu_data(str(tmp_path))
        assert "puuid1" in result
        assert len(result["puuid1"]) == 2

    def test_malformed_jsonl_lines_skipped(self, tmp_path):
        f = tmp_path / "puuid2.jsonl"
        f.write_text('{"game_id": 1}\nnot-json\n{"game_id": 2}\n')
        result = _load_lcu_data(str(tmp_path))
        assert len(result["puuid2"]) == 2  # malformed line skipped

    def test_empty_jsonl_file_excluded(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        result = _load_lcu_data(str(tmp_path))
        assert "empty" not in result

    def test_blank_lines_skipped(self, tmp_path):
        f = tmp_path / "p.jsonl"
        f.write_text('\n  \n{"game_id": 1}\n\n')
        result = _load_lcu_data(str(tmp_path))
        assert len(result["p"]) == 1

    def test_multiple_puuids(self, tmp_path):
        for name in ["aaa", "bbb", "ccc"]:
            f = tmp_path / f"{name}.jsonl"
            f.write_text(json.dumps({"game_id": 1}) + "\n")
        result = _load_lcu_data(str(tmp_path))
        assert len(result) == 3


class TestLcuStatsSection:
    def test_empty_matches(self):
        html_out = _lcu_stats_section([])
        assert "Total Games" in html_out
        assert "<td>0</td>" in html_out

    def test_single_match(self):
        matches = [{"win": True, "game_mode": "CLASSIC"}]
        html_out = _lcu_stats_section(matches)
        assert "<td>1</td>" in html_out  # total
        assert "CLASSIC" in html_out

    def test_multiple_modes(self):
        matches = [
            {"win": True, "game_mode": "CLASSIC"},
            {"win": False, "game_mode": "ARAM"},
            {"win": True, "game_mode": "CLASSIC"},
        ]
        html_out = _lcu_stats_section(matches)
        assert "CLASSIC" in html_out
        assert "ARAM" in html_out

    def test_missing_game_mode_uses_unknown(self):
        matches = [{"win": True}]
        html_out = _lcu_stats_section(matches)
        assert "UNKNOWN" in html_out


class TestMatchHistorySection:
    def test_renders_with_safe_values(self):
        html_out = _match_history_section("puuid-abc", "na1", "Player#NA1")
        assert "puuid-abc" in html_out
        assert "na1" in html_out
        assert "Player#NA1" in html_out

    def test_html_escapes_special_chars(self):
        html_out = _match_history_section("p<script>", "r&gn", "P<>T#1")
        # The raw dangerous value must not appear unescaped
        assert "p<script>" not in html_out
        assert html.escape("p<script>") in html_out
        assert html.escape("r&gn") in html_out


class TestPage:
    def test_renders_html_structure(self):
        result = _page("Test Title", "<p>body</p>")
        assert "<!doctype html>" in result
        assert "Test Title" in result
        assert "<p>body</p>" in result
        assert "<nav>" in result

    def test_contains_navigation_links(self):
        result = _page("X", "")
        assert "/stats" in result
        assert "/players" in result
        assert "/streams" in result
        assert "/lcu" in result
        assert "/logs" in result


class TestStatsForm:
    def test_empty_form(self):
        result = _stats_form()
        assert "Riot ID" in result
        assert "Look Up" in result

    def test_with_message(self):
        result = _stats_form("Player not found", "error")
        assert "Player not found" in result
        assert 'class="error"' in result

    def test_with_stats_html(self):
        result = _stats_form(stats_html="<table>data</table>")
        assert "<table>data</table>" in result


class TestStatsTable:
    def test_renders_stats(self):
        stats = {"wins": "10", "losses": "5"}
        champs = [("Zed", 15.0), ("Yasuo", 10.0)]
        roles = [("MID", 20.0)]
        result = _stats_table(stats, champs, roles)
        assert "Zed" in result
        assert "Yasuo" in result
        assert "MID" in result
        assert "10" in result

    def test_empty_champs_and_roles(self):
        result = _stats_table({}, [], [])
        assert "No data" in result

    def test_html_escapes_stat_values(self):
        stats = {"<script>xss</script>": "val"}
        result = _stats_table(stats, [], [])
        assert "<script>" not in result
        assert html.escape("<script>xss</script>") in result
