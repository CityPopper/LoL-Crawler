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
    _AUTOSEED_COOLDOWN_S,
    _BADGE_VARIANTS,
    _CSS,
    _NAME_CACHE_INDEX,
    _NAME_CACHE_MAX,
    _NAV_ITEMS,
    _PUUID_RE,
    _REGIONS,
    _REGIONS_SET,
    _STATS_ORDER,
    _badge,
    _badge_html,
    _champion_icon_html,
    _depth_badge,
    _empty_state,
    _format_stat_value,
    _match_history_html,
    _match_history_section,
    _merged_log_lines,
    _page,
    _parse_log_line,
    _render_log_lines,
    _render_player_rows,
    _stats_form,
    _stats_table,
    _tail_file,
)


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

    def test_loading_indicator_uses_spinner(self):
        """P11-DD-13: loading indicator uses spinner element, not plain text."""
        html_out = _match_history_section("puuid-abc", "na1", "Player#NA1")
        assert "loading-state" in html_out
        assert "spinner" in html_out
        assert "Loading match history" in html_out
        # Must not inject user-supplied data via innerHTML (static string only)
        assert "innerHTML = '<p>Loading" not in html_out


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
        result = _empty_state("Title", "Run <code>just seed</code>")
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
        _ts, level, _logger, msg, _extra = _parse_log_line("plain text line")
        assert level == "INFO"
        assert msg == "plain text line"

    def test_missing_fields_use_defaults(self):
        line = json.dumps({"message": "hello"})
        ts, level, _logger, msg, _extra = _parse_log_line(line)
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
            mapping={
                "win": "1",
                "champion_name": "Zed",
                "kills": "5",
                "deaths": "2",
                "assists": "3",
            },
        )
        await r.hset(
            "participant:NA1_2:testpuuid",
            mapping={
                "win": "0",
                "champion_name": "Ahri",
                "kills": "1",
                "deaths": "4",
                "assists": "2",
            },
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
            api_rate_limit_per_second = 20

        request = re.Match  # unused, just need a MagicMock
        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = r
        request.app.state.cfg = FakeCfg()
        request.app.state.riot = FakeRiot()

        await show_stats(request)

        # Check envelope in stream has priority=high
        entries = await r.xrange("stream:puuid")
        assert len(entries) == 1
        assert entries[0][1]["priority"] == "high"

        # Check priority key was set
        assert await r.get("player:priority:test-puuid-ui") == "1"

        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_streams__displays_priority_status(self):
        """The /streams page displays priority status via SCAN-based detection."""
        import fakeredis.aioredis

        from lol_ui.main import show_streams

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("player:priority:puuid-1", "1", ex=86400)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await show_streams(request)
        body = resp.body.decode()
        assert "Priority players in-flight" in body
        assert "<strong>Yes</strong>" in body

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

            await show_stats(request)

        assert call_order == ["publish", "hset_seeded_at"]


class TestStatsOrder:
    """Sprint 2.1: _STATS_ORDER controls display order in stats table."""

    def test_stats_order__contains_all_core_stats(self):
        """All expected stat keys are present in the order list."""
        expected = [
            "total_games",
            "total_wins",
            "win_rate",
            "total_kills",
            "total_deaths",
            "total_assists",
            "kda",
            "avg_kills",
            "avg_deaths",
            "avg_assists",
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

    @pytest.mark.asyncio
    async def test_players__shows_page_indicator(self):
        """Single page of players shows 'page 1 of 1' indicator."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.main import show_players

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.hset(
            "player:test-puuid",
            mapping={
                "game_name": "TestPlayer",
                "tag_line": "NA1",
                "region": "na1",
                "seeded_at": "2026-03-19T12:00:00+00:00",
            },
        )
        await r.zadd("players:all", {"test-puuid": 1710835200.0})
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "0"}

        resp = await show_players(request)
        body = resp.body.decode()

        assert "page 1 of 1" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_players__shows_date_only_timestamp(self):
        """P10-RD-9: seeded_at is rendered as date-only, not full ISO string."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.main import show_players

        iso_ts = "2026-03-19T12:34:56+00:00"
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.hset(
            "player:test-puuid",
            mapping={
                "game_name": "TestPlayer",
                "tag_line": "NA1",
                "region": "na1",
                "seeded_at": iso_ts,
            },
        )
        await r.zadd("players:all", {"test-puuid": 1710835200.0})
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "0"}

        resp = await show_players(request)
        body = resp.body.decode()

        assert "2026-03-19" in body
        assert "T12:34:56" not in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_players__multipage_shows_correct_indicator(self):
        """26 players across 2 pages: page=1 shows 'page 2 of 2'."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.main import _PLAYERS_PAGE_SIZE, show_players

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        for i in range(_PLAYERS_PAGE_SIZE + 1):
            await r.hset(
                f"player:puuid-{i}",
                mapping={
                    "game_name": f"Player{i}",
                    "tag_line": "NA1",
                    "region": "na1",
                    "seeded_at": "2026-03-19T00:00:00",
                },
            )
            await r.zadd("players:all", {f"puuid-{i}": 1710835200.0 + i})
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "1"}

        resp = await show_players(request)
        body = resp.body.decode()

        assert "page 2 of 2" in body
        await r.aclose()


class TestStatsHeading:
    """Sprint 2.1: /stats heading shows Riot ID name only (no PUUID)."""

    @pytest.mark.asyncio
    async def test_show_stats__heading_shows_riot_id(self):
        """Heading reads 'Stats for GameName#TagLine' when stats exist."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.return_value = None  # no cache, no halt, no priority
        mock_r.hgetall.return_value = {"total_games": "10", "win_rate": "0.6"}
        mock_r.zrevrange.return_value = []

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.return_value = {"puuid": "test-puuid-heading"}

        request = MagicMock()
        request.query_params = {"riot_id": "Faker#KR1", "region": "kr"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = MagicMock()

        resp = await show_stats(request)
        body = resp.body.decode()

        assert "Stats for Faker#KR1" in body

    @pytest.mark.asyncio
    async def test_show_stats__heading_does_not_contain_puuid(self):
        """The PUUID is not rendered in the heading paragraph."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        test_puuid = "secret-puuid-abc-xyz-999"
        mock_r = AsyncMock()
        mock_r.get.return_value = None
        mock_r.hgetall.return_value = {"total_games": "5"}
        mock_r.zrevrange.return_value = []

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.return_value = {"puuid": test_puuid}

        request = MagicMock()
        request.query_params = {"riot_id": "TestPlayer#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = MagicMock()

        resp = await show_stats(request)
        body = resp.body.decode()

        # PUUID must not appear in the heading message element
        heading_start = body.find('class="success"')
        heading_end = body.find("</p>", heading_start) if heading_start != -1 else -1
        heading = body[heading_start:heading_end] if heading_start != -1 else ""
        assert test_puuid not in heading


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
    async def test_streams_fragment__includes_priority_status(self):
        """Fragment includes SCAN-based priority status display."""
        import fakeredis.aioredis

        from lol_ui.main import streams_fragment

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("player:priority:puuid-1", "1", ex=86400)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await streams_fragment(request)
        body = resp.body.decode()

        assert "<strong>Yes</strong>" in body
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

        await show_stats(request)

        # Verify set was called with cache TTL
        from lol_pipeline.resolve import CACHE_TTL_S

        mock_r.set.assert_any_call("player:name:test#na1", "test-puuid-ttl", ex=CACHE_TTL_S)


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
        request.query_params = {}

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
        request.query_params = {}

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
        request.query_params = {}

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
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "<script>xss</script>" not in body
        assert html.escape("<script>xss</script>") in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_dlq__shows_up_to_max_per_page_entries(self):
        """DLQ page caps at per_page entries (default 25)."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import _DLQ_DEFAULT_PER_PAGE, show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        for i in range(_DLQ_DEFAULT_PER_PAGE + 5):
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
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        # Count the number of <tr> rows in the table (minus header)
        row_count = body.count("<tr><td>")
        assert row_count == _DLQ_DEFAULT_PER_PAGE
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
        assert '<td class="text-right">5</td>' in result
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
        assert '<td class="text-right">2</td>' in result
        await r.aclose()


class TestStreamsFragmentPipeline:
    """P1: _streams_fragment_html uses a single pipeline for all Redis calls."""

    @pytest.mark.asyncio
    async def test_streams_fragment__uses_pipeline_not_individual_calls(self):
        """All XLEN/ZCARD/GET calls should go through a pipeline, not individually."""
        import fakeredis.aioredis

        from lol_ui.main import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Track direct calls on r (not on pipeline)
        direct_xlen_count = 0
        direct_get_count = 0
        original_xlen = r.xlen
        original_get = r.get

        async def tracking_xlen(*args, **kwargs):
            nonlocal direct_xlen_count
            direct_xlen_count += 1
            return await original_xlen(*args, **kwargs)

        async def tracking_get(*args, **kwargs):
            nonlocal direct_get_count
            direct_get_count += 1
            return await original_get(*args, **kwargs)

        r.xlen = tracking_xlen
        r.get = tracking_get

        result = await _streams_fragment_html(r)

        # After pipelining, there should be 0 direct xlen and 0 direct get calls
        assert direct_xlen_count == 0, (
            f"Expected 0 direct xlen calls (use pipeline), got {direct_xlen_count}"
        )
        assert direct_get_count == 0, (
            f"Expected 0 direct get calls (use pipeline), got {direct_get_count}"
        )
        # Result should still be valid HTML
        assert "stream:puuid" in result
        assert "delayed:messages" in result
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
        await r.zadd("players:all", {"test-puuid": 1710835200.0})

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "0"}

        resp = await show_players(request)
        body = resp.body.decode()

        assert 'id="player-search"' in body
        assert 'placeholder="Filter players..."' in body
        await r.aclose()


class TestPlayersAllZset:
    """Performance: /players uses players:all ZSET instead of SCAN."""

    @pytest.mark.asyncio
    async def test_players__uses_zrevrange_not_scan(self):
        """show_players reads from players:all ZSET, not scan_iter."""
        import fakeredis.aioredis

        from lol_ui.main import show_players

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Add player to players:all ZSET and set up hash
        await r.zadd("players:all", {"puuid-zset": 1710835200.0})
        await r.hset(
            "player:puuid-zset",
            mapping={
                "game_name": "ZsetPlayer",
                "tag_line": "001",
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

        # Player from ZSET should appear
        assert "ZsetPlayer#001" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_players__player_in_hash_but_not_zset__not_shown(self):
        """Player hashes without a players:all entry are NOT shown."""
        import fakeredis.aioredis

        from lol_ui.main import show_players

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Only set up hash, NOT players:all
        await r.hset(
            "player:puuid-orphan",
            mapping={
                "game_name": "OrphanPlayer",
                "tag_line": "001",
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

        # Should show empty state since players:all is empty
        assert "No players seeded yet" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_players__ordered_most_recent_first(self):
        """Players are shown most recently seeded first (highest score in ZSET)."""
        import fakeredis.aioredis

        from lol_ui.main import show_players

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("players:all", {"puuid-old": 1000.0, "puuid-new": 2000.0})
        await r.hset(
            "player:puuid-old",
            mapping={
                "game_name": "OldPlayer",
                "tag_line": "001",
                "region": "na1",
                "seeded_at": "2024-01-01T00:00:00",
            },
        )
        await r.hset(
            "player:puuid-new",
            mapping={
                "game_name": "NewPlayer",
                "tag_line": "002",
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

        # NewPlayer should appear before OldPlayer
        new_pos = body.index("NewPlayer")
        old_pos = body.index("OldPlayer")
        assert new_pos < old_pos
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
        assert call_args[1]["host"] == "0.0.0.0"  # noqa: S104
        assert call_args[1]["port"] == 8080


class TestRegionsComplete:
    """R11: _REGIONS must include all 16 platforms from PLATFORM_TO_REGION."""

    def test_regions_contains_all_platform_keys(self):
        from lol_pipeline.riot_api import PLATFORM_TO_REGION

        for platform in PLATFORM_TO_REGION:
            assert platform in _REGIONS, f"Missing platform {platform!r} in _REGIONS"

    def test_regions_has_16_entries(self):
        assert len(_REGIONS) == 16

    def test_regions_includes_sea_platforms(self):
        for platform in ("ph2", "sg2", "th2", "tw2", "vn2"):
            assert platform in _REGIONS

    def test_regions_includes_ru_and_tr1(self):
        assert "ru" in _REGIONS
        assert "tr1" in _REGIONS

    def test_regions_includes_la1_la2(self):
        assert "la1" in _REGIONS
        assert "la2" in _REGIONS


class TestRegionDropdownSelectedSpace:
    """R12: selected attribute must have a leading space in the option tag."""

    def test_selected_has_leading_space(self):
        result = _stats_form(selected_region="na1")
        # Must be 'value="na1" selected' (with space), never 'value="na1"selected'
        assert 'value="na1" selected' in result

    def test_non_selected_no_selected_attr(self):
        result = _stats_form(selected_region="kr")
        na1_match = re.search(r'<option value="na1"[^>]*>', result)
        assert na1_match is not None
        assert "selected" not in na1_match.group(0)

    def test_all_regions_render_as_options(self):
        result = _stats_form()
        for region in _REGIONS:
            assert f'value="{region}"' in result


class TestRedisExceptionHandler:
    """R13: Redis errors return a 503 HTML page, not a stack trace."""

    @pytest.mark.asyncio
    async def test_redis_error_handler__returns_503_with_message(self):
        """Direct test: the exception handler returns 503 with helpful message."""
        from unittest.mock import MagicMock

        import redis.exceptions

        from lol_ui.main import redis_error_handler

        request = MagicMock()
        exc = redis.exceptions.RedisError("connection refused")

        resp = await redis_error_handler(request, exc)

        assert resp.status_code == 503
        body = resp.body.decode()
        assert "Cannot connect to Redis" in body or "Redis" in body
        assert "<code>just run</code>" in body

    @pytest.mark.asyncio
    async def test_connection_error_handler__returns_503(self):
        """ConnectionError also gets a 503 HTML page."""
        from unittest.mock import MagicMock

        from lol_ui.main import connection_error_handler

        request = MagicMock()
        exc = ConnectionError("refused")

        resp = await connection_error_handler(request, exc)

        assert resp.status_code == 503
        body = resp.body.decode()
        assert "Redis" in body


class TestDlqCorruptEntries:
    """B4: /dlq page must not crash on corrupt DLQ entries."""

    @pytest.mark.asyncio
    async def test_show_dlq__corrupt_entry_skipped__returns_200(self):
        """A corrupt entry (missing required fields) is skipped; page still returns 200."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        # Add a valid DLQ entry
        valid = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_good", "region": "na1"},
            attempts=1,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-good",
        )
        await r.xadd("stream:dlq", valid.to_redis_fields())

        # Add a corrupt entry (missing most required fields)
        await r.xadd("stream:dlq", {"garbage": "data"})

        # Add another valid entry after the corrupt one
        valid2 = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_good2", "region": "na1"},
            attempts=2,
            max_attempts=5,
            failure_code="http_5xx",
            failure_reason="server error",
            failed_by="parser",
            original_stream="stream:parse",
            original_message_id="orig-good2",
        )
        await r.xadd("stream:dlq", valid2.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        # Should return 200, not 500
        assert resp.status_code == 200
        # Both valid entries should appear
        assert "NA1_good" in body
        assert "NA1_good2" in body
        # Should still render as a table
        assert "<table>" in body
        # Exactly 2 data rows (corrupt entry skipped)
        assert body.count("<tr><td>") == 2
        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_dlq__all_corrupt__shows_empty_table(self):
        """When all entries are corrupt, page returns 200 with table but no data rows."""
        import fakeredis.aioredis

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        # Add only corrupt entries
        await r.xadd("stream:dlq", {"bad": "entry1"})
        await r.xadd("stream:dlq", {"broken": "entry2"})

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert resp.status_code == 200
        assert "Dead Letter Queue" in body
        # No data rows
        assert body.count("<tr><td>") == 0
        await r.aclose()


class TestRateLimitBeforeRiotCall:
    """I2-H7: wait_for_token must be called before every Riot API call in show_stats."""

    @pytest.mark.asyncio
    async def test_show_stats__calls_wait_for_token_before_riot_api(self):
        """wait_for_token is called before riot.get_account_by_riot_id."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        call_order: list[str] = []

        mock_r = AsyncMock()
        mock_r.get.return_value = None  # no cached puuid
        mock_r.hgetall.return_value = {"total_games": "10"}
        mock_r.zrevrange.return_value = []

        mock_riot = AsyncMock()

        async def tracking_riot_call(*args, **kwargs):
            call_order.append("riot_api")
            return {"puuid": "test-puuid-ratelimit"}

        mock_riot.get_account_by_riot_id.side_effect = tracking_riot_call

        mock_cfg = MagicMock()
        mock_cfg.api_rate_limit_per_second = 20

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = mock_cfg

        with patch("lol_ui.main.wait_for_token", new_callable=AsyncMock) as mock_wft:

            async def tracking_wait(*args, **kwargs):
                call_order.append("wait_for_token")

            mock_wft.side_effect = tracking_wait

            await show_stats(request)

        assert "wait_for_token" in call_order, "wait_for_token was never called"
        assert "riot_api" in call_order, "riot API was never called"
        wft_idx = call_order.index("wait_for_token")
        riot_idx = call_order.index("riot_api")
        assert wft_idx < riot_idx, (
            f"wait_for_token (idx={wft_idx}) must be called before riot API (idx={riot_idx})"
        )

    @pytest.mark.asyncio
    async def test_show_stats__wait_for_token_receives_rate_limit_config(self):
        """wait_for_token is called with the configured api_rate_limit_per_second."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.return_value = None
        mock_r.hgetall.return_value = {"total_games": "10"}
        mock_r.zrevrange.return_value = []

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.return_value = {"puuid": "test-puuid-cfg"}

        mock_cfg = MagicMock()
        mock_cfg.api_rate_limit_per_second = 15

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = mock_cfg

        with patch("lol_ui.main.wait_for_token", new_callable=AsyncMock) as mock_wft:
            await show_stats(request)

        mock_wft.assert_called_once_with(mock_r, limit_per_second=15)

    @pytest.mark.asyncio
    async def test_show_stats__cached_puuid_skips_wait_for_token(self):
        """When puuid is in cache, no Riot API call is made, so no rate limiting needed."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:test#na1": "cached-puuid-123",
            "player:priority:cached-puuid-123": None,
            "system:halted": None,
        }.get(key)
        mock_r.hgetall.return_value = {"total_games": "10"}
        mock_r.zrevrange.return_value = []

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = MagicMock()
        request.app.state.cfg = MagicMock()

        with patch("lol_ui.main.wait_for_token", new_callable=AsyncMock) as mock_wft:
            await show_stats(request)

        mock_wft.assert_not_called()


# ---------------------------------------------------------------------------
# Security: Name cache index (bounded cache size)
# ---------------------------------------------------------------------------


class TestNameCacheIndex:
    """SEC: name cache index tracks entries and caps at _NAME_CACHE_MAX."""

    @pytest.mark.asyncio
    async def test_resolve_adds_entry_to_name_cache_index(self):
        """After resolving a PUUID, the cache key is tracked in name_cache:index."""
        from unittest.mock import AsyncMock, MagicMock

        import fakeredis.aioredis

        from lol_ui.main import show_stats

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        class FakeRiot:
            async def get_account_by_riot_id(self, gn, tl, region):
                return {"puuid": "idx-puuid-1"}

        class FakeCfg:
            max_attempts = 5
            api_rate_limit_per_second = 20

        request = MagicMock()
        request.query_params = {"riot_id": "IndexTest#NA1", "region": "na1"}
        request.app.state.r = r
        request.app.state.cfg = FakeCfg()
        request.app.state.riot = FakeRiot()

        # Pre-set stats so auto-seed path is not entered
        await r.hset("player:stats:idx-puuid-1", mapping={"total_games": "1"})

        with patch("lol_ui.main.wait_for_token", new_callable=AsyncMock):
            await show_stats(request)

        # The name_cache:index should have one entry
        index_size = await r.zcard(_NAME_CACHE_INDEX)
        assert index_size == 1

        members = await r.zrange(_NAME_CACHE_INDEX, 0, -1)
        assert len(members) == 1
        assert "player:name:" in members[0]

        await r.aclose()

    @pytest.mark.asyncio
    async def test_name_cache_index_evicts_oldest_when_full(self):
        """When cache index reaches _NAME_CACHE_MAX, the oldest entry is evicted."""
        from unittest.mock import AsyncMock

        import fakeredis.aioredis

        from lol_ui.main import _resolve_and_cache_puuid

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        # Pre-fill index to _NAME_CACHE_MAX entries
        for i in range(_NAME_CACHE_MAX):
            await r.zadd(_NAME_CACHE_INDEX, {f"player:name:user{i}#tag": float(i)})

        assert await r.zcard(_NAME_CACHE_INDEX) == _NAME_CACHE_MAX

        class FakeRiot:
            async def get_account_by_riot_id(self, gn, tl, region):
                return {"puuid": "new-puuid"}

        class FakeCfg:
            api_rate_limit_per_second = 20

        with patch("lol_ui.main.wait_for_token", new_callable=AsyncMock):
            result = await _resolve_and_cache_puuid(
                r, FakeRiot(), "NewRiotId#NA1", "NewRiotId", "NA1", "na1", FakeCfg()
            )

        assert result == "new-puuid"
        # Size should still be _NAME_CACHE_MAX (oldest evicted, new one added)
        assert await r.zcard(_NAME_CACHE_INDEX) == _NAME_CACHE_MAX

        # The oldest entry (score=0.0, "player:name:user0#tag") should be gone
        members = await r.zrange(_NAME_CACHE_INDEX, 0, 0)
        assert members[0] != "player:name:user0#tag"

        await r.aclose()

    def test_name_cache_max_is_10000(self):
        """_NAME_CACHE_MAX constant is 10,000."""
        assert _NAME_CACHE_MAX == 10_000

    def test_name_cache_index_key(self):
        """_NAME_CACHE_INDEX key is 'name_cache:index'."""
        assert _NAME_CACHE_INDEX == "name_cache:index"


# ---------------------------------------------------------------------------
# Security: Region validation returns 400
# ---------------------------------------------------------------------------


class TestRegionValidation400:
    """SEC: Invalid region returns 400 Bad Request, not silent fallback."""

    @pytest.mark.asyncio
    async def test_invalid_region__returns_400(self):
        """show_stats with unknown region returns 400 status code."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "BOGUS"}
        request.app.state.r = mock_r

        resp = await show_stats(request)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_region__error_message_in_body(self):
        """400 response contains the invalid region name in the error."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()

        request = MagicMock()
        request.query_params = {"riot_id": "", "region": "xyzregion"}
        request.app.state.r = mock_r

        resp = await show_stats(request)
        body = resp.body.decode()

        assert resp.status_code == 400
        assert "Invalid region" in body
        assert "xyzregion" in body

    @pytest.mark.asyncio
    async def test_invalid_region__html_escaped(self):
        """XSS in region parameter is HTML-escaped in the 400 response."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()

        request = MagicMock()
        request.query_params = {"riot_id": "", "region": "<script>alert(1)</script>"}
        request.app.state.r = mock_r

        resp = await show_stats(request)
        body = resp.body.decode()

        assert resp.status_code == 400
        assert "<script>" not in body
        assert html.escape("<script>alert(1)</script>") in body

    @pytest.mark.asyncio
    async def test_valid_region__no_400(self):
        """Valid regions do not trigger 400."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()

        for region in ("na1", "kr", "euw1"):
            request = MagicMock()
            request.query_params = {"riot_id": "", "region": region}
            request.app.state.r = mock_r

            resp = await show_stats(request)
            assert resp.status_code == 200, f"Region {region} should be valid"

    def test_regions_set_matches_regions_list(self):
        """_REGIONS_SET is a frozenset matching _REGIONS list."""
        assert _REGIONS_SET == frozenset(_REGIONS)
        assert len(_REGIONS_SET) == len(_REGIONS)


# ---------------------------------------------------------------------------
# Security: Auto-seed cooldown
# ---------------------------------------------------------------------------


class TestAutoSeedCooldown:
    """SEC: Auto-seed has per-player cooldown to prevent abuse."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua scripts")
    async def test_auto_seed__sets_cooldown_key(self):
        """After auto-seeding, a cooldown key is set with 5-minute TTL."""
        from unittest.mock import AsyncMock, MagicMock

        import fakeredis.aioredis

        from lol_ui.main import show_stats

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        class FakeRiot:
            async def get_account_by_riot_id(self, gn, tl, region):
                return {"puuid": "cooldown-puuid-1"}

        class FakeCfg:
            max_attempts = 5
            api_rate_limit_per_second = 20

        request = MagicMock()
        request.query_params = {"riot_id": "CooldownTest#NA1", "region": "na1"}
        request.app.state.r = r
        request.app.state.cfg = FakeCfg()
        request.app.state.riot = FakeRiot()

        with patch("lol_ui.main.wait_for_token", new_callable=AsyncMock):
            await show_stats(request)

        # Cooldown key should be set
        cooldown = await r.get("autoseed:cooldown:cooldown-puuid-1")
        assert cooldown == "1"

        # TTL should be around 300 seconds
        ttl = await r.ttl("autoseed:cooldown:cooldown-puuid-1")
        assert 0 < ttl <= _AUTOSEED_COOLDOWN_S

        await r.aclose()

    @pytest.mark.asyncio
    async def test_auto_seed__blocked_by_cooldown(self):
        """When cooldown key exists, auto-seed is skipped."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:blocked#na1": "blocked-puuid-123",
            "system:halted": None,
            "player:priority:blocked-puuid-123": None,
            "autoseed:cooldown:blocked-puuid-123": "1",
        }.get(key)
        mock_r.hgetall.return_value = {}  # no stats, triggers auto-seed path
        mock_r.hget.return_value = None  # no seeded_at

        request = MagicMock()
        request.query_params = {"riot_id": "Blocked#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = MagicMock()
        request.app.state.cfg = MagicMock()

        resp = await show_stats(request)
        body = resp.body.decode()

        # Should get the "seeded recently" message, not the "Auto-seeded" message
        assert "seeded recently" in body
        assert "Auto-seeded" not in body

    @pytest.mark.asyncio
    async def test_auto_seed__no_cooldown__proceeds(self):
        """When no cooldown key exists, auto-seed proceeds normally."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import show_stats

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:proceed#na1": "proceed-puuid-456",
            "system:halted": None,
            "player:priority:proceed-puuid-456": None,
            "autoseed:cooldown:proceed-puuid-456": None,
        }.get(key)
        mock_r.hgetall.return_value = {}  # no stats
        mock_r.hget.return_value = None  # no seeded_at
        mock_r.set.return_value = True

        request = MagicMock()
        request.query_params = {"riot_id": "Proceed#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = MagicMock()
        request.app.state.cfg = MagicMock()

        with patch("lol_ui.main.publish", new_callable=AsyncMock):
            with patch("lol_ui.main.set_priority", new_callable=AsyncMock):
                resp = await show_stats(request)

        body = resp.body.decode()
        assert "Auto-seeded" in body

    def test_autoseed_cooldown_constant(self):
        """_AUTOSEED_COOLDOWN_S is 300 seconds (5 minutes)."""
        assert _AUTOSEED_COOLDOWN_S == 300


# ---------------------------------------------------------------------------
# Streams fragment: halt banner, priority count, normal case
# ---------------------------------------------------------------------------


class TestStreamsFragmentHtmlHaltBanner:
    """_streams_fragment_html renders halt banner when system:halted is set."""

    @pytest.mark.asyncio
    async def test_streams_fragment_html__halted__shows_halt_banner(self):
        """When system:halted is set, HTML contains HALTED banner with error class."""
        import fakeredis.aioredis

        from lol_ui.main import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:halted", "auth_403")

        result = await _streams_fragment_html(r)

        assert "HALTED" in result
        assert "banner--error" in result
        assert "System running" not in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment_html__priority_keys_exist__displays_yes(self):
        """When player:priority:* keys exist, displays Yes via SCAN-based detection."""
        import fakeredis.aioredis

        from lol_ui.main import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("player:priority:puuid-1", "1", ex=86400)

        result = await _streams_fragment_html(r)

        assert "Priority players in-flight" in result
        assert "<strong>Yes</strong>" in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment_html__normal__no_halt_shows_running(self):
        """When neither halted nor priority set, shows running banner and priority No."""
        import fakeredis.aioredis

        from lol_ui.main import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        result = await _streams_fragment_html(r)

        assert "System running" in result
        assert "banner--success" in result
        assert "HALTED" not in result
        assert "<strong>No</strong>" in result
        # All stream keys should be present
        assert "stream:puuid" in result
        assert "stream:match_id" in result
        assert "stream:parse" in result
        assert "stream:analyze" in result
        assert "stream:dlq" in result
        await r.aclose()


# ---------------------------------------------------------------------------
# DLQ: original_stream, failure_code, dlq_attempts verification
# ---------------------------------------------------------------------------


class TestDlqEntryFields:
    """DLQ entry renders original_stream, failure_code, and dlq_attempts."""

    @pytest.mark.asyncio
    async def test_show_dlq__entry_shows_original_stream(self):
        """DLQ entry displays original_stream (not source_stream) in table."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_999"},
            attempts=2,
            max_attempts=5,
            failure_code="http_5xx",
            failure_reason="internal server error",
            failed_by="parser",
            original_stream="stream:parse",
            original_message_id="orig-999",
            dlq_attempts=3,
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        # original_stream, not source_stream, should appear
        assert "stream:parse" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_dlq__entry_shows_failure_code_as_badge(self):
        """DLQ entry displays failure_code in an error badge."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_fc"},
            attempts=1,
            max_attempts=5,
            failure_code="parse_error",
            failure_reason="invalid JSON",
            failed_by="parser",
            original_stream="stream:match_id",
            original_message_id="orig-fc",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "parse_error" in body
        assert 'class="badge badge--error"' in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_dlq__entry_shows_dlq_attempts(self):
        """DLQ entry renders the dlq_attempts count in the table."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_att"},
            attempts=4,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-att",
            dlq_attempts=5,
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        # dlq_attempts=5 should appear in the Attempts column
        assert ">5<" in body or "<td>5</td>" in body
        await r.aclose()


# ---------------------------------------------------------------------------
# /stats/matches: valid PUUID with matches, pagination
# ---------------------------------------------------------------------------


class TestStatsMatchesWithData:
    """Tests for /stats/matches with actual match data and pagination."""

    @pytest.mark.asyncio
    async def test_stats_matches__valid_puuid_with_matches__returns_html(self):
        """Valid PUUID with match data returns match history HTML."""
        import fakeredis.aioredis

        from lol_ui.main import stats_matches

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd(
            "player:matches:validpuuid",
            {"NA1_100": 1700000000000.0, "NA1_200": 1700001000000.0},
        )
        await r.hset(
            "match:NA1_100",
            mapping={"game_start": "1700000000000", "game_mode": "CLASSIC"},
        )
        await r.hset(
            "match:NA1_200",
            mapping={"game_start": "1700001000000", "game_mode": "ARAM"},
        )
        await r.hset(
            "participant:NA1_100:validpuuid",
            mapping={
                "win": "1",
                "champion_name": "Jinx",
                "kills": "12",
                "deaths": "3",
                "assists": "8",
            },
        )
        await r.hset(
            "participant:NA1_200:validpuuid",
            mapping={
                "win": "0",
                "champion_name": "Zed",
                "kills": "5",
                "deaths": "7",
                "assists": "2",
            },
        )

        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {
            "puuid": "validpuuid",
            "region": "na1",
            "riot_id": "Player#NA1",
            "page": "0",
        }
        request.app.state.r = r

        resp = await stats_matches(request)
        body = resp.body.decode()

        assert resp.status_code == 200
        assert "Jinx" in body
        assert "Zed" in body
        assert "12/3/8" in body
        assert "5/7/2" in body
        assert "Win" in body
        assert "Loss" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_stats_matches__pagination_page1_skips_first_page(self):
        """Page 1 skips the first _MATCH_PAGE_SIZE entries."""
        import fakeredis.aioredis

        from lol_ui.main import _MATCH_PAGE_SIZE, stats_matches

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Create _MATCH_PAGE_SIZE + 5 matches to span 2 pages
        total = _MATCH_PAGE_SIZE + 5
        for i in range(total):
            mid = f"NA1_{i:04d}"
            ts = 1700000000000.0 + i * 1000
            await r.zadd("player:matches:pagepuuid", {mid: ts})
            await r.hset(
                f"match:{mid}",
                mapping={"game_start": str(int(ts)), "game_mode": "CLASSIC"},
            )
            await r.hset(
                f"participant:{mid}:pagepuuid",
                mapping={
                    "win": "1",
                    "champion_name": f"Champ{i}",
                    "kills": "1",
                    "deaths": "0",
                    "assists": "0",
                },
            )

        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {
            "puuid": "pagepuuid",
            "region": "na1",
            "riot_id": "P#1",
            "page": "1",
        }
        request.app.state.r = r

        resp = await stats_matches(request)
        body = resp.body.decode()

        assert resp.status_code == 200
        # Page 1 should have exactly 5 matches (the remaining after page 0)
        row_count = body.count("<tr><td>")
        assert row_count == 5
        # Should NOT contain "Load more" since these are the last matches
        assert "Load more" not in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_stats_matches__page0_with_more__shows_load_more(self):
        """Page 0 with more than _MATCH_PAGE_SIZE matches shows Load more link."""
        import fakeredis.aioredis

        from lol_ui.main import _MATCH_PAGE_SIZE, stats_matches

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        total = _MATCH_PAGE_SIZE + 1
        for i in range(total):
            mid = f"NA1_P{i:04d}"
            ts = 1700000000000.0 + i * 1000
            await r.zadd("player:matches:morepuuid", {mid: ts})
            await r.hset(
                f"match:{mid}",
                mapping={"game_start": str(int(ts)), "game_mode": "CLASSIC"},
            )
            await r.hset(
                f"participant:{mid}:morepuuid",
                mapping={
                    "win": "1",
                    "champion_name": "X",
                    "kills": "0",
                    "deaths": "0",
                    "assists": "0",
                },
            )

        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {
            "puuid": "morepuuid",
            "region": "na1",
            "riot_id": "More#1",
            "page": "0",
        }
        request.app.state.r = r

        resp = await stats_matches(request)
        body = resp.body.decode()

        assert resp.status_code == 200
        assert "Load more" in body
        await r.aclose()


# ---------------------------------------------------------------------------
# _tail_file: large files, exact n lines
# ---------------------------------------------------------------------------


class TestTailFileLargeAndExact:
    """Edge cases for _tail_file byte-seek logic with large files."""

    def test_tail_file__exactly_n_lines__returns_all(self, tmp_path):
        """File with exactly n lines returns all n lines."""
        f = tmp_path / "exact.log"
        lines = [f"line{i}" for i in range(5)]
        f.write_text("\n".join(lines) + "\n")
        result = _tail_file(f, 5)
        assert len(result) == 5
        assert result[0] == "line0"
        assert result[4] == "line4"

    def test_tail_file__large_file__returns_last_n_via_byte_seek(self, tmp_path):
        """File much larger than n lines returns exactly last n via byte-seek."""
        f = tmp_path / "large.log"
        # Write 200 lines — each line is large enough to trigger byte-seek
        all_lines = [f"long_structured_log_line_number_{i:04d}_payload" for i in range(200)]
        f.write_text("\n".join(all_lines) + "\n")
        result = _tail_file(f, 10)
        assert len(result) == 10
        assert result[-1] == "long_structured_log_line_number_0199_payload"
        assert result[0] == "long_structured_log_line_number_0190_payload"

    def test_tail_file__single_line__returns_that_line(self, tmp_path):
        """File with a single line returns that line."""
        f = tmp_path / "single.log"
        f.write_text("only-line\n")
        result = _tail_file(f, 5)
        assert result == ["only-line"]

    def test_tail_file__no_trailing_newline__returns_lines(self, tmp_path):
        """File without trailing newline still returns all lines."""
        f = tmp_path / "notrail.log"
        f.write_text("line1\nline2\nline3")
        result = _tail_file(f, 10)
        assert len(result) == 3
        assert result[-1] == "line3"


# ---------------------------------------------------------------------------
# Phase 9: Global halt banner on all pages
# ---------------------------------------------------------------------------


class TestHaltBannerPlayers:
    """Phase 9: /players shows halt banner when system:halted is set."""

    @pytest.mark.asyncio
    async def test_players__shows_halt_banner_when_halted(self):
        import fakeredis.aioredis

        from lol_ui.main import show_players

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:halted", "1")
        await r.zadd("players:all", {"puuid-1": 1000.0})
        await r.hset(
            "player:puuid-1",
            mapping={
                "game_name": "Test",
                "tag_line": "1",
                "region": "na1",
                "seeded_at": "2026-01-01T00:00:00",
            },
        )

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "0"}

        resp = await show_players(request)
        body = resp.body.decode()

        assert "HALTED" in body
        assert "banner--error" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_players__no_halt_banner_when_not_halted(self):
        import fakeredis.aioredis

        from lol_ui.main import show_players

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "0"}

        resp = await show_players(request)
        body = resp.body.decode()

        assert "HALTED" not in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_players__empty__shows_halt_banner_when_halted(self):
        import fakeredis.aioredis

        from lol_ui.main import show_players

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:halted", "1")

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "0"}

        resp = await show_players(request)
        body = resp.body.decode()

        assert "HALTED" in body
        assert "banner--error" in body
        await r.aclose()


class TestHaltBannerDlq:
    """Phase 9: /dlq shows halt banner when system:halted is set."""

    @pytest.mark.asyncio
    async def test_dlq__shows_halt_banner_when_halted(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:halted", "1")
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_h1"},
            attempts=1,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-h1",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "HALTED" in body
        assert "banner--error" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq__no_halt_banner_when_not_halted(self):
        import fakeredis.aioredis

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "HALTED" not in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq__empty__shows_halt_banner_when_halted(self):
        import fakeredis.aioredis

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:halted", "1")

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "HALTED" in body
        assert "banner--error" in body
        await r.aclose()


class TestHaltBannerLogs:
    """Phase 9: /logs shows halt banner when system:halted is set."""

    @pytest.mark.asyncio
    async def test_logs__shows_halt_banner_when_halted(self):
        import fakeredis.aioredis

        from lol_ui.main import show_logs

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:halted", "1")

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        with patch.dict("os.environ", {"LOG_DIR": ""}):
            resp = await show_logs(request)
        body = resp.body.decode()

        assert "HALTED" in body
        assert "banner--error" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_logs__no_halt_banner_when_not_halted(self):
        import fakeredis.aioredis

        from lol_ui.main import show_logs

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        with patch.dict("os.environ", {"LOG_DIR": ""}):
            resp = await show_logs(request)
        body = resp.body.decode()

        assert "HALTED" not in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_logs__with_files__shows_halt_banner_when_halted(self, tmp_path):
        import fakeredis.aioredis

        from lol_ui.main import show_logs

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:halted", "1")

        log_file = tmp_path / "svc.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        with patch.dict("os.environ", {"LOG_DIR": str(tmp_path)}):
            resp = await show_logs(request)
        body = resp.body.decode()

        assert "HALTED" in body
        assert "banner--error" in body
        await r.aclose()


class TestHaltBannerStatsMatches:
    """Phase 9: /stats/matches shows halt banner when system:halted is set."""

    @pytest.mark.asyncio
    async def test_stats_matches__shows_halt_banner_when_halted(self):
        import fakeredis.aioredis

        from lol_ui.main import stats_matches

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:halted", "1")

        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {
            "puuid": "validpuuid",
            "region": "na1",
            "riot_id": "T#1",
            "page": "0",
        }
        request.app.state.r = r

        resp = await stats_matches(request)
        body = resp.body.decode()

        assert "HALTED" in body
        assert "banner--error" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_stats_matches__no_halt_banner_when_not_halted(self):
        import fakeredis.aioredis

        from lol_ui.main import stats_matches

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {
            "puuid": "validpuuid",
            "region": "na1",
            "riot_id": "T#1",
            "page": "0",
        }
        request.app.state.r = r

        resp = await stats_matches(request)
        body = resp.body.decode()

        assert "HALTED" not in body
        await r.aclose()


# ---------------------------------------------------------------------------
# Phase 9: DLQ inline replay button + POST endpoint
# ---------------------------------------------------------------------------


class TestDlqReplayButton:
    """Phase 9: Each DLQ entry has an inline Replay button."""

    @pytest.mark.asyncio
    async def test_show_dlq__each_entry_has_replay_button(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_rb1"},
            attempts=1,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-rb1",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "Replay" in body
        assert 'action="/dlq/replay/' in body
        assert 'method="post"' in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_dlq__action_column_header(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_ac"},
            attempts=1,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-ac",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "<th>Action</th>" in body
        await r.aclose()


class TestDlqReplayEndpoint:
    """Phase 9: POST /dlq/replay/{entry_id} replays a DLQ entry."""

    @pytest.mark.asyncio
    async def test_dlq_replay__replays_entry_to_original_stream(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import dlq_replay

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_rp1", "region": "na1"},
            attempts=2,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-rp1",
            dlq_attempts=1,
            priority="high",
        )
        entry_id = await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = MagicMock(max_attempts=5)

        resp = await dlq_replay(request, entry_id)

        # Should redirect to /dlq
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dlq"

        # DLQ entry should be deleted
        remaining = await r.xrange("stream:dlq")
        assert len(remaining) == 0

        # Original stream should have the replayed message
        replayed = await r.xrange("stream:match_id")
        assert len(replayed) == 1
        fields = replayed[0][1]
        assert fields["source_stream"] == "stream:match_id"
        assert fields["type"] == "match_id"
        assert fields["priority"] == "high"
        assert fields["dlq_attempts"] == "1"
        payload = json.loads(fields["payload"])
        assert payload["match_id"] == "NA1_rp1"
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq_replay__nonexistent_entry_returns_404(self):
        import fakeredis.aioredis

        from lol_ui.main import dlq_replay

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = MagicMock(max_attempts=5)

        resp = await dlq_replay(request, "0-0")

        assert resp.status_code == 404
        body = resp.body.decode()
        assert "not found" in body.lower()
        assert "Back to DLQ" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq_replay__corrupt_entry_returns_422(self):
        import fakeredis.aioredis

        from lol_ui.main import dlq_replay

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        entry_id = await r.xadd("stream:dlq", {"garbage": "data"})

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = MagicMock(max_attempts=5)

        resp = await dlq_replay(request, entry_id)

        assert resp.status_code == 422
        body = resp.body.decode()
        assert "corrupt" in body.lower()
        assert "Back to DLQ" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq_replay__preserves_envelope_fields(self):
        """Replayed envelope preserves enqueued_at, dlq_attempts, priority."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import dlq_replay

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"puuid": "p1", "region": "kr"},
            attempts=3,
            max_attempts=5,
            failure_code="http_5xx",
            failure_reason="server error",
            failed_by="parser",
            original_stream="stream:parse",
            original_message_id="orig-pres",
            enqueued_at="2026-01-01T00:00:00+00:00",
            dlq_attempts=2,
            priority="normal",
        )
        entry_id = await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = MagicMock(max_attempts=10)

        await dlq_replay(request, entry_id)

        replayed = await r.xrange("stream:parse")
        assert len(replayed) == 1
        fields = replayed[0][1]
        assert fields["enqueued_at"] == "2026-01-01T00:00:00+00:00"
        assert fields["dlq_attempts"] == "2"
        assert fields["priority"] == "normal"
        assert fields["source_stream"] == "stream:parse"
        assert fields["type"] == "parse"
        assert fields["max_attempts"] == "10"
        await r.aclose()


# ---------------------------------------------------------------------------
# Phase 9: DLQ pagination
# ---------------------------------------------------------------------------


class TestDlqPagination:
    """Phase 9: /dlq supports page and per_page query params."""

    @pytest.mark.asyncio
    async def test_dlq__default_page_shows_first_25(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import _DLQ_DEFAULT_PER_PAGE, show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        for i in range(30):
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
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        row_count = body.count("<tr><td>")
        assert row_count == _DLQ_DEFAULT_PER_PAGE
        assert "Next" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq__page1_shows_remaining(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        for i in range(30):
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
        request.query_params = {"page": "1"}

        resp = await show_dlq(request)
        body = resp.body.decode()

        # 30 entries, page 0 shows 25, page 1 shows 5
        row_count = body.count("<tr><td>")
        assert row_count == 5
        assert "Prev" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq__custom_per_page(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        for i in range(15):
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
        request.query_params = {"per_page": "10"}

        resp = await show_dlq(request)
        body = resp.body.decode()

        row_count = body.count("<tr><td>")
        assert row_count == 10
        assert "Next" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq__per_page_capped_at_50(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import _DLQ_MAX_PER_PAGE, show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        for i in range(60):
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
        request.query_params = {"per_page": "100"}

        resp = await show_dlq(request)
        body = resp.body.decode()

        row_count = body.count("<tr><td>")
        assert row_count == _DLQ_MAX_PER_PAGE
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq__prev_next_links_include_per_page(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        for i in range(15):
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
        request.query_params = {"page": "1", "per_page": "5"}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "per_page=5" in body
        assert "Prev" in body
        assert "Next" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq__page_shows_per_page_info(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_pp"},
            attempts=1,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-pp",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "entries per page" in body
        await r.aclose()


# ---------------------------------------------------------------------------
# Phase 9: _make_replay_envelope
# ---------------------------------------------------------------------------


class TestMakeReplayEnvelope:
    """Phase 9: _make_replay_envelope reconstructs MessageEnvelope from DLQ."""

    def test_reconstructs_envelope_from_dlq(self):
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import _make_replay_envelope

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_re1", "region": "na1"},
            attempts=3,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-re1",
            enqueued_at="2026-01-01T00:00:00+00:00",
            dlq_attempts=2,
            priority="high",
        )

        envelope = _make_replay_envelope(dlq, max_attempts=10)

        assert envelope.source_stream == "stream:match_id"
        assert envelope.type == "match_id"
        assert envelope.payload == {"match_id": "NA1_re1", "region": "na1"}
        assert envelope.max_attempts == 10
        assert envelope.enqueued_at == "2026-01-01T00:00:00+00:00"
        assert envelope.dlq_attempts == 2
        assert envelope.priority == "high"

    def test_type_derived_from_original_stream(self):
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.main import _make_replay_envelope

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"puuid": "p1"},
            attempts=1,
            max_attempts=5,
            failure_code="http_5xx",
            failure_reason="server error",
            failed_by="parser",
            original_stream="stream:parse",
            original_message_id="orig-tp",
        )

        envelope = _make_replay_envelope(dlq, max_attempts=5)

        assert envelope.type == "parse"
        assert envelope.source_stream == "stream:parse"


# ---------------------------------------------------------------------------
# P10-QA-1: Auto-seed must call set_priority BEFORE publish
# ---------------------------------------------------------------------------


class TestAutoSeedPriorityBeforePublish:
    @pytest.mark.asyncio
    async def test_auto_seed_player__sets_priority_before_publish(self):
        """set_priority() must be called BEFORE publish() to avoid race with Crawler."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import _auto_seed_player

        call_order: list[str] = []

        mock_r = AsyncMock()
        mock_r.get.return_value = None  # no halted, no cooldown
        mock_r.hget.return_value = None  # no existing seeded_at
        mock_r.set.return_value = True

        mock_cfg = MagicMock()
        mock_cfg.max_attempts = 5

        async def tracking_publish(*args, **kwargs):
            call_order.append("publish")

        async def tracking_set_priority(*args, **kwargs):
            call_order.append("set_priority")

        with (
            patch("lol_ui.main.publish", new_callable=AsyncMock) as mock_pub,
            patch("lol_ui.main.set_priority", new_callable=AsyncMock) as mock_sp,
        ):
            mock_pub.side_effect = tracking_publish
            mock_sp.side_effect = tracking_set_priority

            await _auto_seed_player(mock_r, "test-puuid", "GameName", "Tag", "na1", mock_cfg)

        assert call_order == ["set_priority", "publish"]


# ---------------------------------------------------------------------------
# P10-QA-2: Auto-seed must write to players:all sorted set
# ---------------------------------------------------------------------------


class TestAutoSeedWritesPlayersAll:
    @pytest.mark.asyncio
    async def test_auto_seed_player__writes_to_players_all(self):
        """Auto-seed must call zadd('players:all', {puuid: timestamp})."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.main import _auto_seed_player

        mock_r = AsyncMock()
        mock_r.get.return_value = None
        mock_r.hget.return_value = None
        mock_r.set.return_value = True

        mock_cfg = MagicMock()
        mock_cfg.max_attempts = 5

        with (
            patch("lol_ui.main.publish", new_callable=AsyncMock),
            patch("lol_ui.main.set_priority", new_callable=AsyncMock),
        ):
            await _auto_seed_player(mock_r, "seed-puuid-1", "Player", "NA1", "na1", mock_cfg)

        mock_r.zadd.assert_called_once()
        args, _kwargs = mock_r.zadd.call_args
        assert args[0] == "players:all"
        mapping = args[1]
        assert "seed-puuid-1" in mapping
        # Score should be a timestamp (positive number)
        assert mapping["seed-puuid-1"] > 0


# ---------------------------------------------------------------------------
# P10-DD-3/PM-01: Dashboard nav link
# ---------------------------------------------------------------------------


class TestNavItemsDashboardLink:
    def test_nav_items__contains_dashboard_link(self):
        """_NAV_ITEMS must include a '/' -> 'Dashboard' entry as the first item."""
        assert ("/", "Dashboard") in _NAV_ITEMS
        assert _NAV_ITEMS[0] == ("/", "Dashboard")


# ---------------------------------------------------------------------------
# P10-CW-7/DD-9: Riot Games attribution footer
# ---------------------------------------------------------------------------


class TestRiotAttributionFooter:
    def test_page__contains_riot_attribution(self):
        """_page() output must contain Riot Games legal attribution in a footer."""
        result = _page("Test", "<p>body</p>")
        assert "Riot Games" in result
        assert "<footer" in result
        assert (
            "isn\u2019t endorsed by Riot Games" in result
            or "isn&rsquo;t endorsed by Riot Games" in result
        )


# ---------------------------------------------------------------------------
# P10-DD-7: Dashboard region selector must show all regions
# ---------------------------------------------------------------------------


class TestDashboardRegionSelector:
    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua scripts")
    async def test_dashboard__region_select_has_all_regions(self):
        """Dashboard region <select> must include all _REGIONS, not just 4."""
        import fakeredis.aioredis

        from lol_ui.main import index

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        resp = await index(request)
        body = resp.body.decode()

        for region in _REGIONS:
            assert f'value="{region}"' in body, f"Region {region} missing from dashboard"

        # Count option tags — should be at least len(_REGIONS)
        option_count = body.count("<option value=")
        assert option_count >= len(_REGIONS)

        await r.aclose()


class TestCssTableScrollNowrap:
    def test_css__table_scroll_has_nowrap(self):
        """P10-RD-8: .table-scroll td/th must have white-space: nowrap."""
        assert "white-space: nowrap" in _CSS
        assert ".table-scroll td" in _CSS or ".table-scroll th" in _CSS


class TestRenderPlayerRowsSeededTruncated:
    def test_render_player_rows__seeded_at_truncated_to_date(self):
        """P10-RD-9: seeded_at ISO timestamp must be truncated to date-only."""
        rows = [("TestPlayer", "NA1", "na1", "2024-01-15T14:23:11+00:00")]
        result = _render_player_rows(rows)
        assert "2024-01-15" in result
        assert "T14:23:11" not in result
        assert "14:23:11+00:00" not in result


class TestCssSortControlsMinHeight:
    def test_css__sort_controls_has_min_height_44(self):
        """P10-RD-6: .sort-controls a must have min-height: 44px for tap targets."""
        idx = _CSS.find(".sort-controls a")
        assert idx != -1, ".sort-controls a rule not found in CSS"
        snippet = _CSS[idx : idx + 300]
        assert "min-height: 44px" in snippet


class TestCssPauseBtnMinHeight:
    def test_css__pause_btn_has_min_height(self):
        """P10-RD-11: #pause-btn must have min-height: 44px."""
        idx = _CSS.find("#pause-btn")
        assert idx != -1, "#pause-btn rule not found in CSS"
        snippet = _CSS[idx : idx + 200]
        assert "min-height: 44px" in snippet


class TestSecurityHeaders:
    """P10-SEC-4: Every response must include security headers."""

    def _get_response(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from starlette.testclient import TestClient

        with (
            patch("lol_ui.main.Config") as mock_cfg_cls,
            patch("lol_ui.main.get_redis") as mock_get_redis,
            patch("lol_ui.main.RiotClient") as mock_riot_cls,
        ):
            mock_cfg = MagicMock()
            mock_cfg.redis_url = "redis://localhost:6379/0"
            mock_cfg.riot_api_key = "RGAPI-test"
            mock_cfg_cls.return_value = mock_cfg

            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis

            mock_riot = AsyncMock()
            mock_riot_cls.return_value = mock_riot

            from lol_ui.main import app

            with TestClient(app) as client:
                return client.get("/health")

    def test_security_headers__x_content_type_options(self):
        """P10-SEC-4: X-Content-Type-Options: nosniff in every response."""
        resp = self._get_response()
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_security_headers__x_frame_options(self):
        """P10-SEC-4: X-Frame-Options: DENY in every response."""
        resp = self._get_response()
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_security_headers__referrer_policy(self):
        """P10-SEC-4: Referrer-Policy: no-referrer in every response."""
        resp = self._get_response()
        assert resp.headers.get("Referrer-Policy") == "no-referrer"


class TestChampionIconHtml:
    """P10-UX-1: champion icon rendering."""

    def test_with_version__contains_img_tag(self):
        result = _champion_icon_html("Zed", "14.1.1")
        assert "<img " in result
        assert 'class="champion-icon"' in result
        assert "ddragon.leagueoflegends.com" in result
        assert "14.1.1" in result
        assert "Zed.png" in result

    def test_no_version__returns_empty_string(self):
        result = _champion_icon_html("Zed", None)
        assert result == ""

    def test_empty_name__returns_empty_string(self):
        result = _champion_icon_html("", "14.1.1")
        assert result == ""

    def test_xss_name__html_escaped(self):
        result = _champion_icon_html("<script>alert(1)</script>", "14.1.1")
        assert "<script>" not in result
        assert html.escape("<script>alert(1)</script>") in result

    def test_onerror_hides_on_failure(self):
        """Graceful degradation: broken image hides itself."""
        result = _champion_icon_html("Zed", "14.1.1")
        assert "onerror=" in result
        assert "display" in result

    def test_lazy_loading(self):
        result = _champion_icon_html("Zed", "14.1.1")
        assert 'loading="lazy"' in result

    def test_css_class_in_stylesheet(self):
        """P10-UX-1: .champion-icon class must exist in the CSS."""
        assert ".champion-icon" in _CSS


class TestContentSecurityPolicy:
    """P12-SEC-1: Content-Security-Policy header in every response."""

    def _get_response(self):
        from unittest.mock import AsyncMock, MagicMock
        from unittest.mock import patch as _patch

        from starlette.testclient import TestClient

        with (
            _patch("lol_ui.main.Config") as mock_cfg_cls,
            _patch("lol_ui.main.get_redis") as mock_get_redis,
            _patch("lol_ui.main.RiotClient") as mock_riot_cls,
        ):
            mock_cfg = MagicMock()
            mock_cfg.redis_url = "redis://localhost:6379/0"
            mock_cfg.riot_api_key = "RGAPI-test"
            mock_cfg_cls.return_value = mock_cfg

            mock_redis = AsyncMock()
            mock_get_redis.return_value = mock_redis

            mock_riot = AsyncMock()
            mock_riot_cls.return_value = mock_riot

            from lol_ui.main import app

            with TestClient(app) as client:
                return client.get("/health")

    def test_csp_header_present(self):
        """CSP header must be present in response."""
        resp = self._get_response()
        csp = resp.headers.get("Content-Security-Policy")
        assert csp is not None, "Content-Security-Policy header missing"

    def test_csp_default_src_self(self):
        """CSP default-src must be 'self'."""
        resp = self._get_response()
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp

    def test_csp_script_src_unsafe_inline(self):
        """CSP script-src must allow 'unsafe-inline' for inline scripts."""
        resp = self._get_response()
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "script-src 'self' 'unsafe-inline'" in csp

    def test_csp_style_src_unsafe_inline(self):
        """CSP style-src must allow 'unsafe-inline' for inline styles."""
        resp = self._get_response()
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "style-src 'self' 'unsafe-inline'" in csp

    def test_csp_img_src_allows_ddragon(self):
        """CSP img-src must allow ddragon.leagueoflegends.com for champion icons."""
        resp = self._get_response()
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "ddragon.leagueoflegends.com" in csp

    def test_csp_connect_src_self(self):
        """CSP connect-src must be 'self' for AJAX polling."""
        resp = self._get_response()
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "connect-src 'self'" in csp


class TestDlqReplayEntryIdValidation:
    """P12-SEC-7: DLQ replay endpoint validates entry_id format."""

    @pytest.mark.asyncio
    async def test_dlq_replay__invalid_entry_id__returns_400(self):
        """Invalid entry_id (not timestamp-sequence) returns 400."""
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        from lol_ui.main import dlq_replay

        request = MagicMock()
        request.app.state.r = MagicMock()
        request.app.state.cfg = MagicMock(max_attempts=5)

        with pytest.raises(HTTPException) as exc_info:
            await dlq_replay(request, "../../etc/passwd")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_dlq_replay__path_traversal__returns_400(self):
        """Path traversal attempt in entry_id returns 400."""
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        from lol_ui.main import dlq_replay

        request = MagicMock()
        request.app.state.r = MagicMock()
        request.app.state.cfg = MagicMock(max_attempts=5)

        with pytest.raises(HTTPException) as exc_info:
            await dlq_replay(request, "abc-def")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_dlq_replay__valid_entry_id__accepted(self):
        """Valid entry_id (e.g. '1234567890-0') passes validation (not 400)."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.main import dlq_replay

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = MagicMock(max_attempts=5)

        # Valid format, but no entry exists -> should return 404, not 400
        resp = await dlq_replay(request, "1234567890-0")
        assert resp.status_code == 404
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq_replay__script_injection__returns_400(self):
        """Script injection in entry_id returns 400."""
        from unittest.mock import MagicMock

        from fastapi import HTTPException

        from lol_ui.main import dlq_replay

        request = MagicMock()
        request.app.state.r = MagicMock()
        request.app.state.cfg = MagicMock(max_attempts=5)

        with pytest.raises(HTTPException) as exc_info:
            await dlq_replay(request, "<script>alert(1)</script>")
        assert exc_info.value.status_code == 400


class TestLogsAsyncIo:
    """P12-OPT-2: _merged_log_lines called via asyncio.to_thread."""

    @pytest.mark.asyncio
    async def test_logs_fragment__calls_merged_log_lines_in_thread(self, tmp_path):
        """logs_fragment wraps _merged_log_lines in asyncio.to_thread."""
        from lol_ui.main import logs_fragment

        log_file = tmp_path / "svc.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        call_tracker: dict[str, object] = {"called": False, "func": None}
        original_to_thread = __import__("asyncio").to_thread

        async def tracking_to_thread(func, *args, **kwargs):
            call_tracker["called"] = True
            call_tracker["func"] = func.__name__ if hasattr(func, "__name__") else str(func)
            return await original_to_thread(func, *args, **kwargs)

        with (
            patch.dict("os.environ", {"LOG_DIR": str(tmp_path)}),
            patch("lol_ui.main.asyncio") as mock_asyncio,
        ):
            mock_asyncio.to_thread = tracking_to_thread
            await logs_fragment()

        assert call_tracker["called"], "asyncio.to_thread was not called in logs_fragment"
        assert call_tracker["func"] == "_merged_log_lines"

    @pytest.mark.asyncio
    async def test_show_logs__calls_merged_log_lines_in_thread(self, tmp_path):
        """show_logs wraps _merged_log_lines in asyncio.to_thread."""
        import fakeredis.aioredis

        from lol_ui.main import show_logs

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        log_file = tmp_path / "svc.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r

        call_tracker: dict[str, object] = {"called": False, "func": None}
        original_to_thread = __import__("asyncio").to_thread

        async def tracking_to_thread(func, *args, **kwargs):
            call_tracker["called"] = True
            call_tracker["func"] = func.__name__ if hasattr(func, "__name__") else str(func)
            return await original_to_thread(func, *args, **kwargs)

        with (
            patch.dict("os.environ", {"LOG_DIR": str(tmp_path)}),
            patch("lol_ui.main.asyncio") as mock_asyncio,
        ):
            mock_asyncio.to_thread = tracking_to_thread
            await show_logs(request)

        assert call_tracker["called"], "asyncio.to_thread was not called in show_logs"
        assert call_tracker["func"] == "_merged_log_lines"
        await r.aclose()
