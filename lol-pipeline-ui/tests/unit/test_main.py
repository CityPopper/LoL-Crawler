"""Unit tests for lol_ui.main — helper functions and data loading."""

from __future__ import annotations

import html
import json
import re
from unittest.mock import patch

import pytest
from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ServerError,
)

from lol_ui.main import (
    _BADGE_VARIANTS,
    _CSS,
    _NAV_ITEMS,
    _PUUID_RE,
    _STATS_ORDER,
    _aggregate_by_mode,
    _badge,
    _badge_html,
    _depth_badge,
    _empty_state,
    _format_stat_value,
    _lcu_stats_section,
    _load_lcu_data,
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
        f.write_text(
            json.dumps({"game_id": 1, "win": True})
            + "\n"
            + json.dumps({"game_id": 2, "win": False})
            + "\n"
        )
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

    def test_uses_badge_for_unverified(self):
        html_out = _lcu_stats_section([{"win": True, "game_mode": "CLASSIC"}])
        assert "badge badge--warning" in html_out
        assert "Unverified" in html_out

    def test_tables_wrapped_in_scroll_div(self):
        html_out = _lcu_stats_section([{"win": True, "game_mode": "CLASSIC"}])
        assert html_out.count('class="table-scroll"') == 2


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

    def test_no_inline_onclick(self):
        """SEC: match history section must not contain inline onclick handlers."""
        html_out = _match_history_section("puuid-abc", "na1", "Player#NA1")
        assert "onclick=" not in html_out

    def test_uses_data_attributes(self):
        """SEC: match history uses data-* attributes for event delegation."""
        html_out = _match_history_section("puuid-abc", "na1", "Player#NA1")
        assert 'data-puuid="puuid-abc"' in html_out
        assert 'data-region="na1"' in html_out
        assert 'data-riot-id="Player#NA1"' in html_out
        assert 'data-page="0"' in html_out
        assert 'class="load-matches"' in html_out

    def test_event_delegation_script(self):
        """SEC: match history includes event delegation JS."""
        html_out = _match_history_section("puuid-abc", "na1", "Player#NA1")
        assert "document.addEventListener" in html_out
        assert "closest('.load-matches')" in html_out
        assert "dataset.puuid" in html_out


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

    def test_dark_color_scheme_meta(self):
        result = _page("X", "")
        assert '<meta name="color-scheme" content="dark">' in result

    def test_css_uses_custom_properties(self):
        result = _page("X", "")
        assert "--color-bg: #1a1a2e" in result
        assert "--color-surface: #16213e" in result
        assert "--color-text: #e0e0e0" in result

    def test_nav_active_state__stats(self):
        result = _page("X", "", path="/stats")
        assert 'href="/stats" class="active"' in result
        # Other links should not be active
        assert 'href="/players" class="active"' not in result

    def test_nav_active_state__players(self):
        result = _page("X", "", path="/players")
        assert 'href="/players" class="active"' in result
        assert 'href="/stats" class="active"' not in result

    def test_nav_no_active_without_path(self):
        result = _page("X", "")
        assert 'class="active"' not in result

    def test_nav_active_state__all_routes(self):
        for href, _label in _NAV_ITEMS:
            result = _page("X", "", path=href)
            assert f'href="{href}" class="active"' in result


class TestCssConstant:
    def test_css_contains_design_tokens(self):
        assert "--color-bg:" in _CSS
        assert "--color-surface:" in _CSS
        assert "--font-mono:" in _CSS
        assert "--space-md:" in _CSS
        assert "--radius:" in _CSS

    def test_css_contains_component_classes(self):
        assert ".card" in _CSS
        assert ".badge" in _CSS
        assert ".banner" in _CSS
        assert ".stat" in _CSS
        assert ".form-inline" in _CSS
        assert ".table-scroll" in _CSS
        assert ".empty-state" in _CSS

    def test_css_contains_responsive_breakpoints(self):
        assert "@media (min-width: 768px)" in _CSS
        assert "@media (min-width: 1440px)" in _CSS

    def test_css_contains_log_viewer_styles(self):
        assert ".log-wrap" in _CSS
        assert ".log-line" in _CSS
        assert ".log-badge" in _CSS
        assert ".log-ts" in _CSS

    def test_css_dark_log_colors__no_light_artifacts(self):
        assert "#ffe0e0" not in _CSS
        assert "#fff0f0" not in _CSS
        assert "#fffbe6" not in _CSS
        assert "#f0f0f0" not in _CSS

    def test_css_accessibility(self):
        assert ":focus-visible" in _CSS
        assert "prefers-reduced-motion" in _CSS

    def test_css_mobile_first_form(self):
        assert "flex-direction: column" in _CSS


class TestBadge:
    def test_valid_variants(self):
        for variant in _BADGE_VARIANTS:
            result = _badge(variant, "text")
            assert f'class="badge badge--{variant}"' in result
            assert "text" in result

    def test_invalid_variant_raises(self):
        with pytest.raises(ValueError, match="Invalid badge variant"):
            _badge("nonexistent", "text")

    def test_auto_escapes_html_in_text(self):
        """SEC: _badge auto-escapes text to prevent XSS."""
        result = _badge("success", "<script>alert(1)</script>")
        assert "<script>" not in result
        assert html.escape("<script>alert(1)</script>") in result

    def test_returns_span(self):
        result = _badge("info", "test")
        assert result.startswith("<span")
        assert result.endswith("</span>")

    def test_plain_text_preserved(self):
        result = _badge("info", "OK")
        assert "OK" in result

    def test_ampersand_escaped(self):
        result = _badge("info", "A & B")
        assert "&amp;" in result


class TestBadgeHtml:
    def test_valid_variants(self):
        for variant in _BADGE_VARIANTS:
            result = _badge_html(variant, "text")
            assert f'class="badge badge--{variant}"' in result

    def test_invalid_variant_raises(self):
        with pytest.raises(ValueError, match="Invalid badge variant"):
            _badge_html("nonexistent", "text")

    def test_raw_html_preserved(self):
        """_badge_html preserves raw HTML entities."""
        result = _badge_html("success", "&#10003; Verified")
        assert "&#10003; Verified" in result

    def test_returns_span(self):
        result = _badge_html("info", "test")
        assert result.startswith("<span")
        assert result.endswith("</span>")


class TestEmptyState:
    def test_renders_title_and_body(self):
        result = _empty_state("No data", "Try again later.")
        assert "No data" in result
        assert "Try again later." in result
        assert 'class="empty-state"' in result

    def test_raw_html_in_body(self):
        result = _empty_state("Title", 'Run <code>just seed</code>')
        assert "<code>just seed</code>" in result


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

    def test_form_has_inline_class(self):
        result = _stats_form()
        assert 'class="form-inline"' in result

    def test_stats_nav_active(self):
        result = _stats_form()
        assert 'href="/stats" class="active"' in result

    def test_region_default_na1_selected(self):
        result = _stats_form()
        assert 'value="na1"selected' in result or 'value="na1" selected' in result

    def test_region_preserves_euw1_selection(self):
        result = _stats_form(selected_region="euw1")
        assert 'value="euw1"selected' in result or 'value="euw1" selected' in result
        # na1 should NOT be selected
        na1_match = re.search(r'value="na1"[^>]*>', result)
        assert na1_match is not None
        assert "selected" not in na1_match.group(0)

    def test_region_preserves_kr_selection(self):
        result = _stats_form(selected_region="kr")
        assert 'value="kr"selected' in result or 'value="kr" selected' in result


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

    def test_uses_badge_for_verified(self):
        result = _stats_table({"wins": "10"}, [], [])
        assert "badge badge--success" in result
        assert "Verified" in result

    def test_tables_wrapped_in_scroll_div(self):
        result = _stats_table({"wins": "10"}, [("Zed", 5.0)], [("MID", 3.0)])
        assert result.count('class="table-scroll"') == 3


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
            (
                "NA1_123",
                {"game_start": "1700000000000", "game_mode": "CLASSIC"},
                {"win": "1", "champion_name": "Zed", "kills": "10", "deaths": "2", "assists": "5"},
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert "Zed" in result
        assert "10/2/5" in result
        assert "Win" in result

    def test_win_uses_badge(self):
        matches = [
            (
                "NA1_123",
                {"game_start": "1700000000000", "game_mode": "CLASSIC"},
                {"win": "1", "champion_name": "Zed", "kills": "0", "deaths": "0", "assists": "0"},
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert 'class="badge badge--success"' in result
        assert "Win" in result

    def test_loss_renders_correctly(self):
        matches = [
            (
                "NA1_456",
                {"game_start": "1700000000000", "game_mode": "ARAM"},
                {"win": "0", "champion_name": "Ahri", "kills": "3", "deaths": "7", "assists": "1"},
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert "Loss" in result
        assert "Ahri" in result

    def test_loss_uses_badge(self):
        matches = [
            (
                "NA1_456",
                {"game_start": "1700000000000", "game_mode": "ARAM"},
                {"win": "0", "champion_name": "Ahri", "kills": "0", "deaths": "0", "assists": "0"},
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert 'class="badge badge--error"' in result
        assert "Loss" in result

    def test_has_more_shows_load_link(self):
        matches = [
            (
                "NA1_123",
                {"game_start": "0", "game_mode": "SR"},
                {"win": "1", "champion_name": "X", "kills": "0", "deaths": "0", "assists": "0"},
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, True)
        assert "Load more" in result
        assert "page 2" in result

    def test_has_more_uses_data_attributes(self):
        """SEC: load-more link uses data-* attributes, not onclick."""
        matches = [
            (
                "NA1_123",
                {"game_start": "0", "game_mode": "SR"},
                {"win": "1", "champion_name": "X", "kills": "0", "deaths": "0", "assists": "0"},
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, True)
        assert "onclick=" not in result
        assert 'data-puuid="puuid"' in result
        assert 'data-region="na1"' in result
        assert 'class="load-matches"' in result

    def test_no_more_hides_load_link(self):
        matches = [
            (
                "NA1_123",
                {"game_start": "0", "game_mode": "SR"},
                {"win": "1", "champion_name": "X", "kills": "0", "deaths": "0", "assists": "0"},
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert "Load more" not in result

    def test_html_escapes_champion_name(self):
        matches = [
            (
                "NA1_1",
                {"game_start": "0", "game_mode": "SR"},
                {
                    "win": "0",
                    "champion_name": "<b>XSS</b>",
                    "kills": "0",
                    "deaths": "0",
                    "assists": "0",
                },
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert "<b>XSS</b>" not in result
        assert html.escape("<b>XSS</b>") in result

    def test_table_wrapped_in_scroll_div(self):
        matches = [
            (
                "NA1_1",
                {"game_start": "0", "game_mode": "SR"},
                {"win": "1", "champion_name": "X", "kills": "0", "deaths": "0", "assists": "0"},
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert 'class="table-scroll"' in result


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
        line = json.dumps(
            {
                "timestamp": "2025-01-01T12:00:00.000",
                "level": "ERROR",
                "logger": "crawler",
                "message": "something failed",
                "extra_key": "val",
            }
        )
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
        lines = [
            json.dumps(
                {
                    "timestamp": "2025-01-01T00:00:00",
                    "level": "INFO",
                    "logger": "test",
                    "message": "hello",
                }
            )
        ]
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
        lines = [
            json.dumps({"timestamp": f"2025-01-01T00:00:{i:02d}", "message": str(i)})
            for i in range(10)
        ]
        f.write_text("\n".join(lines) + "\n")
        result = _merged_log_lines(tmp_path, 3)
        assert len(result) == 3

    def test_heapq_merge_interleaves_sorted_files(self, tmp_path):
        """CQ-1: heapq.merge correctly interleaves pre-sorted per-file lines."""
        f1 = tmp_path / "svc1.log"
        f2 = tmp_path / "svc2.log"
        # File 1 has timestamps 01, 03, 05; file 2 has 02, 04, 06
        f1.write_text(
            "\n".join(
                json.dumps({"timestamp": f"2025-01-01T00:00:{t:02d}", "message": f"f1-{t}"})
                for t in [1, 3, 5]
            )
            + "\n"
        )
        f2.write_text(
            "\n".join(
                json.dumps({"timestamp": f"2025-01-01T00:00:{t:02d}", "message": f"f2-{t}"})
                for t in [2, 4, 6]
            )
            + "\n"
        )
        result = _merged_log_lines(tmp_path, 6)
        assert len(result) == 6
        # Verify interleaved order: f1-1, f2-2, f1-3, f2-4, f1-5, f2-6
        messages = [json.loads(line)["message"] for line in result]
        assert messages == ["f1-1", "f2-2", "f1-3", "f2-4", "f1-5", "f2-6"]


class TestPuuidValidation:
    """SEC-1: PUUID format validation at the /stats/matches endpoint."""

    def test_valid_puuid_matches_regex(self):
        """Standard alphanumeric PUUID with hyphens and underscores passes."""
        assert _PUUID_RE.match("abc-DEF_123") is not None

    def test_empty_puuid_rejected(self):
        """Empty string is rejected."""
        assert _PUUID_RE.match("") is None

    def test_too_long_puuid_rejected(self):
        """PUUID longer than 128 chars is rejected."""
        assert _PUUID_RE.match("a" * 129) is None

    def test_special_chars_rejected(self):
        """PUUIDs with special characters (injection) are rejected."""
        assert _PUUID_RE.match("puuid:../../etc/passwd") is None
        assert _PUUID_RE.match("puuid<script>") is None
        assert _PUUID_RE.match("puuid with spaces") is None

    def test_max_length_puuid_accepted(self):
        """128-char PUUID is accepted."""
        assert _PUUID_RE.match("a" * 128) is not None


class TestStatsMatchesPipeline:
    """CQ-17: stats_matches uses pipeline for HGETALL calls."""

    @pytest.mark.asyncio
    async def test_hgetall_batched_via_pipeline(self):
        """2 HGETALL per match should go through pipeline, not individual calls."""
        import fakeredis.aioredis

        from lol_ui.main import stats_matches

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Set up match data
        await r.zadd("player:matches:testpuuid", {"NA1_1": 1000.0, "NA1_2": 2000.0})
        await r.hset("match:NA1_1", mapping={"game_start": "1000", "game_mode": "CLASSIC"})
        await r.hset("match:NA1_2", mapping={"game_start": "2000", "game_mode": "ARAM"})
        await r.hset(
            "participant:NA1_1:testpuuid",
            mapping={"win": "1", "champion_name": "Zed", "kills": "5", "deaths": "2", "assists": "3"},
        )
        await r.hset(
            "participant:NA1_2:testpuuid",
            mapping={"win": "0", "champion_name": "Ahri", "kills": "1", "deaths": "4", "assists": "2"},
        )

        # Track direct hgetall calls on r (not pipeline)
        direct_hgetall_count = 0
        original_hgetall = r.hgetall

        async def counting_hgetall(*args, **kwargs):
            nonlocal direct_hgetall_count
            direct_hgetall_count += 1
            return await original_hgetall(*args, **kwargs)

        r.hgetall = counting_hgetall

        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {
            "puuid": "testpuuid",
            "region": "na1",
            "riot_id": "Test#NA1",
            "page": "0",
        }
        request.app.state.r = r

        resp = await stats_matches(request)

        # No direct hgetall calls — all go through pipeline
        assert direct_hgetall_count == 0
        assert resp.status_code == 200
        assert "Zed" in resp.body.decode()
        assert "Ahri" in resp.body.decode()
        await r.aclose()


try:
    import lupa  # noqa: F401

    _LUPA_AVAILABLE = True
except ImportError:
    _LUPA_AVAILABLE = False


class TestAutoSeedPriority:
    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua scripts")
    async def test_show_stats__auto_seed_sets_priority_high(self):
        """Auto-seed envelope has priority='high' and sets player:priority key."""
        import fakeredis.aioredis

        from lol_ui.main import show_stats

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        class FakeRiot:
            async def get_account_by_riot_id(self, gn, tl, region):
                return {"puuid": "test-puuid-ui"}

        class FakeCfg:
            max_attempts = 5

        request = re.Match  # unused, just need a MagicMock
        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = r
        request.app.state.cfg = FakeCfg()
        request.app.state.riot = FakeRiot()
        request.app.state.lcu = {}

        resp = await show_stats(request)

        # Check envelope in stream has priority=high
        entries = await r.xrange("stream:puuid")
        assert len(entries) == 1
        assert entries[0][1]["priority"] == "high"

        # Check priority key was set
        assert await r.get("player:priority:test-puuid-ui") == "high"
        assert await r.get("system:priority_count") == "1"

        await r.aclose()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua scripts")
    async def test_show_streams__displays_priority_count(self):
        """The /streams page displays system:priority_count value."""
        import fakeredis.aioredis

        from lol_ui.main import show_streams

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:priority_count", "3")

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await show_streams(request)
        body = resp.body.decode()
        assert "Priority players in-flight" in body
        assert "<strong>3</strong>" in body

        await r.aclose()


class TestAutoSeedOrdering:
    """CQ-12: publish() must happen before hset(seeded_at) in auto-seed path."""

    @pytest.mark.asyncio
    async def test_publish_before_hset_seeded_at(self):
        """Auto-seed writes to stream:puuid BEFORE marking seeded_at in player hash."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        call_order: list[str] = []

        mock_r = AsyncMock()
        mock_r.get.return_value = None  # no system:halted, no cached puuid
        mock_r.hget.return_value = None  # no existing seeded_at
        mock_r.hgetall.return_value = {}  # no stats
        mock_r.set.return_value = True

        original_hset = mock_r.hset

        async def tracking_hset(key, *args, **kwargs):
            if "seeded_at" in str(kwargs.get("mapping", {})):
                call_order.append("hset_seeded_at")
            return await original_hset(key, *args, **kwargs)

        mock_r.hset = tracking_hset

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.return_value = {"puuid": "test-puuid-123"}

        mock_cfg = MagicMock()
        mock_cfg.max_attempts = 5

        with patch("lol_ui.main.publish", new_callable=AsyncMock) as mock_publish:

            async def tracking_publish(*args, **kwargs):
                call_order.append("publish")

            mock_publish.side_effect = tracking_publish

            request = MagicMock()
            request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
            request.app.state.r = mock_r
            request.app.state.cfg = mock_cfg
            request.app.state.riot = mock_riot
            request.app.state.lcu = {}

            await show_stats(request)

        assert call_order == ["publish", "hset_seeded_at"]


class TestStatsOrder:
    """Sprint 2.1: _STATS_ORDER controls display order in stats table."""

    def test_stats_order__contains_all_core_stats(self):
        """All expected stat keys are present in the order list."""
        expected = [
            "total_games", "total_wins", "win_rate",
            "total_kills", "total_deaths", "total_assists",
            "kda", "avg_kills", "avg_deaths", "avg_assists",
        ]
        for key in expected:
            assert key in _STATS_ORDER, f"{key} missing from _STATS_ORDER"

    def test_stats_order__totals_before_averages(self):
        """Total stats appear before average stats in the order."""
        total_games_idx = _STATS_ORDER.index("total_games")
        avg_kills_idx = _STATS_ORDER.index("avg_kills")
        assert total_games_idx < avg_kills_idx

    def test_stats_order__win_rate_after_total_wins(self):
        """win_rate appears after total_wins."""
        assert _STATS_ORDER.index("win_rate") > _STATS_ORDER.index("total_wins")

    def test_stats_table__uses_ordered_stats(self):
        """Stats table renders in _STATS_ORDER order, not alphabetical."""
        stats = {
            "avg_kills": "7.2",
            "total_games": "150",
            "win_rate": "0.567",
            "total_wins": "85",
        }
        result = _stats_table(stats, [], [])
        # total_games should appear before avg_kills
        pos_total = result.index("total_games")
        pos_avg = result.index("avg_kills")
        assert pos_total < pos_avg, "total_games must appear before avg_kills"

    def test_stats_table__unknown_keys_appended_after_ordered(self):
        """Stats not in _STATS_ORDER are appended alphabetically after known keys."""
        stats = {
            "total_games": "10",
            "some_custom_stat": "42",
        }
        result = _stats_table(stats, [], [])
        pos_total = result.index("total_games")
        pos_custom = result.index("some_custom_stat")
        assert pos_total < pos_custom


class TestFormatStatValue:
    """Sprint 2.1: _format_stat_value formats stats for display."""

    def test_win_rate__formatted_as_percentage(self):
        """win_rate decimal is displayed as percentage with 1 decimal place."""
        assert _format_stat_value("win_rate", "0.567") == "56.7%"

    def test_win_rate__zero(self):
        assert _format_stat_value("win_rate", "0") == "0.0%"

    def test_win_rate__one(self):
        assert _format_stat_value("win_rate", "1") == "100.0%"

    def test_win_rate__invalid_value_returned_as_is(self):
        """Non-numeric win_rate returns the raw value."""
        assert _format_stat_value("win_rate", "N/A") == "N/A"

    def test_avg_stat__rounded_to_2_decimals(self):
        """Average stats are rounded to 2 decimal places."""
        assert _format_stat_value("avg_kills", "7.23456") == "7.23"

    def test_avg_deaths__rounded(self):
        assert _format_stat_value("avg_deaths", "3.1") == "3.10"

    def test_avg_assists__rounded(self):
        assert _format_stat_value("avg_assists", "8.999") == "9.00"

    def test_kda__rounded_to_2_decimals(self):
        assert _format_stat_value("kda", "3.45678") == "3.46"

    def test_non_special_stat__returned_as_is(self):
        """Non-special stats (total_games, etc.) return the raw value."""
        assert _format_stat_value("total_games", "150") == "150"
        assert _format_stat_value("total_wins", "85") == "85"

    def test_avg_invalid_value__returned_as_is(self):
        """Non-numeric avg value returns raw value."""
        assert _format_stat_value("avg_kills", "N/A") == "N/A"

    def test_win_rate__nan_returns_na(self):
        """Fix 5: NaN win_rate returns 'N/A' instead of 'nan%'."""
        assert _format_stat_value("win_rate", "nan") == "N/A"

    def test_win_rate__inf_returns_na(self):
        """Fix 5: Inf win_rate returns 'N/A'."""
        assert _format_stat_value("win_rate", "inf") == "N/A"

    def test_avg_kills__nan_returns_na(self):
        """Fix 5: NaN avg stat returns 'N/A'."""
        assert _format_stat_value("avg_kills", "nan") == "N/A"

    def test_kda__inf_returns_na(self):
        """Fix 5: Inf kda returns 'N/A'."""
        assert _format_stat_value("kda", "inf") == "N/A"

    def test_kda__neg_inf_returns_na(self):
        """Fix 5: -Inf kda returns 'N/A'."""
        assert _format_stat_value("kda", "-inf") == "N/A"

    def test_stats_table__applies_formatting(self):
        """Stats table uses _format_stat_value for rendered values."""
        stats = {"win_rate": "0.567", "avg_kills": "7.23456", "total_games": "100"}
        result = _stats_table(stats, [], [])
        assert "56.7%" in result
        assert "7.23" in result
        assert "100" in result


class TestDepthBadge:
    """Sprint 2.3: _depth_badge returns badge HTML based on stream depth."""

    def test_zero_depth__shows_ok(self):
        result = _depth_badge("stream:puuid", 0)
        assert "badge--success" in result
        assert "OK" in result

    def test_low_depth__shows_ok(self):
        result = _depth_badge("stream:puuid", 50)
        assert "badge--success" in result
        assert "OK" in result

    def test_medium_depth__shows_busy(self):
        result = _depth_badge("stream:puuid", 100)
        assert "badge--warning" in result
        assert "Busy" in result

    def test_high_depth__shows_backlog(self):
        result = _depth_badge("stream:puuid", 1000)
        assert "badge--error" in result
        assert "Backlog" in result

    def test_boundary_99__ok(self):
        result = _depth_badge("stream:puuid", 99)
        assert "badge--success" in result

    def test_boundary_999__busy(self):
        result = _depth_badge("stream:puuid", 999)
        assert "badge--warning" in result

    def test_dlq_nonzero__always_error(self):
        """DLQ with depth > 0 always shows error badge."""
        result = _depth_badge("stream:dlq", 1)
        assert "badge--error" in result

    def test_dlq_zero__shows_ok(self):
        result = _depth_badge("stream:dlq", 0)
        assert "badge--success" in result

    def test_dlq_archive__not_treated_as_dlq(self):
        """stream:dlq:archive is not special-cased like stream:dlq."""
        result = _depth_badge("stream:dlq:archive", 50)
        assert "badge--success" in result


class TestPlayersPageCount:
    """Sprint 2.2: /players shows 'page X of Y' and full ISO timestamps."""

    # These test the show_players output directly via route, but we can test
    # the formatting logic by checking the generated HTML structure.
    pass


class TestLcuPlayerLinks:
    """Sprint 2.4: /lcu links player names to /stats page."""

    # Tested via route-level integration, covered in the show_lcu tests.
    pass


class TestStatsHeading:
    """Sprint 2.1: /stats heading shows Riot ID name only (no PUUID)."""

    # Tested via the show_stats route level.
    pass


class TestStreamsFragment:
    """Sprint 3.1: /streams/fragment returns HTML fragment for AJAX polling."""

    @pytest.mark.asyncio
    async def test_streams_fragment__returns_table_without_page_wrapper(self):
        """Fragment endpoint returns table HTML without <!doctype html> wrapper."""
        import fakeredis.aioredis

        from lol_ui.main import streams_fragment

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await streams_fragment(request)
        body = resp.body.decode()

        assert "<!doctype" not in body.lower()
        assert "<html" not in body.lower()
        assert "stream:puuid" in body
        assert "stream:match_id" in body
        assert "stream:dlq" in body
        assert "delayed:messages" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment__shows_halted_status(self):
        """Fragment shows HALTED banner when system:halted is set."""
        import fakeredis.aioredis

        from lol_ui.main import streams_fragment

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:halted", "1")

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await streams_fragment(request)
        body = resp.body.decode()

        assert "HALTED" in body
        assert "banner--error" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment__shows_running_status(self):
        """Fragment shows running banner when system is not halted."""
        import fakeredis.aioredis

        from lol_ui.main import streams_fragment

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await streams_fragment(request)
        body = resp.body.decode()

        assert "System running" in body
        assert "banner--success" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment__includes_priority_count(self):
        """Fragment includes priority count display."""
        import fakeredis.aioredis

        from lol_ui.main import streams_fragment

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:priority_count", "5")

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await streams_fragment(request)
        body = resp.body.decode()

        assert "<strong>5</strong>" in body
        await r.aclose()


class TestStreamsAutoRefresh:
    """Sprint 3.1: /streams page has auto-refresh JS and pause button."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua scripts")
    async def test_show_streams__has_auto_refresh_script(self):
        """The /streams page includes JS polling script."""
        import fakeredis.aioredis

        from lol_ui.main import show_streams

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await show_streams(request)
        body = resp.body.decode()

        assert "/streams/fragment" in body
        assert "setInterval" in body
        assert "5000" in body
        await r.aclose()

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua scripts")
    async def test_show_streams__has_pause_button(self):
        """The /streams page includes a Pause button."""
        import fakeredis.aioredis

        from lol_ui.main import show_streams

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await show_streams(request)
        body = resp.body.decode()

        assert "streams-pause-btn" in body
        assert "Pause" in body
        assert "Auto-refresh every 5s" in body
        await r.aclose()


class TestErrorMessages:
    """Sprint 3.5: show_stats() returns distinct error messages per exception type."""

    @pytest.mark.asyncio
    async def test_not_found_error__specific_message(self):
        """NotFoundError shows player-not-found guidance."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.return_value = None  # no cached puuid

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.side_effect = NotFoundError("not found")

        request = MagicMock()
        request.query_params = {"riot_id": "Fake#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = MagicMock()

        resp = await show_stats(request)
        body = resp.body.decode()

        assert "Player not found" in body
        assert "Check the spelling" in body

    @pytest.mark.asyncio
    async def test_rate_limit_error__specific_message(self):
        """RateLimitError shows rate-limit guidance."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.return_value = None

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.side_effect = RateLimitError("429")

        request = MagicMock()
        request.query_params = {"riot_id": "Fake#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = MagicMock()

        resp = await show_stats(request)
        body = resp.body.decode()

        assert "Rate limited" in body
        assert "few seconds" in body

    @pytest.mark.asyncio
    async def test_auth_error__specific_message(self):
        """AuthError shows API key guidance."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.return_value = None

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.side_effect = AuthError("403")

        request = MagicMock()
        request.query_params = {"riot_id": "Fake#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = MagicMock()

        resp = await show_stats(request)
        body = resp.body.decode()

        assert "API key issue" in body
        assert "system-resume" in body

    @pytest.mark.asyncio
    async def test_server_error__specific_message(self):
        """ServerError shows server-unavailable guidance."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.return_value = None

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.side_effect = ServerError("500")

        request = MagicMock()
        request.query_params = {"riot_id": "Fake#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = MagicMock()

        resp = await show_stats(request)
        body = resp.body.decode()

        assert "temporarily unavailable" in body
        assert "Try again later" in body


class TestRegionValidation:
    """Fix 8: Invalid region falls back to na1."""

    @pytest.mark.asyncio
    async def test_invalid_region__falls_back_to_na1(self):
        """show_stats with unknown region defaults to na1."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()

        request = MagicMock()
        request.query_params = {"riot_id": "", "region": "INVALID_REGION"}
        request.app.state.r = mock_r

        resp = await show_stats(request)
        body = resp.body.decode()

        # na1 should be selected (fallback)
        na1_match = re.search(r'value="na1"[^>]*>', body)
        assert na1_match is not None
        assert "selected" in na1_match.group(0)


class TestNameCacheTTLInUI:
    """Fix 7: player:name cache in UI has 24h TTL."""

    @pytest.mark.asyncio
    async def test_name_cache_set_with_ttl(self):
        """show_stats sets player:name cache with ex=86400."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:test#na1": None,
            "system:halted": None,
        }.get(key)
        mock_r.hgetall.return_value = {"total_games": "10"}
        mock_r.zrevrange.return_value = []
        mock_r.hget.return_value = None

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.return_value = {"puuid": "test-puuid-ttl"}

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = MagicMock()
        request.app.state.lcu = {}

        await show_stats(request)

        # Verify set was called with cache TTL
        from lol_pipeline.resolve import _CACHE_TTL_S

        mock_r.set.assert_any_call(
            "player:name:test#na1", "test-puuid-ttl", ex=_CACHE_TTL_S
        )


class TestRegionPreservation:
    """Sprint 3.2: Region dropdown preserves selection across requests."""

    @pytest.mark.asyncio
    async def test_empty_form__preserves_region_from_query(self):
        """When no riot_id given, region from query string is preserved."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()

        request = MagicMock()
        request.query_params = {"riot_id": "", "region": "kr"}
        request.app.state.r = mock_r

        resp = await show_stats(request)
        body = resp.body.decode()

        kr_match = re.search(r'value="kr"[^>]*>', body)
        assert kr_match is not None
        assert "selected" in kr_match.group(0)

    @pytest.mark.asyncio
    async def test_error_response__preserves_region(self):
        """On NotFoundError, region selection is preserved in the form."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.return_value = None

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.side_effect = NotFoundError("not found")

        request = MagicMock()
        request.query_params = {"riot_id": "Fake#NA1", "region": "euw1"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = MagicMock()

        resp = await show_stats(request)
        body = resp.body.decode()

        euw1_match = re.search(r'value="euw1"[^>]*>', body)
        assert euw1_match is not None
        assert "selected" in euw1_match.group(0)


class TestPriorityBadge:
    """Sprint 3.4: Priority badge shown on /stats for players with active priority."""

    @pytest.mark.asyncio
    async def test_priority_badge__shown_when_priority_key_exists(self):
        """Priority badge appears next to player name when priority key is set."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:test#na1": "test-puuid-123",
            "player:priority:test-puuid-123": "high",
            "system:halted": None,
        }.get(key)
        mock_r.hgetall.return_value = {"total_games": "10", "wins": "5"}
        mock_r.zrevrange.return_value = []

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = MagicMock()
        request.app.state.cfg = MagicMock()
        request.app.state.lcu = {}

        resp = await show_stats(request)
        body = resp.body.decode()

        assert "badge--info" in body
        assert "Priority" in body

    @pytest.mark.asyncio
    async def test_priority_badge__hidden_when_no_priority_key(self):
        """No priority badge when priority key does not exist."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:test#na1": "test-puuid-123",
            "player:priority:test-puuid-123": None,
            "system:halted": None,
        }.get(key)
        mock_r.hgetall.return_value = {"total_games": "10", "wins": "5"}
        mock_r.zrevrange.return_value = []

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = MagicMock()
        request.app.state.cfg = MagicMock()
        request.app.state.lcu = {}

        resp = await show_stats(request)
        body = resp.body.decode()

        assert "Priority" not in body


class TestPlayersEmptyState:
    """Sprint 3.3: /players empty state uses _empty_state() component."""

    @pytest.mark.asyncio
    async def test_no_players__shows_empty_state(self):
        """When no players seeded, shows styled empty state with guidance."""
        import fakeredis.aioredis

        from lol_ui.main import show_players

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "0"}

        resp = await show_players(request)
        body = resp.body.decode()

        assert 'class="empty-state"' in body
        assert "No players seeded yet" in body
        assert "just seed GameName#Tag" in body
        await r.aclose()


class TestFavicon:
    """Sprint 5.1: Favicon appears in _page() output."""

    def test_page__contains_favicon_link(self):
        result = _page("Test", "")
        assert 'rel="icon"' in result
        assert "data:image/svg+xml" in result


class TestDlqBrowser:
    """Sprint 5.3: /dlq route displays DLQ entries."""

    @pytest.mark.asyncio
    async def test_show_dlq__empty_dlq_shows_empty_state(self):
        """When DLQ stream is empty, show 'pipeline is healthy' empty state."""
        import fakeredis.aioredis

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "Dead Letter Queue" in body
        assert 'class="empty-state"' in body
        assert "DLQ is empty" in body
        assert "pipeline is healthy" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_dlq__displays_entries(self):
        """When DLQ has entries, display them in a table."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_123", "region": "na1"},
            attempts=3,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-123",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "Dead Letter Queue" in body
        assert "http_429" in body
        assert "fetcher" in body
        assert "NA1_123" in body
        assert 'class="badge badge--error"' in body
        await r.aclose()


class TestDlqBrowserEdgeCases:
    """Additional DLQ route edge cases."""

    @pytest.mark.asyncio
    async def test_show_dlq__truncates_long_payload(self):
        """Payloads longer than 80 chars are truncated with '...'."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_" + "x" * 200, "region": "na1"},
            attempts=1,
            max_attempts=5,
            failure_code="parse_error",
            failure_reason="bad data",
            failed_by="parser",
            original_stream="stream:parse",
            original_message_id="orig-1",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "..." in body
        assert "parse_error" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_dlq__html_escapes_failure_code(self):
        """Failure code with HTML is escaped."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_1"},
            attempts=1,
            max_attempts=5,
            failure_code="<script>xss</script>",
            failure_reason="test",
            failed_by="test",
            original_stream="stream:match_id",
            original_message_id="orig-2",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "<script>xss</script>" not in body
        assert html.escape("<script>xss</script>") in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_dlq__shows_up_to_50_entries(self):
        """DLQ page caps at 50 entries."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        for i in range(55):
            dlq = DLQEnvelope(
                source_stream="stream:dlq",
                type="dlq",
                payload={"match_id": f"NA1_{i}"},
                attempts=1,
                max_attempts=5,
                failure_code="http_429",
                failure_reason="rate limited",
                failed_by="fetcher",
                original_stream="stream:match_id",
                original_message_id=f"orig-{i}",
            )
            await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await show_dlq(request)
        body = resp.body.decode()

        # Count the number of <tr> rows in the table (minus header)
        row_count = body.count("<tr><td>")
        assert row_count == 50
        await r.aclose()


class TestStreamsFragmentHtmlEdgeCases:
    """Additional _streams_fragment_html edge cases."""

    @pytest.mark.asyncio
    async def test_streams_fragment__shows_stream_depths(self):
        """Fragment shows actual stream depths when streams have entries."""
        import fakeredis.aioredis

        from lol_ui.main import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Add some entries to a stream
        for i in range(5):
            await r.xadd("stream:puuid", {"dummy": str(i)})

        result = await _streams_fragment_html(r)
        assert "<td>5</td>" in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment__delayed_messages_count(self):
        """Fragment shows delayed:messages ZSET count."""
        import fakeredis.aioredis

        from lol_ui.main import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("delayed:messages", {"msg1": 1000.0, "msg2": 2000.0})

        result = await _streams_fragment_html(r)
        assert "delayed:messages" in result
        assert "<td>2</td>" in result
        await r.aclose()


class TestStatsMatchesEdgeCases:
    """Additional /stats/matches route edge cases."""

    @pytest.mark.asyncio
    async def test_stats_matches__empty_puuid_returns_error(self):
        """Missing puuid returns error message."""
        from unittest.mock import MagicMock

        from lol_ui.main import stats_matches

        request = MagicMock()
        request.query_params = {"puuid": "", "region": "na1", "riot_id": "T#1", "page": "0"}
        request.app.state.r = MagicMock()

        resp = await stats_matches(request)
        assert resp.status_code == 200
        assert "Missing puuid" in resp.body.decode()

    @pytest.mark.asyncio
    async def test_stats_matches__invalid_puuid_returns_400(self):
        """Invalid puuid format returns 400."""
        from unittest.mock import MagicMock

        from lol_ui.main import stats_matches

        request = MagicMock()
        request.query_params = {
            "puuid": "../../etc/passwd",
            "region": "na1",
            "riot_id": "T#1",
            "page": "0",
        }
        request.app.state.r = MagicMock()

        resp = await stats_matches(request)
        assert resp.status_code == 400
        assert "Invalid PUUID" in resp.body.decode()

    @pytest.mark.asyncio
    async def test_stats_matches__invalid_page_defaults_to_zero(self):
        """Non-numeric page defaults to 0."""
        import fakeredis.aioredis

        from lol_ui.main import stats_matches

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {
            "puuid": "validpuuid123",
            "region": "na1",
            "riot_id": "T#1",
            "page": "abc",
        }
        request.app.state.r = r

        resp = await stats_matches(request)
        assert resp.status_code == 200
        assert "No match history" in resp.body.decode()
        await r.aclose()

    @pytest.mark.asyncio
    async def test_stats_matches__no_matches_returns_no_history(self):
        """PUUID with no matches returns 'No match history'."""
        import fakeredis.aioredis

        from lol_ui.main import stats_matches

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {
            "puuid": "emptyplayer",
            "region": "na1",
            "riot_id": "T#1",
            "page": "0",
        }
        request.app.state.r = r

        resp = await stats_matches(request)
        assert resp.status_code == 200
        assert "No match history" in resp.body.decode()
        await r.aclose()


class TestPlayerSearch:
    """Sprint 5.5: /players has client-side search filter input."""

    @pytest.mark.asyncio
    async def test_players__has_search_input(self):
        """The /players page includes a search input when players exist."""
        import fakeredis.aioredis

        from lol_ui.main import show_players

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.hset(
            "player:test-puuid",
            mapping={
                "game_name": "TestPlayer",
                "tag_line": "NA1",
                "region": "na1",
                "seeded_at": "2026-03-19T00:00:00",
            },
        )

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "0"}

        resp = await show_players(request)
        body = resp.body.decode()

        assert 'id="player-search"' in body
        assert 'placeholder="Filter players..."' in body
        await r.aclose()


class TestStatsGrid:
    """Sprint 5.6: Stats table uses stats-grid for wide layout."""

    def test_stats_table__wraps_champs_and_roles_in_stats_grid(self):
        """Champions and roles tables are wrapped in a stats-grid div."""
        result = _stats_table({"total_games": "10"}, [("Zed", 5.0)], [("MID", 3.0)])
        assert 'class="stats-grid"' in result


class TestDlqNav:
    """Sprint 5.3: DLQ nav link is always present."""

    def test_page__nav_contains_dlq_link(self):
        result = _page("Test", "")
        assert "/dlq" in result
        assert "DLQ" in result


class TestUiEntryPoint:
    """Tests for __main__ module."""

    def test_main__calls_uvicorn_run(self):
        """__main__ calls uvicorn.run with correct host and port."""
        import importlib
        import sys

        # Remove cached module so reload actually re-executes it
        sys.modules.pop("lol_ui.__main__", None)
        with patch("uvicorn.run") as mock_uvicorn:
            importlib.import_module("lol_ui.__main__")
        mock_uvicorn.assert_called_once()
        call_args = mock_uvicorn.call_args
        assert call_args[1]["host"] == "0.0.0.0"
        assert call_args[1]["port"] == 8080
