"""Unit tests for lol_ui.main — helper functions and data loading."""

from __future__ import annotations

import html
import json
from pathlib import Path

from lol_ui.main import (
    _aggregate_by_mode,
    _load_lcu_data,
    _lcu_stats_section,
    _match_history_html,
    _match_history_section,
    _merged_log_lines,
    _page,
    _parse_log_line,
    _render_log_lines,
    _stats_form,
    _stats_table,
    _tail_file,
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


class TestAggregateByMode:
    def test_empty_matches(self):
        assert _aggregate_by_mode([]) == {}

    def test_single_mode(self):
        matches = [{"game_mode": "CLASSIC", "win": True}, {"game_mode": "CLASSIC", "win": False}]
        result = _aggregate_by_mode(matches)
        assert result == {"CLASSIC": {"t": 2, "w": 1}}

    def test_multiple_modes(self):
        matches = [
            {"game_mode": "CLASSIC", "win": True},
            {"game_mode": "ARAM", "win": False},
            {"game_mode": "CLASSIC", "win": True},
        ]
        result = _aggregate_by_mode(matches)
        assert result["CLASSIC"] == {"t": 2, "w": 2}
        assert result["ARAM"] == {"t": 1, "w": 0}

    def test_missing_game_mode_uses_unknown(self):
        matches = [{"win": True}]
        result = _aggregate_by_mode(matches)
        assert "UNKNOWN" in result


class TestMatchHistoryHtml:
    def test_empty_matches(self):
        result = _match_history_html([], "puuid", "na1", "P#1", 0, False)
        assert "No match history" in result

    def test_renders_match_rows(self):
        matches = [
            ("NA1_123", {"game_start": "1700000000000", "game_mode": "CLASSIC"},
             {"win": "1", "champion_name": "Zed", "kills": "10", "deaths": "2", "assists": "5"}),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert "Zed" in result
        assert "10/2/5" in result
        assert "Win" in result

    def test_loss_renders_correctly(self):
        matches = [
            ("NA1_456", {"game_start": "1700000000000", "game_mode": "ARAM"},
             {"win": "0", "champion_name": "Ahri", "kills": "3", "deaths": "7", "assists": "1"}),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert "Loss" in result
        assert "Ahri" in result

    def test_has_more_shows_load_link(self):
        matches = [
            ("NA1_123", {"game_start": "0", "game_mode": "SR"},
             {"win": "1", "champion_name": "X", "kills": "0", "deaths": "0", "assists": "0"}),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, True)
        assert "Load more" in result
        assert "page 2" in result

    def test_no_more_hides_load_link(self):
        matches = [
            ("NA1_123", {"game_start": "0", "game_mode": "SR"},
             {"win": "1", "champion_name": "X", "kills": "0", "deaths": "0", "assists": "0"}),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert "Load more" not in result

    def test_html_escapes_champion_name(self):
        matches = [
            ("NA1_1", {"game_start": "0", "game_mode": "SR"},
             {"win": "0", "champion_name": "<b>XSS</b>", "kills": "0", "deaths": "0",
              "assists": "0"}),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert "<b>XSS</b>" not in result
        assert html.escape("<b>XSS</b>") in result


class TestTailFile:
    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.log"
        f.write_text("")
        assert _tail_file(f, 10) == []

    def test_returns_last_n_lines(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")
        result = _tail_file(f, 3)
        assert len(result) == 3
        assert result[-1] == "line5"

    def test_fewer_lines_than_n(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("line1\nline2\n")
        result = _tail_file(f, 10)
        assert len(result) == 2

    def test_missing_file(self, tmp_path):
        f = tmp_path / "missing.log"
        assert _tail_file(f, 10) == []

    def test_blank_lines_skipped(self, tmp_path):
        f = tmp_path / "test.log"
        f.write_text("line1\n\n\nline2\n\n")
        result = _tail_file(f, 10)
        assert len(result) == 2


class TestParseLogLine:
    def test_valid_json_log(self):
        line = json.dumps({
            "timestamp": "2025-01-01T12:00:00.000",
            "level": "ERROR",
            "logger": "crawler",
            "message": "something failed",
            "extra_key": "val",
        })
        ts, level, logger, msg, extra = _parse_log_line(line)
        assert ts == "2025-01-01 12:00:00"
        assert level == "ERROR"
        assert logger == "crawler"
        assert msg == "something failed"
        assert "extra_key=val" in extra

    def test_non_json_line(self):
        ts, level, logger, msg, extra = _parse_log_line("plain text line")
        assert level == "INFO"
        assert msg == "plain text line"

    def test_missing_fields_use_defaults(self):
        line = json.dumps({"message": "hello"})
        ts, level, logger, msg, extra = _parse_log_line(line)
        assert level == "INFO"
        assert msg == "hello"
        assert ts == ""

    def test_underscore_keys_excluded_from_extra(self):
        line = json.dumps({"message": "hi", "_internal": "skip", "visible": "yes"})
        _, _, _, _, extra = _parse_log_line(line)
        assert "_internal" not in extra
        assert "visible=yes" in extra


class TestRenderLogLines:
    def test_empty_list(self):
        result = _render_log_lines([])
        assert "No log entries" in result

    def test_renders_log_entries(self):
        lines = [json.dumps({"timestamp": "2025-01-01T00:00:00", "level": "INFO",
                             "logger": "test", "message": "hello"})]
        result = _render_log_lines(lines)
        assert "hello" in result
        assert "log-line" in result

    def test_html_escapes_message(self):
        lines = [json.dumps({"message": "<script>alert(1)</script>", "level": "ERROR"})]
        result = _render_log_lines(lines)
        assert "<script>" not in result
        assert html.escape("<script>alert(1)</script>") in result


class TestMergedLogLines:
    def test_empty_dir(self, tmp_path):
        result = _merged_log_lines(tmp_path, 10)
        assert result == []

    def test_merges_multiple_files(self, tmp_path):
        f1 = tmp_path / "svc1.log"
        f2 = tmp_path / "svc2.log"
        f1.write_text(json.dumps({"timestamp": "2025-01-01T00:00:01", "message": "a"}) + "\n")
        f2.write_text(json.dumps({"timestamp": "2025-01-01T00:00:02", "message": "b"}) + "\n")
        result = _merged_log_lines(tmp_path, 10)
        assert len(result) == 2
        # Should be sorted by timestamp
        assert "a" in result[0]
        assert "b" in result[1]

    def test_limits_to_n_lines(self, tmp_path):
        f = tmp_path / "svc.log"
        lines = [json.dumps({"timestamp": f"2025-01-01T00:00:{i:02d}", "message": str(i)})
                 for i in range(10)]
        f.write_text("\n".join(lines) + "\n")
        result = _merged_log_lines(tmp_path, 3)
        assert len(result) == 3
