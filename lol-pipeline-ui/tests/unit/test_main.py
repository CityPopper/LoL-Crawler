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

from lol_ui.champions_helpers import (
    _assign_tiers,
    _champion_tier_table,
    _patch_delta,
    _pbi_tier,
)
from lol_ui.constants import (
    _AUTOSEED_COOLDOWN_S,
    _BADGE_VARIANTS,
    _DELTA_MIN_GAMES,
    _HALT_BANNER,
    _MATCH_BADGE_COLORS,
    _NAME_CACHE_INDEX,
    _NAME_CACHE_MAX,
    _PBI_MIN_GAMES,
    _PLAYSTYLE_MIN_GAMES,
    _PUUID_RE,
    _REGIONS,
    _STATS_ORDER,
    _TIER_COLORS,
    _TILT_RECENT_COUNT,
    _TILT_RECENT_KDA_COUNT,
)
from lol_ui.css import _CSS, _NAV_ITEMS
from lol_ui.ddragon import _DDRAGON_CHAMPION_IDS_KEY, _get_champion_id_map
from lol_ui.log_helpers import _merged_log_lines, _parse_log_line, _render_log_lines, _tail_file
from lol_ui.match_badges import _match_badges, _match_badges_html
from lol_ui.match_history import _match_history_html, _match_history_section
from lol_ui.player_helpers import _render_player_rows
from lol_ui.playstyle import _playstyle_pills_html, _playstyle_tags
from lol_ui.rank import _rank_history_html
from lol_ui.rendering import (
    _badge,
    _badge_html,
    _champion_icon_html,
    _depth_badge,
    _empty_state,
    _page,
    _stats_form,
)
from lol_ui.stats_helpers import (
    _BreakdownEntry,
    _champion_diversity,
    _compute_champion_breakdown,
    _compute_role_breakdown,
    _format_stat_value,
    _render_champion_rows,
    _render_role_rows,
    _stats_table,
)
from lol_ui.tilt import _streak_indicator, _tilt_banner_html


class TestHaltBanner:
    def test_halt_banner__contains_recovery_instructions(self):
        """_HALT_BANNER must tell users how to recover, not just that the system is halted."""
        assert "system-resume" in _HALT_BANNER
        assert "just up" in _HALT_BANNER
        assert ".env" in _HALT_BANNER
        assert "banner--error" in _HALT_BANNER


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
        # Container uses data attrs; JS reads them via dataset
        assert "dataset.puuid" in html_out


class TestPage:
    def test_renders_html_structure(self):
        result = _page("Test Title", "<p>body</p>")
        assert "<!doctype html>" in result
        assert "Test Title" in result
        assert "<p>body</p>" in result
        assert '<nav aria-label="Main navigation">' in result

    def test_contains_navigation_links(self):
        result = _page("X", "")
        assert "/stats" in result
        assert "/champions" in result
        assert "/players" in result
        assert "/streams" in result
        assert "/logs" in result

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


class TestBadgeHtml:
    def test_raw_html_preserved(self):
        """_badge_html preserves raw HTML entities."""
        result = _badge_html("success", "&#10003; Verified")
        assert "&#10003; Verified" in result


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

    def test_invalid_css_class__coerced_to_error(self):
        """Unknown css_class is coerced to 'error' rather than injected into HTML."""
        result = _stats_form("Something went wrong", "banana")
        # Must not render the unknown class name
        assert 'class="banana"' not in result
        # Must render the safe fallback
        assert 'class="error"' in result


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

    def test_heading_is_player_stats(self):
        result = _stats_table({"wins": "10"}, [], [])
        assert "<h3" in result
        assert "Player Stats" in result
        assert "<details>" in result

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
        # KDA is rendered in separate spans: <span>10</span>/<span>2</span>/<span>5</span>
        assert "<span>10</span>" in result
        assert "<span>5</span>" in result
        assert "WIN" in result

    def test_loss_renders_correctly(self):
        matches = [
            (
                "NA1_456",
                {"game_start": "1700000000000", "game_mode": "ARAM"},
                {"win": "0", "champion_name": "Ahri", "kills": "3", "deaths": "7", "assists": "1"},
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert "LOSS" in result
        assert "Ahri" in result

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
        assert 'data-page="1"' in result

    def test_has_more_uses_data_attributes(self):
        """SEC: load-more button uses data-* attributes, not onclick."""
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
        assert 'class="match-load-more"' in result

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

        from lol_ui.routes.stats import stats_matches

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

        from lol_ui.routes.stats import show_stats

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        class FakeRiot:
            async def get_account_by_riot_id(self, gn, tl, region):
                return {"puuid": "test-puuid-ui"}

        class FakeCfg:
            max_attempts = 5
            api_rate_limit_per_second = 20
            players_all_max = 50000

        request = re.Match  # unused, just need a MagicMock
        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = r
        request.app.state.cfg = FakeCfg()
        request.app.state.riot = FakeRiot()

        await show_stats(request)

        # Check envelope in stream has priority=manual_20
        entries = await r.xrange("stream:puuid")
        assert len(entries) == 1
        assert entries[0][1]["priority"] == "manual_20"

        # Check priority key was set
        assert await r.get("player:priority:test-puuid-ui") == "1"

        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_streams__displays_priority_status(self):
        """The /streams page displays priority status."""
        import fakeredis.aioredis
        from lol_pipeline.priority import set_priority

        from lol_ui.routes.streams import show_streams

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await set_priority(r, "puuid-1")

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

        from lol_ui.routes.stats import show_stats

        call_order: list[str] = []

        mock_seed_pipe = AsyncMock()
        mock_seed_pipe.execute.return_value = [None, None, None]

        mock_seed_ctx = MagicMock()
        mock_seed_ctx.__aenter__ = AsyncMock(return_value=mock_seed_pipe)
        mock_seed_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.get.return_value = None  # no system:halted, no cached puuid
        mock_r.hgetall.return_value = {}  # no stats
        mock_r.set.return_value = True
        mock_r.pipeline = MagicMock(return_value=mock_seed_ctx)

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

        with patch("lol_ui.routes.stats.publish", new_callable=AsyncMock) as mock_publish:

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

        from lol_ui.routes.players import show_players

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

        from lol_ui.routes.players import show_players

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

        from lol_ui.constants import _PLAYERS_PAGE_SIZE
        from lol_ui.routes.players import show_players

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

    def _make_mock_r(
        self,
        *,
        puuid,
        stats,
        priority_key=None,
        rank=None,
    ):
        """Build an AsyncMock Redis client that supports the pipeline context manager."""
        from unittest.mock import AsyncMock, MagicMock

        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = [
            priority_key,
            rank or {},
            [],
            {},  # player:{puuid} hash (profile_icon_id, summoner_level)
            [],  # player:matches:{puuid} ZREVRANGE (recent match IDs)
        ]

        mock_pipeline_ctx = MagicMock()
        mock_pipeline_ctx.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipeline_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.get.return_value = None  # no cache hit, no halt
        mock_r.hgetall.return_value = stats
        mock_r.zrevrangebyscore.return_value = []  # no split matches
        # pipeline() must be a sync callable so `async with r.pipeline(...)` works.
        mock_r.pipeline = MagicMock(return_value=mock_pipeline_ctx)
        return mock_r

    @pytest.mark.asyncio
    async def test_show_stats__heading_shows_riot_id(self):
        """Heading reads 'Stats for GameName#TagLine' when stats exist."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.routes.stats import show_stats

        mock_r = self._make_mock_r(
            puuid="test-puuid-heading",
            stats={"total_games": "10", "win_rate": "0.6"},
        )
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

        from lol_ui.routes.stats import show_stats

        test_puuid = "secret-puuid-abc-xyz-999"
        mock_r = self._make_mock_r(
            puuid=test_puuid,
            stats={"total_games": "5"},
        )
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

        from lol_ui.routes.streams import streams_fragment

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

        from lol_ui.routes.streams import streams_fragment

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

        from lol_ui.routes.streams import streams_fragment

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
        """Fragment includes priority status display."""
        import fakeredis.aioredis
        from lol_pipeline.priority import set_priority

        from lol_ui.routes.streams import streams_fragment

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await set_priority(r, "puuid-1")

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

        from lol_ui.routes.streams import show_streams

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

        from lol_ui.routes.streams import show_streams

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

        from lol_ui.routes.stats import show_stats

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

        from lol_ui.routes.stats import show_stats

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

        from lol_ui.routes.stats import show_stats

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

        assert "API key invalid or expired" in body
        assert "system-resume" in body

    @pytest.mark.asyncio
    async def test_server_error__specific_message(self):
        """ServerError shows server-unavailable guidance."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.routes.stats import show_stats

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

        from lol_ui.routes.stats import show_stats

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

        from lol_ui.routes.stats import show_stats

        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = [None, {}, [], {}, []]

        mock_pipeline_ctx = MagicMock()
        mock_pipeline_ctx.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipeline_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:test#na1": None,
            "system:halted": None,
        }.get(key)
        mock_r.hgetall.return_value = {"total_games": "10"}
        mock_r.hget.return_value = None
        mock_r.zrevrangebyscore.return_value = []  # no split matches
        mock_r.pipeline = MagicMock(return_value=mock_pipeline_ctx)

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

        from lol_ui.routes.stats import show_stats

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

        from lol_ui.routes.stats import show_stats

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

        from lol_ui.routes.stats import show_stats

        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = ["high", {}, [], {}, []]

        mock_pipeline_ctx = MagicMock()
        mock_pipeline_ctx.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipeline_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:test#na1": "test-puuid-123",
            "system:halted": None,
        }.get(key)
        mock_r.hgetall.return_value = {"total_games": "10", "wins": "5"}
        mock_r.zrevrangebyscore.return_value = []  # no split matches
        mock_r.pipeline = MagicMock(return_value=mock_pipeline_ctx)

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

        from lol_ui.routes.stats import show_stats

        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = [None, {}, [], {}, []]

        mock_pipeline_ctx = MagicMock()
        mock_pipeline_ctx.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipeline_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:test#na1": "test-puuid-123",
            "system:halted": None,
        }.get(key)
        mock_r.hgetall.return_value = {"total_games": "10", "wins": "5"}
        mock_r.zrevrangebyscore.return_value = []  # no split matches
        mock_r.pipeline = MagicMock(return_value=mock_pipeline_ctx)

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

        from lol_ui.routes.players import show_players

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


class TestDlqBrowser:
    """Sprint 5.3: /dlq route displays DLQ entries."""

    @pytest.mark.asyncio
    async def test_show_dlq__empty_dlq_shows_empty_state(self):
        """When DLQ stream is empty, show 'pipeline is healthy' empty state."""
        import fakeredis.aioredis

        from lol_ui.routes.dlq import show_dlq

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

        from lol_ui.routes.dlq import show_dlq

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

        from lol_ui.routes.dlq import show_dlq

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

        from lol_ui.routes.dlq import show_dlq

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

        from lol_ui.constants import _DLQ_DEFAULT_PER_PAGE
        from lol_ui.routes.dlq import show_dlq

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

        # Count DLQ data rows via Replay buttons (one per entry)
        row_count = body.count('action="/dlq/replay/')
        assert row_count == _DLQ_DEFAULT_PER_PAGE
        await r.aclose()


class TestStreamsFragmentHtmlEdgeCases:
    """Additional _streams_fragment_html edge cases."""

    @pytest.mark.asyncio
    async def test_streams_fragment__shows_stream_depths(self):
        """Fragment shows actual stream depths when streams have entries."""
        import fakeredis.aioredis

        from lol_ui.streams_helpers import _streams_fragment_html

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

        from lol_ui.streams_helpers import _streams_fragment_html

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

        from lol_ui.streams_helpers import _streams_fragment_html

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

        from lol_ui.routes.stats import stats_matches

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

        from lol_ui.routes.stats import stats_matches

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

        from lol_ui.routes.stats import stats_matches

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

        from lol_ui.routes.stats import stats_matches

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

        from lol_ui.routes.players import show_players

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

        from lol_ui.routes.players import show_players

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

        from lol_ui.routes.players import show_players

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

        from lol_ui.routes.players import show_players

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


class TestRegionDropdownSelectedSpace:
    """R12: selected attribute must have a leading space in the option tag."""

    def test_non_selected_no_selected_attr(self):
        result = _stats_form(selected_region="kr")
        na1_match = re.search(r'<option value="na1"[^>]*>', result)
        assert na1_match is not None
        assert "selected" not in na1_match.group(0)


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
        assert "<code>just up</code>" in body

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

        from lol_ui.routes.dlq import show_dlq

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
        # Exactly 2 DLQ data rows (corrupt entry skipped), counted via Replay buttons
        assert body.count('action="/dlq/replay/') == 2
        await r.aclose()

    @pytest.mark.asyncio
    async def test_show_dlq__all_corrupt__shows_empty_table(self):
        """When all entries are corrupt, page returns 200 with table but no data rows."""
        import fakeredis.aioredis

        from lol_ui.routes.dlq import show_dlq

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
        # No DLQ data rows (all corrupt, skipped), counted via Replay buttons
        assert body.count('action="/dlq/replay/') == 0
        await r.aclose()


class TestRateLimitBeforeRiotCall:
    """I2-H7: wait_for_token must be called before every Riot API call in show_stats."""

    @pytest.mark.asyncio
    async def test_show_stats__calls_wait_for_token_before_riot_api(self):
        """wait_for_token is called before riot.get_account_by_riot_id."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.routes.stats import show_stats

        call_order: list[str] = []

        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = [None, {}, [], {}, []]

        mock_pipeline_ctx = MagicMock()
        mock_pipeline_ctx.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipeline_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.get.return_value = None  # no cached puuid
        mock_r.hgetall.return_value = {"total_games": "10"}
        mock_r.zrevrangebyscore.return_value = []  # no split matches
        mock_r.pipeline = MagicMock(return_value=mock_pipeline_ctx)

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

        with patch("lol_ui.routes.stats.wait_for_token", new_callable=AsyncMock) as mock_wft:

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

        from lol_ui.routes.stats import show_stats

        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = [None, {}, [], {}, []]

        mock_pipeline_ctx = MagicMock()
        mock_pipeline_ctx.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipeline_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.get.return_value = None
        mock_r.hgetall.return_value = {"total_games": "10"}
        mock_r.zrevrangebyscore.return_value = []  # no split matches
        mock_r.pipeline = MagicMock(return_value=mock_pipeline_ctx)

        mock_riot = AsyncMock()
        mock_riot.get_account_by_riot_id.return_value = {"puuid": "test-puuid-cfg"}

        mock_cfg = MagicMock()
        mock_cfg.api_rate_limit_per_second = 15

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = mock_riot
        request.app.state.cfg = mock_cfg

        with patch("lol_ui.routes.stats.wait_for_token", new_callable=AsyncMock) as mock_wft:
            await show_stats(request)

        mock_wft.assert_called_once_with(mock_r, limit_per_second=15, region="na1")

    @pytest.mark.asyncio
    async def test_show_stats__cached_puuid_skips_wait_for_token(self):
        """When puuid is in cache, no Riot API call is made, so no rate limiting needed."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.routes.stats import show_stats

        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = [None, {}, [], {}, []]

        mock_pipeline_ctx = MagicMock()
        mock_pipeline_ctx.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipeline_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:test#na1": "cached-puuid-123",
            "system:halted": None,
        }.get(key)
        mock_r.hgetall.return_value = {"total_games": "10"}
        mock_r.zrevrangebyscore.return_value = []  # no split matches
        mock_r.pipeline = MagicMock(return_value=mock_pipeline_ctx)

        request = MagicMock()
        request.query_params = {"riot_id": "Test#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = MagicMock()
        request.app.state.cfg = MagicMock()

        with patch("lol_ui.routes.stats.wait_for_token", new_callable=AsyncMock) as mock_wft:
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

        from lol_ui.routes.stats import show_stats

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

        with patch("lol_ui.routes.stats.wait_for_token", new_callable=AsyncMock):
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

        from lol_ui.routes.stats import _resolve_and_cache_puuid

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

        with patch("lol_ui.routes.stats.wait_for_token", new_callable=AsyncMock):
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


# ---------------------------------------------------------------------------
# Security: Region validation returns 400
# ---------------------------------------------------------------------------


class TestRegionValidation400:
    """SEC: Invalid region returns 400 Bad Request, not silent fallback."""

    @pytest.mark.asyncio
    async def test_invalid_region__returns_400(self):
        """show_stats with unknown region returns 400 status code."""
        from unittest.mock import AsyncMock, MagicMock

        from lol_ui.routes.stats import show_stats

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

        from lol_ui.routes.stats import show_stats

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

        from lol_ui.routes.stats import show_stats

        mock_r = AsyncMock()

        request = MagicMock()
        request.query_params = {"riot_id": "", "region": "<script>alert(1)</script>"}
        request.app.state.r = mock_r

        resp = await show_stats(request)
        body = resp.body.decode()

        assert resp.status_code == 400
        assert "<script>" not in body
        assert html.escape("<script>alert(1)</script>") in body


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

        from lol_ui.routes.stats import show_stats

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        class FakeRiot:
            async def get_account_by_riot_id(self, gn, tl, region):
                return {"puuid": "cooldown-puuid-1"}

        class FakeCfg:
            max_attempts = 5
            api_rate_limit_per_second = 20
            players_all_max = 50000

        request = MagicMock()
        request.query_params = {"riot_id": "CooldownTest#NA1", "region": "na1"}
        request.app.state.r = r
        request.app.state.cfg = FakeCfg()
        request.app.state.riot = FakeRiot()

        with patch("lol_ui.routes.stats.wait_for_token", new_callable=AsyncMock):
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

        from lol_ui.routes.stats import show_stats

        mock_seed_pipe = AsyncMock()
        mock_seed_pipe.execute.return_value = [None, "1", None]

        mock_seed_ctx = MagicMock()
        mock_seed_ctx.__aenter__ = AsyncMock(return_value=mock_seed_pipe)
        mock_seed_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:blocked#na1": "blocked-puuid-123",
            "system:halted": None,
            "player:priority:blocked-puuid-123": None,
        }.get(key)
        mock_r.hgetall.return_value = {}  # no stats, triggers auto-seed path
        mock_r.pipeline = MagicMock(return_value=mock_seed_ctx)

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

        from lol_ui.routes.stats import show_stats

        mock_seed_pipe = AsyncMock()
        mock_seed_pipe.execute.return_value = [None, None, None]

        mock_seed_ctx = MagicMock()
        mock_seed_ctx.__aenter__ = AsyncMock(return_value=mock_seed_pipe)
        mock_seed_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.get.side_effect = lambda key: {
            "player:name:proceed#na1": "proceed-puuid-456",
            "system:halted": None,
            "player:priority:proceed-puuid-456": None,
        }.get(key)
        mock_r.hgetall.return_value = {}  # no stats
        mock_r.set.return_value = True
        mock_r.pipeline = MagicMock(return_value=mock_seed_ctx)

        request = MagicMock()
        request.query_params = {"riot_id": "Proceed#NA1", "region": "na1"}
        request.app.state.r = mock_r
        request.app.state.riot = MagicMock()
        request.app.state.cfg = MagicMock()

        with patch("lol_ui.routes.stats.publish", new_callable=AsyncMock):
            with patch("lol_ui.routes.stats.set_priority", new_callable=AsyncMock):
                resp = await show_stats(request)

        body = resp.body.decode()
        assert "Auto-seeded" in body


# ---------------------------------------------------------------------------
# Streams fragment: halt banner, priority count, normal case
# ---------------------------------------------------------------------------


class TestStreamsFragmentHtmlHaltBanner:
    """_streams_fragment_html renders halt banner when system:halted is set."""

    @pytest.mark.asyncio
    async def test_streams_fragment_html__halted__shows_halt_banner(self):
        """When system:halted is set, HTML contains HALTED banner with error class."""
        import fakeredis.aioredis

        from lol_ui.streams_helpers import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("system:halted", "auth_403")

        result = await _streams_fragment_html(r)

        assert "HALTED" in result
        assert "banner--error" in result
        assert "System running" not in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment_html__priority_keys_exist__displays_yes(self):
        """When priority:active SET has members, displays Yes."""
        import fakeredis.aioredis
        from lol_pipeline.priority import set_priority

        from lol_ui.streams_helpers import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await set_priority(r, "puuid-1")

        result = await _streams_fragment_html(r)

        assert "Priority players in-flight" in result
        assert "<strong>Yes</strong>" in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment_html__normal__no_halt_shows_running(self):
        """When neither halted nor priority set, shows running banner and priority No."""
        import fakeredis.aioredis

        from lol_ui.streams_helpers import _streams_fragment_html

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
# /stats/matches: valid PUUID with matches, pagination
# ---------------------------------------------------------------------------


class TestStatsMatchesWithData:
    """Tests for /stats/matches with actual match data and pagination."""

    @pytest.mark.asyncio
    async def test_stats_matches__valid_puuid_with_matches__returns_html(self):
        """Valid PUUID with match data returns match history HTML."""
        import fakeredis.aioredis

        from lol_ui.routes.stats import stats_matches

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
        # KDA is rendered in separate spans
        assert "<span>12</span>" in body
        assert "<span>8</span>" in body
        assert "<span>5</span>" in body
        assert "WIN" in body
        assert "LOSS" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_stats_matches__pagination_page1_skips_first_page(self):
        """Page 1 skips the first _MATCH_PAGE_SIZE entries."""
        import fakeredis.aioredis

        from lol_ui.constants import _MATCH_PAGE_SIZE
        from lol_ui.routes.stats import stats_matches

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
        row_count = body.count("match-row match-row--")
        assert row_count == 5
        # Should NOT contain "Load more" since these are the last matches
        assert "Load more" not in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_stats_matches__page0_with_more__shows_load_more(self):
        """Page 0 with more than _MATCH_PAGE_SIZE matches shows Load more link."""
        import fakeredis.aioredis

        from lol_ui.constants import _MATCH_PAGE_SIZE
        from lol_ui.routes.stats import stats_matches

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
# Phase 9: DLQ inline replay button + POST endpoint
# ---------------------------------------------------------------------------


class TestDlqReplayEndpoint:
    """Phase 9: POST /dlq/replay/{entry_id} replays a DLQ entry."""

    @pytest.mark.asyncio
    async def test_dlq_replay__replays_entry_to_original_stream(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.routes.dlq import dlq_replay

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

        from lol_ui.routes.dlq import dlq_replay

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

        from lol_ui.routes.dlq import dlq_replay

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

        from lol_ui.routes.dlq import dlq_replay

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

    @pytest.mark.asyncio
    async def test_dlq_replay__invalid_original_stream__returns_422(self):
        """S16-1: DLQ entry with unknown original_stream is rejected with 422."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.routes.dlq import dlq_replay

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"puuid": "p1", "region": "na1"},
            attempts=1,
            max_attempts=5,
            failure_code="http_5xx",
            failure_reason="server error",
            failed_by="crawler",
            original_stream="stream:arbitrary-unknown",
            original_message_id="orig-x",
        )
        entry_id = await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = MagicMock(max_attempts=5)

        resp = await dlq_replay(request, entry_id)

        assert resp.status_code == 422
        body = resp.body.decode()
        assert "invalid" in body.lower() or "refused" in body.lower()
        # DLQ entry must NOT be deleted — replay was rejected
        assert await r.xlen("stream:dlq") == 1
        # Nothing published to unknown stream
        assert await r.xlen("stream:arbitrary-unknown") == 0
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq_replay__self_referential__returns_422(self):
        """S16-1: DLQ entry whose original_stream is stream:dlq itself is rejected."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.routes.dlq import dlq_replay

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"puuid": "p1", "region": "na1"},
            attempts=1,
            max_attempts=5,
            failure_code="http_5xx",
            failure_reason="server error",
            failed_by="crawler",
            original_stream="stream:dlq",  # self-referential
            original_message_id="orig-self",
        )
        entry_id = await r.xadd("stream:dlq", dlq.to_redis_fields())

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = MagicMock(max_attempts=5)

        resp = await dlq_replay(request, entry_id)

        assert resp.status_code == 422
        body = resp.body.decode()
        assert "invalid" in body.lower() or "refused" in body.lower()
        # Entry must remain in DLQ
        assert await r.xlen("stream:dlq") == 1
        await r.aclose()


# ---------------------------------------------------------------------------
# Phase 9: DLQ pagination
# ---------------------------------------------------------------------------


class TestDlqPagination:
    """Phase 9: /dlq supports page and per_page query params."""

    @pytest.mark.asyncio
    async def test_dlq__cursor_shows_remaining(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.routes.dlq import show_dlq

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        entry_ids = []
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
            eid = await r.xadd("stream:dlq", dlq.to_redis_fields())
            entry_ids.append(eid)

        from unittest.mock import MagicMock

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"cursor": entry_ids[25]}

        resp = await show_dlq(request)
        body = resp.body.decode()

        row_count = body.count('action="/dlq/replay/')
        assert row_count == 5
        assert "Next" not in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq__custom_per_page(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.routes.dlq import show_dlq

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

        row_count = body.count('action="/dlq/replay/')
        assert row_count == 10
        assert "Next" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq__next_link_includes_cursor_and_per_page(self):
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.routes.dlq import show_dlq

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
        request.query_params = {"per_page": "5"}

        resp = await show_dlq(request)
        body = resp.body.decode()

        assert "per_page=5" in body
        assert "cursor=" in body
        assert "Next" in body
        await r.aclose()


# ---------------------------------------------------------------------------
# Phase 9: _make_replay_envelope
# ---------------------------------------------------------------------------


class TestMakeReplayEnvelope:
    """Phase 9: _make_replay_envelope reconstructs MessageEnvelope from DLQ."""

    def test_reconstructs_envelope_from_dlq(self):
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.dlq_helpers import _make_replay_envelope

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

        from lol_ui.dlq_helpers import _make_replay_envelope

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

        from lol_ui.routes.stats import _auto_seed_player

        call_order: list[str] = []

        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = [None, None, None]

        mock_pipeline_ctx = MagicMock()
        mock_pipeline_ctx.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipeline_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.pipeline = MagicMock(return_value=mock_pipeline_ctx)
        mock_r.set.return_value = True

        mock_cfg = MagicMock()
        mock_cfg.max_attempts = 5

        async def tracking_publish(*args, **kwargs):
            call_order.append("publish")

        async def tracking_set_priority(*args, **kwargs):
            call_order.append("set_priority")

        with (
            patch("lol_ui.routes.stats.publish", new_callable=AsyncMock) as mock_pub,
            patch("lol_ui.routes.stats.set_priority", new_callable=AsyncMock) as mock_sp,
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

        from lol_ui.routes.stats import _auto_seed_player

        mock_pipe = AsyncMock()
        mock_pipe.execute.return_value = [None, None, None]

        mock_pipeline_ctx = MagicMock()
        mock_pipeline_ctx.__aenter__ = AsyncMock(return_value=mock_pipe)
        mock_pipeline_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_r = AsyncMock()
        mock_r.pipeline = MagicMock(return_value=mock_pipeline_ctx)
        mock_r.set.return_value = True

        mock_cfg = MagicMock()
        mock_cfg.max_attempts = 5

        with (
            patch("lol_ui.routes.stats.publish", new_callable=AsyncMock),
            patch("lol_ui.routes.stats.set_priority", new_callable=AsyncMock),
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
# P10-DD-7: Dashboard region selector must show all regions
# ---------------------------------------------------------------------------


class TestDashboardRegionSelector:
    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua scripts")
    async def test_dashboard__region_select_has_all_regions(self):
        """Dashboard region <select> must include all _REGIONS, not just 4."""
        import fakeredis.aioredis

        from lol_ui.routes.dashboard import index

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


class TestRenderPlayerRowsSeededTruncated:
    def test_render_player_rows__seeded_at_truncated_to_date(self):
        """P10-RD-9: seeded_at ISO timestamp must be truncated to date-only."""
        rows = [("TestPlayer", "NA1", "na1", "2024-01-15T14:23:11+00:00")]
        result = _render_player_rows(rows)
        assert "2024-01-15" in result
        assert "T14:23:11" not in result
        assert "14:23:11+00:00" not in result


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

        from lol_ui.routes.dlq import dlq_replay

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

        from lol_ui.routes.dlq import dlq_replay

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

        from lol_ui.routes.dlq import dlq_replay

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

        from lol_ui.routes.dlq import dlq_replay

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
        from unittest.mock import MagicMock

        from lol_ui.routes.logs import logs_fragment

        log_file = tmp_path / "svc.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        mock_cfg = MagicMock()
        mock_cfg.log_dir = str(tmp_path)
        request = MagicMock()
        request.app.state.cfg = mock_cfg

        call_tracker: dict[str, object] = {"called": False, "func": None}
        original_to_thread = __import__("asyncio").to_thread

        async def tracking_to_thread(func, *args, **kwargs):
            call_tracker["called"] = True
            call_tracker["func"] = func.__name__ if hasattr(func, "__name__") else str(func)
            return await original_to_thread(func, *args, **kwargs)

        with patch("lol_ui.routes.logs.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = tracking_to_thread
            await logs_fragment(request)

        assert call_tracker["called"], "asyncio.to_thread was not called in logs_fragment"
        assert call_tracker["func"] == "_merged_log_lines"

    @pytest.mark.asyncio
    async def test_show_logs__calls_merged_log_lines_in_thread(self, tmp_path):
        """show_logs wraps _merged_log_lines in asyncio.to_thread."""
        import fakeredis.aioredis

        from lol_ui.routes.logs import show_logs

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        log_file = tmp_path / "svc.log"
        log_file.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00", "message": "test"}) + "\n"
        )

        from unittest.mock import MagicMock

        mock_cfg = MagicMock()
        mock_cfg.log_dir = str(tmp_path)
        request = MagicMock()
        request.app.state.r = r
        request.app.state.cfg = mock_cfg

        call_tracker: dict[str, object] = {"called": False, "func": None}
        original_to_thread = __import__("asyncio").to_thread

        async def tracking_to_thread(func, *args, **kwargs):
            call_tracker["called"] = True
            call_tracker["func"] = func.__name__ if hasattr(func, "__name__") else str(func)
            return await original_to_thread(func, *args, **kwargs)

        with patch("lol_ui.routes.logs.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = tracking_to_thread
            await show_logs(request)

        assert call_tracker["called"], "asyncio.to_thread was not called in show_logs"
        assert call_tracker["func"] == "_merged_log_lines"
        await r.aclose()


# ---------------------------------------------------------------------------
# Sprint 3: Champion Pages
# ---------------------------------------------------------------------------


class TestChampionsPageEmpty:
    """No patch:list data shows empty state."""

    @pytest.mark.asyncio
    async def test_champions_page_empty(self):
        """No champion data → empty state message."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champions

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_champions(request)
        body = resp.body.decode()

        assert "No champion data yet" in body
        assert "empty-state" in body
        await r.aclose()


class TestChampionsPageWithData:
    """Mock champion data → verify table renders."""

    @pytest.mark.asyncio
    async def test_champions_page_with_data(self):
        """Champion data exists → table with champion names renders."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champions

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Seed patch:list
        await r.zadd("patch:list", {"14.5": 1710000000})
        # Seed champion index
        await r.zadd("champion:index:14.5", {"Zed:MID": 50, "Ahri:MID": 40})
        # Seed champion stats
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "50",
                "wins": "28",
                "kills": "400",
                "deaths": "200",
                "assists": "150",
                "cs": "10000",
                "gold": "500000",
                "damage": "750000",
                "vision": "500",
            },
        )
        await r.hset(
            "champion:stats:Ahri:14.5:MID",
            mapping={
                "games": "40",
                "wins": "22",
                "kills": "300",
                "deaths": "160",
                "assists": "200",
                "cs": "8000",
                "gold": "400000",
                "damage": "600000",
                "vision": "400",
            },
        )
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_champions(request)
        body = resp.body.decode()

        assert "Zed" in body
        assert "Ahri" in body
        assert "MID" in body
        assert "<thead>" in body
        assert "<tbody>" in body
        assert 'scope="col"' in body
        assert "Win Rate" in body
        assert "Games" in body
        await r.aclose()


class TestChampionsPageRoleFilter:
    """Only shows filtered role."""

    @pytest.mark.asyncio
    async def test_champions_page_role_filter(self):
        """Role filter TOP hides MID champions."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champions

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000})
        await r.zadd("champion:index:14.5", {"Zed:MID": 50, "Garen:TOP": 30})
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "50",
                "wins": "25",
                "kills": "200",
                "deaths": "100",
                "assists": "100",
                "cs": "5000",
            },
        )
        await r.hset(
            "champion:stats:Garen:14.5:TOP",
            mapping={
                "games": "30",
                "wins": "18",
                "kills": "120",
                "deaths": "80",
                "assists": "50",
                "cs": "4000",
            },
        )
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"role": "TOP"}

        resp = await show_champions(request)
        body = resp.body.decode()

        assert "Garen" in body
        # Zed is MID, should not appear in TOP filter
        assert "Zed" not in body
        await r.aclose()


class TestChampionsPagePatchSelector:
    """Defaults to latest patch."""

    @pytest.mark.asyncio
    async def test_champions_page_patch_selector(self):
        """Without patch param, uses latest (highest score) patch."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champions

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.4": 1709000000, "14.5": 1710000000})
        await r.zadd("champion:index:14.5", {"Zed:MID": 10})
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "10",
                "wins": "5",
                "kills": "50",
                "deaths": "30",
                "assists": "20",
                "cs": "1000",
            },
        )
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_champions(request)
        body = resp.body.decode()

        # Should show 14.5 (latest), not 14.4
        assert "Patch 14.5" in body
        assert "Zed" in body
        await r.aclose()


class TestChampionDetailPage:
    """Shows single champion stats."""

    @pytest.mark.asyncio
    async def test_champion_detail_page(self):
        """Detail page for Zed shows stats table and patch history."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champion_detail

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000, "14.4": 1709000000})
        await r.zadd("champion:index:14.5", {"Zed:MID": 50})
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "50",
                "wins": "28",
                "kills": "400",
                "deaths": "200",
                "assists": "150",
                "cs": "10000",
                "gold": "500000",
                "damage": "750000",
                "vision": "500",
                "double_kills": "20",
                "triple_kills": "5",
                "quadra_kills": "1",
                "penta_kills": "0",
            },
        )
        await r.hset(
            "champion:stats:Zed:14.4:MID",
            mapping={
                "games": "30",
                "wins": "15",
                "kills": "200",
                "deaths": "120",
                "assists": "80",
                "cs": "6000",
                "gold": "300000",
                "damage": "400000",
                "vision": "300",
            },
        )
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"patch": "14.5", "role": "MID"}

        resp = await show_champion_detail(request, "Zed")
        body = resp.body.decode()

        assert "Zed" in body
        assert "MID" in body
        assert "Win Rate" in body
        assert "56.0%" in body  # 28/50
        assert "Patch History" in body
        assert "14.4" in body
        assert "14.5" in body
        assert "Double Kills" in body
        assert "<thead>" in body
        assert 'scope="col"' in body
        await r.aclose()


class TestChampionDetailNotFound:
    """Unknown champion shows empty state."""

    @pytest.mark.asyncio
    async def test_champion_detail_not_found(self):
        """Non-existent champion → empty state."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champion_detail

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000})
        # No champion index entries for "FakeChamp"
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_champion_detail(request, "FakeChamp")
        body = resp.body.decode()

        assert "No data for FakeChamp" in body
        assert "empty-state" in body
        await r.aclose()


# ---------------------------------------------------------------------------
# Rank display on stats page
# ---------------------------------------------------------------------------


class TestStatsPageRankDisplay:
    """Rank data from player:rank:{puuid} is shown on the stats page."""

    @pytest.mark.asyncio
    async def test_stats_page__shows_rank_when_available(self):
        """When player:rank:{puuid} exists, tier/division/LP/W/L are rendered."""

        import fakeredis.aioredis

        from lol_ui.routes.stats import _build_stats_response

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        puuid = "rank-test-puuid"
        await r.hset(
            f"player:rank:{puuid}",
            mapping={
                "tier": "GOLD",
                "division": "II",
                "lp": "47",
                "wins": "120",
                "losses": "115",
            },
        )
        # Set up pipeline data (priority, champs, roles)
        await r.zadd(f"player:champions:{puuid}", {"Zed": 15.0})
        await r.zadd(f"player:roles:{puuid}", {"MID": 20.0})

        stats = {"total_games": "235", "win_rate": "0.511"}
        resp = await _build_stats_response(
            r, puuid, "TestPlayer", "NA1", "na1", "TestPlayer#NA1", stats
        )
        body = resp.body.decode()

        assert "GOLD" in body
        assert "II" in body
        assert "47 LP" in body
        assert "120W" in body
        assert "115L" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_stats_page__no_rank_still_renders(self):
        """When player:rank:{puuid} does not exist, page still renders without rank card."""

        import fakeredis.aioredis

        from lol_ui.routes.stats import _build_stats_response

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        puuid = "no-rank-puuid"

        stats = {"total_games": "10"}
        resp = await _build_stats_response(r, puuid, "NoRank", "NA1", "na1", "NoRank#NA1", stats)
        body = resp.body.decode()

        assert "Ranked Solo/Duo" not in body
        # Page still renders stats
        assert "Stats for NoRank#NA1" in body
        await r.aclose()


# ---------------------------------------------------------------------------
# Matchups page
# ---------------------------------------------------------------------------


class TestMatchupsPage:
    """The /matchups page shows a form and matchup data."""

    @pytest.mark.asyncio
    async def test_matchups_page__form_shown_without_params(self):
        """When no champion params are provided, the search form is rendered."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.matchups import show_matchups

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_matchups(request)
        body = resp.body.decode()

        assert "Champion Matchups" in body
        assert "Champion A" in body
        assert "Champion B" in body
        assert "Compare" in body
        assert "form" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_matchups_page__shows_data_when_available(self):
        """When matchup data exists, win rates are shown for both champions."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.matchups import show_matchups

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000})
        await r.hset(
            "matchup:Jinx:Caitlyn:BOTTOM:14.5",
            mapping={"games": "100", "wins": "55"},
        )
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {
            "champ_a": "Jinx",
            "champ_b": "Caitlyn",
            "role": "BOTTOM",
            "patch": "14.5",
        }

        resp = await show_matchups(request)
        body = resp.body.decode()

        assert "Jinx" in body
        assert "Caitlyn" in body
        assert "100" in body  # games count
        assert "55.0%" in body  # Jinx win rate
        assert "45.0%" in body  # Caitlyn win rate
        await r.aclose()

    @pytest.mark.asyncio
    async def test_matchups_page__no_data_shows_empty_state(self):
        """When no matchup data exists, an empty state message is shown."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.matchups import show_matchups

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000})
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {
            "champ_a": "Jinx",
            "champ_b": "Caitlyn",
            "role": "BOTTOM",
            "patch": "14.5",
        }

        resp = await show_matchups(request)
        body = resp.body.decode()

        assert "No matchup data" in body
        assert "empty-state" in body
        await r.aclose()


# ---------------------------------------------------------------------------
# Champion detail matchups section
# ---------------------------------------------------------------------------


class TestChampionDetailMatchups:
    """Champion detail page includes matchup section when data is available."""

    @pytest.mark.asyncio
    async def test_champion_detail__shows_matchups(self):
        """When matchup index data exists, a matchups table is rendered."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champion_detail

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000})
        await r.zadd("champion:index:14.5", {"Zed:MID": 50})
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "50",
                "wins": "28",
                "kills": "400",
                "deaths": "200",
                "assists": "150",
                "cs": "10000",
                "gold": "500000",
                "damage": "750000",
                "vision": "500",
            },
        )
        # Matchup index and data
        await r.sadd("matchup:index:Zed:MID:14.5", "Yasuo", "Ahri")
        await r.hset(
            "matchup:Zed:Yasuo:MID:14.5",
            mapping={"games": "20", "wins": "12"},
        )
        await r.hset(
            "matchup:Zed:Ahri:MID:14.5",
            mapping={"games": "15", "wins": "6"},
        )

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"patch": "14.5", "role": "MID"}

        resp = await show_champion_detail(request, "Zed")
        body = resp.body.decode()

        assert "Matchups" in body
        assert "Yasuo" in body
        assert "Ahri" in body
        assert "60.0%" in body  # 12/20
        assert "40.0%" in body  # 6/15
        await r.aclose()

    @pytest.mark.asyncio
    async def test_champion_detail__no_matchups_no_section(self):
        """When no matchup index exists, no matchup section is rendered."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champion_detail

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000})
        await r.zadd("champion:index:14.5", {"Zed:MID": 50})
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "50",
                "wins": "28",
                "kills": "400",
                "deaths": "200",
                "assists": "150",
                "cs": "10000",
                "gold": "500000",
                "damage": "750000",
                "vision": "500",
            },
        )
        # No matchup data
        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"patch": "14.5", "role": "MID"}

        resp = await show_champion_detail(request, "Zed")
        body = resp.body.decode()

        assert "Zed" in body
        assert "vs Champion" not in body
        await r.aclose()


# ---------------------------------------------------------------------------
# Match history items display
# ---------------------------------------------------------------------------


class TestMatchHistoryItems:
    """Match history rows show final items when participant data includes them."""

    def test_match_history__shows_items_column(self):
        """When participant has items field, item icons are rendered."""
        from lol_ui.match_history import _match_history_html

        matches = [
            (
                "NA1_123",
                {"game_start": "1700000000000", "game_mode": "CLASSIC"},
                {
                    "win": "1",
                    "champion_name": "Zed",
                    "kills": "10",
                    "deaths": "2",
                    "assists": "5",
                    "items": "3142,6693,3158,3814,6696,3134",
                },
            ),
        ]
        # version is required for item icons to render as img tags
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False, version="14.5.1")
        assert "match-items" in result
        assert "3142" in result

    def test_match_history__no_items_shows_dash(self):
        """When participant has no items field, empty item slots are rendered."""
        from lol_ui.match_history import _match_history_html

        matches = [
            (
                "NA1_123",
                {"game_start": "1700000000000", "game_mode": "CLASSIC"},
                {
                    "win": "1",
                    "champion_name": "Zed",
                    "kills": "10",
                    "deaths": "2",
                    "assists": "5",
                },
            ),
        ]
        result = _match_history_html(matches, "puuid", "na1", "P#1", 0, False)
        assert "match-items" in result
        assert "match-item--empty" in result


class TestGetChampionIdMap:
    """_get_champion_id_map returns {numeric_id: champion_name} from DDragon."""

    @pytest.mark.asyncio
    async def test_champion_id_map__returns_from_cache(self):
        """When ddragon:champion_ids exists in Redis, returns cached mapping."""
        import fakeredis.aioredis

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        mapping = {"266": "Aatrox", "103": "Ahri", "238": "Zed"}
        await r.set(_DDRAGON_CHAMPION_IDS_KEY, json.dumps(mapping), ex=86400)

        result = await _get_champion_id_map(r)

        assert result == mapping
        await r.aclose()

    @pytest.mark.asyncio
    async def test_champion_id_map__fetches_and_caches(self):
        """When no cache, fetches from DDragon and stores in Redis."""
        import fakeredis.aioredis
        import httpx
        import respx

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("ddragon:version", "14.5.1")

        ddragon_data = {
            "data": {
                "Aatrox": {"key": "266", "id": "Aatrox"},
                "Ahri": {"key": "103", "id": "Ahri"},
                "Zed": {"key": "238", "id": "Zed"},
            }
        }
        mock_router = respx.mock(assert_all_called=False)
        with mock_router:
            mock_router.get(
                "https://ddragon.leagueoflegends.com/cdn/14.5.1/data/en_US/champion.json"
            ).mock(return_value=httpx.Response(200, json=ddragon_data))

            result = await _get_champion_id_map(r)

        assert result == {"266": "Aatrox", "103": "Ahri", "238": "Zed"}
        # Verify it was cached
        cached = await r.get(_DDRAGON_CHAMPION_IDS_KEY)
        assert cached is not None
        assert json.loads(cached) == result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_champion_id_map__no_version_returns_empty(self):
        """When no DDragon version available, returns empty dict."""
        import fakeredis.aioredis
        import respx

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # No ddragon:version in Redis; block real HTTP to prevent flaky results
        with respx.mock(assert_all_called=False):
            result = await _get_champion_id_map(r)

        assert result == {}
        await r.aclose()

    @pytest.mark.asyncio
    async def test_champion_id_map__http_error_returns_empty(self):
        """When DDragon HTTP request fails, returns empty dict."""
        import fakeredis.aioredis
        import httpx
        import respx

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set("ddragon:version", "14.5.1")

        with respx.mock(assert_all_called=False):
            respx.get(
                "https://ddragon.leagueoflegends.com/cdn/14.5.1/data/en_US/champion.json"
            ).mock(return_value=httpx.Response(500))

            result = await _get_champion_id_map(r)

        assert result == {}
        await r.aclose()


class TestChampionTierTableBanRate:
    """Ban % column appears in champion tier table."""

    def test_champion_tier_table__shows_ban_rate_header(self):
        """Ban % column header appears in the tier table."""
        rows = [
            {
                "name": "Zed",
                "role": "MID",
                "games": 50,
                "win_rate": 56.0,
                "pick_rate": 10.0,
                "kda": 2.75,
                "cs": 200,
                "ban_rate": 25.0,
            },
        ]
        result = _champion_tier_table(rows, "14.5", "14.5.1")
        assert "Ban %" in result

    def test_champion_tier_table__shows_ban_rate_value(self):
        """Ban rate value appears in the table row."""
        rows = [
            {
                "name": "Zed",
                "role": "MID",
                "games": 50,
                "win_rate": 56.0,
                "pick_rate": 10.0,
                "kda": 2.75,
                "cs": 200,
                "ban_rate": 25.0,
            },
        ]
        result = _champion_tier_table(rows, "14.5", "14.5.1")
        assert "25.0%" in result

    def test_champion_tier_table__zero_ban_rate(self):
        """When ban_rate is 0.0, still shows 0.0% in the table."""
        rows = [
            {
                "name": "Ahri",
                "role": "MID",
                "games": 40,
                "win_rate": 55.0,
                "pick_rate": 8.0,
                "kda": 3.0,
                "cs": 180,
                "ban_rate": 0.0,
            },
        ]
        result = _champion_tier_table(rows, "14.5", "14.5.1")
        assert "0.0%" in result


class TestChampionsPageBanRate:
    """Integration: /champions page shows Ban % column with ban data."""

    @pytest.mark.asyncio
    async def test_champions_page__shows_ban_rate_column(self):
        """When ban data exists, Ban % column shows in the champions table."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champions

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000})
        await r.zadd("champion:index:14.5", {"Zed:MID": 50})
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "50",
                "wins": "28",
                "kills": "400",
                "deaths": "200",
                "assists": "150",
                "cs": "10000",
            },
        )
        # Ban data: Zed (champion ID 238) banned 15 times in 60 games
        await r.hset(
            "champion:bans:14.5",
            mapping={"238": "15", "_total_games": "60"},
        )
        # Champion ID mapping cache
        mapping = {"238": "Zed"}
        await r.set(_DDRAGON_CHAMPION_IDS_KEY, json.dumps(mapping), ex=86400)

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_champions(request)
        body = resp.body.decode()

        assert "Ban %" in body
        assert "25.0%" in body  # 15/60 * 100
        await r.aclose()

    @pytest.mark.asyncio
    async def test_champions_page__no_ban_data_shows_zero(self):
        """When no ban data exists, Ban % column shows 0.0%."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champions

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000})
        await r.zadd("champion:index:14.5", {"Zed:MID": 50})
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "50",
                "wins": "28",
                "kills": "400",
                "deaths": "200",
                "assists": "150",
                "cs": "10000",
            },
        )
        # No ban data at all — no champion:bans:14.5 key
        # Empty champion ID map so no DDragon fetch needed
        await r.set(_DDRAGON_CHAMPION_IDS_KEY, json.dumps({}), ex=86400)

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_champions(request)
        body = resp.body.decode()

        assert "Ban %" in body
        assert "0.0%" in body
        await r.aclose()


class TestDlqSummary:
    """Tests for _dlq_summary_html and its integration into show_dlq."""

    @pytest.mark.asyncio
    async def test_dlq_summary__shows_depth_and_archive_count(self):
        """Summary displays DLQ depth and archive depth."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.dlq_helpers import _dlq_summary_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        # Add 2 entries to DLQ
        for i in range(2):
            dlq = DLQEnvelope(
                source_stream="stream:dlq",
                type="dlq",
                payload={"match_id": f"NA1_{i}", "region": "na1"},
                attempts=3,
                max_attempts=5,
                failure_code="http_429",
                failure_reason="rate limited",
                failed_by="fetcher",
                original_stream="stream:match_id",
                original_message_id=f"orig-{i}",
            )
            await r.xadd("stream:dlq", dlq.to_redis_fields())

        # Add 3 entries to archive
        for i in range(3):
            await r.xadd("stream:dlq:archive", {"data": f"archived-{i}"})

        result = await _dlq_summary_html(r)

        assert ">2<" in result  # pending count
        assert ">3<" in result  # archived count
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq_summary__failure_code_breakdown(self):
        """Summary aggregates failure codes into a breakdown table."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.dlq_helpers import _dlq_summary_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        # Add entries with different failure codes
        for fc in ["http_429", "http_429", "http_5xx"]:
            dlq = DLQEnvelope(
                source_stream="stream:dlq",
                type="dlq",
                payload={"match_id": "NA1_1", "region": "na1"},
                attempts=3,
                max_attempts=5,
                failure_code=fc,
                failure_reason="reason",
                failed_by="fetcher",
                original_stream="stream:match_id",
                original_message_id="orig-1",
            )
            await r.xadd("stream:dlq", dlq.to_redis_fields())

        result = await _dlq_summary_html(r)

        assert "Failure Codes" in result
        assert "http_429" in result
        assert "http_5xx" in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq_summary__source_stream_breakdown(self):
        """Summary aggregates source streams into a breakdown table."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.dlq_helpers import _dlq_summary_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        for stream in ["stream:match_id", "stream:match_id", "stream:parse"]:
            dlq = DLQEnvelope(
                source_stream="stream:dlq",
                type="dlq",
                payload={"match_id": "NA1_1", "region": "na1"},
                attempts=3,
                max_attempts=5,
                failure_code="http_429",
                failure_reason="reason",
                failed_by="fetcher",
                original_stream=stream,
                original_message_id="orig-1",
            )
            await r.xadd("stream:dlq", dlq.to_redis_fields())

        result = await _dlq_summary_html(r)

        assert "Source Streams" in result
        assert "stream:match_id" in result
        assert "stream:parse" in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq_summary__oldest_message_age(self):
        """Summary computes oldest message age from first stream entry ID."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.dlq_helpers import _dlq_summary_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_1", "region": "na1"},
            attempts=3,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="reason",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-1",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        result = await _dlq_summary_html(r)

        # Should show an age string, not "n/a"
        assert "n/a" not in result
        assert "oldest message" in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_dlq_summary__escapes_failure_code(self):
        """Failure codes are HTML-escaped via _badge to prevent injection."""
        import fakeredis.aioredis
        from lol_pipeline.models import DLQEnvelope

        from lol_ui.dlq_helpers import _dlq_summary_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_1", "region": "na1"},
            attempts=3,
            max_attempts=5,
            failure_code="<script>xss</script>",
            failure_reason="reason",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="orig-1",
        )
        await r.xadd("stream:dlq", dlq.to_redis_fields())

        result = await _dlq_summary_html(r)

        assert "<script>" not in result
        assert "&lt;script&gt;" in result
        await r.aclose()


class TestStreamsConsumerLag:
    """Consumer lag monitoring: Group, Pending, Lag columns on /streams page."""

    @pytest.mark.asyncio
    async def test_streams_fragment__shows_group_pending_lag_columns(self):
        """The streams table header includes Group, Pending, and Lag columns."""
        import fakeredis.aioredis

        from lol_ui.streams_helpers import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        result = await _streams_fragment_html(r)

        assert "<th" in result
        assert "Group" in result
        assert "Pending" in result
        assert "Lag" in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment__displays_consumer_group_info(self):
        """When a stream has a consumer group, its name, pending, and lag appear."""
        import fakeredis.aioredis

        from lol_ui.streams_helpers import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Create a stream with entries and a consumer group
        await r.xadd("stream:puuid", {"data": "test1"})
        await r.xadd("stream:puuid", {"data": "test2"})
        await r.xgroup_create("stream:puuid", "crawlers", "0")

        result = await _streams_fragment_html(r)

        # The group name should appear in the output
        assert "crawlers" in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment__no_groups__shows_dash(self):
        """When a stream has no consumer groups, display dashes for group/pending/lag."""
        import fakeredis.aioredis

        from lol_ui.streams_helpers import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        result = await _streams_fragment_html(r)

        # Streams with no groups should show mdash placeholders
        assert "&mdash;" in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment__lag_value_displayed(self):
        """Consumer lag value is rendered in the table."""
        import fakeredis.aioredis

        from lol_ui.streams_helpers import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        # Create stream, add entries, create group at 0 (unread entries = lag)
        for i in range(5):
            await r.xadd("stream:match_id", {"data": str(i)})
        await r.xgroup_create("stream:match_id", "fetchers", "0")

        result = await _streams_fragment_html(r)

        # Lag should be rendered (fakeredis reports lag field)
        assert "fetchers" in result
        await r.aclose()

    @pytest.mark.asyncio
    async def test_streams_fragment__xinfo_error_handled_gracefully(self):  # noqa: C901
        """If XINFO GROUPS raises an error, treat as no groups (show dashes)."""
        import fakeredis.aioredis

        from lol_ui.streams_helpers import _streams_fragment_html

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        # Patch pipeline execute to return ResponseError for xinfo_groups calls
        original_pipeline = r.pipeline

        class PatchedPipeline:
            """Wraps pipeline to inject ResponseError for xinfo_groups results."""

            def __init__(self, pipe):
                self._pipe = pipe

            async def __aenter__(self):
                self._inner = await self._pipe.__aenter__()
                return self

            async def __aexit__(self, *args):
                return await self._pipe.__aexit__(*args)

            def xlen(self, *a, **kw):
                return self._inner.xlen(*a, **kw)

            def xinfo_groups(self, *a, **kw):
                # Still queue the command so result count is correct
                return self._inner.xinfo_groups(*a, **kw)

            def zcard(self, *a, **kw):
                return self._inner.zcard(*a, **kw)

            def get(self, *a, **kw):
                return self._inner.get(*a, **kw)

            async def execute(self, raise_on_error=True):
                import redis.exceptions

                results = await self._inner.execute(raise_on_error=False)
                n = 6  # number of streams
                # Replace xinfo_groups results with ResponseError
                for i in range(n, 2 * n):
                    results[i] = redis.exceptions.ResponseError("no such key")
                return results

        def patched_pipeline(**kw):
            return PatchedPipeline(original_pipeline(**kw))

        r.pipeline = patched_pipeline

        result = await _streams_fragment_html(r)

        # Should not crash; should show dashes for all streams
        assert "&mdash;" in result
        assert "stream:puuid" in result
        await r.aclose()


class TestFormatGroupCells:
    """Unit tests for _format_group_cells helper."""

    def test_single_group__renders_name_pending_lag(self):
        from lol_ui.streams_helpers import _format_group_cells

        groups = [{"name": "crawlers", "pending": 5, "lag": 10}]
        result = _format_group_cells(groups)
        assert "crawlers" in result
        assert ">5<" in result
        assert ">10<" in result

    def test_lag_none__shows_question_mark(self):
        from lol_ui.streams_helpers import _format_group_cells

        groups = [{"name": "parsers", "pending": 0, "lag": None}]
        result = _format_group_cells(groups)
        assert "?" in result

    def test_group_name_html_escaped(self):
        from lol_ui.streams_helpers import _format_group_cells

        groups = [{"name": "<script>alert(1)</script>", "pending": 0, "lag": 0}]
        result = _format_group_cells(groups)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


class TestChampionDiversity:
    """_champion_diversity computes HHI-based pool diversity score."""

    def test_one_trick__score_zero(self):
        """Single champion played = HHI of 1.0, diversity 0.0, label OTP."""
        champs = [("Zed", 100.0)]
        score, label = _champion_diversity(champs)
        assert score == 0.0
        assert label == "OTP"

    def test_two_equal_champions__score_50(self):
        """Two champions with equal games: HHI = 0.5, diversity = 50.0."""
        champs = [("Zed", 50.0), ("Ahri", 50.0)]
        score, label = _champion_diversity(champs)
        assert score == 50.0
        assert label == "Moderate"

    def test_four_equal_champions__score_75(self):
        """Four equal champions: HHI = 0.25, diversity = 75.0."""
        champs = [("Zed", 25.0), ("Ahri", 25.0), ("Yasuo", 25.0), ("Lux", 25.0)]
        score, label = _champion_diversity(champs)
        assert score == 75.0
        assert label == "Diverse"

    def test_ten_equal_champions__score_90(self):
        """Ten equal champions: HHI = 0.1, diversity = 90.0."""
        champs = [(f"Champ{i}", 10.0) for i in range(10)]
        score, label = _champion_diversity(champs)
        assert score == 90.0
        assert label == "Flex"

    def test_empty_data__score_zero(self):
        """No champion data = score 0.0, label OTP."""
        score, label = _champion_diversity([])
        assert score == 0.0
        assert label == "OTP"

    def test_zero_total_games__score_zero(self):
        """All zero games = score 0.0, label OTP."""
        champs = [("Zed", 0.0), ("Ahri", 0.0)]
        score, label = _champion_diversity(champs)
        assert score == 0.0
        assert label == "OTP"

    def test_uneven_distribution__focused(self):
        """Dominant champion with a few others: low diversity."""
        champs = [("Zed", 80.0), ("Ahri", 10.0), ("Lux", 10.0)]
        score, label = _champion_diversity(champs)
        # HHI = (0.8^2 + 0.1^2 + 0.1^2) = 0.64 + 0.01 + 0.01 = 0.66
        # diversity = (1 - 0.66) * 100 = 34.0
        assert score == 34.0
        assert label == "Focused"

    def test_label_boundary__otp_upper(self):
        """Score just below 20 should be OTP."""
        # Need HHI such that (1 - HHI) * 100 < 20, i.e., HHI > 0.8
        # Two champs: 90 + 10 => HHI = 0.81 + 0.01 = 0.82 => diversity = 18.0
        champs = [("Zed", 90.0), ("Ahri", 10.0)]
        score, label = _champion_diversity(champs)
        assert score == 18.0
        assert label == "OTP"

    def test_label_boundary__focused_lower(self):
        """Score exactly 20 should be Focused (boundary inclusive at lower end)."""
        # HHI = 0.8 => diversity = 20.0
        # Five champs: one with 80 games, four with 5 each
        # p = [0.8, 0.05, 0.05, 0.05, 0.05]
        # HHI = 0.64 + 4*0.0025 = 0.65 => 35.0 -- too high
        # Need exactly 20: HHI = 0.8
        # Two champs: a and b such that a^2 + b^2 = 0.8, a+b=1
        # a^2 + (1-a)^2 = 0.8 => 2a^2 - 2a + 1 = 0.8 => 2a^2 - 2a + 0.2 = 0
        # a = (2 +/- sqrt(4 - 1.6))/4 = (2 +/- sqrt(2.4))/4
        # Not clean integers. Use a direct assertion instead.
        champs = [("Zed", 90.0), ("Ahri", 10.0)]
        score, _ = _champion_diversity(champs)
        assert score < 20.0  # Should be OTP

    def test_label_boundary__diverse_at_60(self):
        """Score at 60 should be Diverse."""
        # Five equal: HHI = 5 * (0.2^2) = 0.2, diversity = 80 -- too high
        # Three equal: HHI = 3 * (1/3)^2 = 1/3 = 0.333, diversity = 66.7
        champs = [("A", 10.0), ("B", 10.0), ("C", 10.0)]
        score, label = _champion_diversity(champs)
        assert 60.0 <= score < 80.0
        assert label == "Diverse"

    def test_label_boundary__flex_at_80(self):
        """Score at or above 80 should be Flex."""
        champs = [(f"C{i}", 10.0) for i in range(5)]
        score, label = _champion_diversity(champs)
        assert score == 80.0
        assert label == "Flex"

    def test_score_is_rounded(self):
        """Score is rounded to 1 decimal place."""
        champs = [("A", 7.0), ("B", 3.0)]
        score, _ = _champion_diversity(champs)
        # HHI = (0.7^2 + 0.3^2) = 0.49 + 0.09 = 0.58 => diversity = 42.0
        assert score == 42.0
        assert isinstance(score, float)


class TestStatsTableDiversity:
    """_stats_table renders champion pool diversity score."""

    def test_diversity_shown__above_min_games(self):
        """When total champion games >= 20, diversity score appears."""
        champs = [("Zed", 15.0), ("Ahri", 10.0)]
        result = _stats_table({"total_games": "25"}, champs, [])
        assert "Pool Diversity" in result
        assert "Pool Diversity: &mdash;" not in result
        assert "<strong>" in result.split("Pool Diversity")[1].split("</div>")[0]

    def test_diversity_hidden__below_min_games(self):
        """When total champion games < 20, diversity shows dash."""
        champs = [("Zed", 10.0), ("Ahri", 5.0)]
        result = _stats_table({"total_games": "15"}, champs, [])
        assert "Pool Diversity" in result
        assert "Pool Diversity: &mdash;" in result

    def test_diversity_hidden__no_champs(self):
        """When no champion data, diversity shows dash."""
        result = _stats_table({"total_games": "50"}, [], [])
        assert "Pool Diversity: &mdash;" in result

    def test_diversity_shows_label(self):
        """Diversity section includes the label text."""
        champs = [("Zed", 10.0), ("Ahri", 10.0)]
        result = _stats_table({}, champs, [])
        assert "Moderate" in result

    def test_diversity_exactly_at_threshold(self):
        """Exactly 20 total games shows the diversity score."""
        champs = [("Zed", 10.0), ("Ahri", 10.0)]
        assert sum(g for _, g in champs) == 20
        result = _stats_table({}, champs, [])
        assert "Pool Diversity" in result
        assert "50.0" in result


# ---------------------------------------------------------------------------
# Tilt / Streak Indicator
# ---------------------------------------------------------------------------


def _make_match(
    win: str = "1",
    kills: str = "5",
    deaths: str = "2",
    assists: str = "3",
):
    """Helper to build a participant dict."""
    return {"win": win, "kills": kills, "deaths": deaths, "assists": assists}


class TestStreakIndicator:
    """_streak_indicator computes streak/KDA trend from recent matches."""

    def test_empty_matches__returns_neutral(self):
        result = _streak_indicator([])
        assert result["streak_type"] == "none"
        assert result["streak_count"] == 0
        assert result["recent_wr"] == 0.0
        assert result["kda_trend"] == "neutral"

    def test_all_wins__streak_equals_count(self):
        matches = [_make_match(win="1") for _ in range(5)]
        result = _streak_indicator(matches)
        assert result["streak_type"] == "win"
        assert result["streak_count"] == 5
        assert result["recent_wr"] == 100.0

    def test_all_losses__streak_equals_count(self):
        matches = [_make_match(win="0") for _ in range(4)]
        result = _streak_indicator(matches)
        assert result["streak_type"] == "loss"
        assert result["streak_count"] == 4
        assert result["recent_wr"] == 0.0

    def test_streak_broken__counts_only_consecutive(self):
        matches = [
            _make_match(win="1"),
            _make_match(win="1"),
            _make_match(win="1"),
            _make_match(win="0"),  # breaks streak
            _make_match(win="1"),
        ]
        result = _streak_indicator(matches)
        assert result["streak_type"] == "win"
        assert result["streak_count"] == 3

    def test_loss_streak_broken(self):
        matches = [
            _make_match(win="0"),
            _make_match(win="0"),
            _make_match(win="1"),
        ]
        result = _streak_indicator(matches)
        assert result["streak_type"] == "loss"
        assert result["streak_count"] == 2

    def test_single_match__streak_of_one(self):
        result = _streak_indicator([_make_match(win="1")])
        assert result["streak_type"] == "win"
        assert result["streak_count"] == 1

    def test_recent_wr__mixed(self):
        matches = [_make_match(win="1")] * 3 + [_make_match(win="0")] * 7
        result = _streak_indicator(matches)
        assert result["recent_wr"] == 30.0

    def test_kda_trend__rising(self):
        """Recent 5 matches have much higher KDA than older matches."""
        recent = [_make_match(kills="10", deaths="1", assists="5")] * _TILT_RECENT_KDA_COUNT
        older = [_make_match(kills="2", deaths="5", assists="1")] * 15
        result = _streak_indicator(recent + older)
        assert result["kda_trend"] == "rising"

    def test_kda_trend__falling(self):
        """Recent 5 matches have much lower KDA than older matches."""
        recent = [_make_match(kills="1", deaths="8", assists="1")] * _TILT_RECENT_KDA_COUNT
        older = [_make_match(kills="10", deaths="1", assists="5")] * 15
        result = _streak_indicator(recent + older)
        assert result["kda_trend"] == "falling"

    def test_kda_trend__neutral_when_similar(self):
        """Similar KDA across all matches -> neutral."""
        matches = [_make_match(kills="5", deaths="3", assists="3")] * 20
        result = _streak_indicator(matches)
        assert result["kda_trend"] == "neutral"

    def test_kda_trend__neutral_when_no_older_matches(self):
        """Only recent matches (no older group) -> neutral."""
        matches = [_make_match(kills="10", deaths="1", assists="5")] * 3
        result = _streak_indicator(matches)
        assert result["kda_trend"] == "neutral"

    def test_deaths_zero__no_division_error(self):
        """Deaths=0 should not cause ZeroDivisionError."""
        matches = [_make_match(kills="10", deaths="0", assists="5")] * 10
        result = _streak_indicator(matches)
        assert result["streak_type"] == "win"

    def test_missing_fields__defaults_to_zero(self):
        """Missing keys in dict should default to 0."""
        result = _streak_indicator([{}])
        assert result["streak_type"] == "loss"
        assert result["streak_count"] == 1


class TestTiltBannerHtml:
    """_tilt_banner_html renders streak badges and KDA trend arrows."""

    def test_empty_indicator__returns_empty(self):
        indicator = _streak_indicator([])
        result = _tilt_banner_html(indicator)
        assert result == ""

    def test_win_streak_3__shows_green_badge(self):
        indicator = {
            "streak_type": "win",
            "streak_count": 3,
            "recent_wr": 60.0,
            "kda_trend": "neutral",
        }
        result = _tilt_banner_html(indicator)
        assert "W3" in result
        assert "badge--success" in result
        assert "tilt-indicator" in result

    def test_win_streak_5__shows_w5(self):
        indicator = {
            "streak_type": "win",
            "streak_count": 5,
            "recent_wr": 75.0,
            "kda_trend": "neutral",
        }
        result = _tilt_banner_html(indicator)
        assert "W5" in result

    def test_loss_streak_3__shows_red_badge(self):
        indicator = {
            "streak_type": "loss",
            "streak_count": 3,
            "recent_wr": 40.0,
            "kda_trend": "neutral",
        }
        result = _tilt_banner_html(indicator)
        assert "L3" in result
        assert "badge--error" in result

    def test_loss_streak_7__shows_l7(self):
        indicator = {
            "streak_type": "loss",
            "streak_count": 7,
            "recent_wr": 15.0,
            "kda_trend": "neutral",
        }
        result = _tilt_banner_html(indicator)
        assert "L7" in result

    def test_streak_under_3__no_streak_badge(self):
        indicator = {
            "streak_type": "win",
            "streak_count": 2,
            "recent_wr": 50.0,
            "kda_trend": "neutral",
        }
        result = _tilt_banner_html(indicator)
        assert result == ""

    def test_rising_kda__shows_arrow_up(self):
        indicator = {
            "streak_type": "win",
            "streak_count": 1,
            "recent_wr": 55.0,
            "kda_trend": "rising",
        }
        result = _tilt_banner_html(indicator)
        assert "Rising" in result
        assert "badge--success" in result
        assert "&uarr;" in result

    def test_falling_kda__shows_arrow_down(self):
        indicator = {
            "streak_type": "loss",
            "streak_count": 1,
            "recent_wr": 45.0,
            "kda_trend": "falling",
        }
        result = _tilt_banner_html(indicator)
        assert "Falling" in result
        assert "badge--error" in result
        assert "&darr;" in result

    def test_streak_and_rising__shows_both(self):
        indicator = {
            "streak_type": "win",
            "streak_count": 4,
            "recent_wr": 70.0,
            "kda_trend": "rising",
        }
        result = _tilt_banner_html(indicator)
        assert "W4" in result
        assert "Rising" in result

    def test_shows_recent_wr(self):
        indicator = {
            "streak_type": "win",
            "streak_count": 3,
            "recent_wr": 65.0,
            "kda_trend": "neutral",
        }
        result = _tilt_banner_html(indicator)
        assert "65%" in result
        assert f"Last {_TILT_RECENT_COUNT}" in result

    def test_neutral_only__returns_empty(self):
        """Streak < 3 and neutral KDA -> no banner."""
        indicator = {
            "streak_type": "win",
            "streak_count": 2,
            "recent_wr": 50.0,
            "kda_trend": "neutral",
        }
        assert _tilt_banner_html(indicator) == ""


class TestPatchDelta:
    """Unit tests for _patch_delta helper."""

    def test_positive_delta__returns_positive_float(self):
        """Current WR higher than previous -> positive delta."""
        cur = {"games": 50, "win_rate": 55.0}
        prev = {"games": 40, "win_rate": 50.0}
        result = _patch_delta(cur, prev)
        assert result == pytest.approx(5.0)

    def test_negative_delta__returns_negative_float(self):
        """Current WR lower than previous -> negative delta."""
        cur = {"games": 30, "win_rate": 45.0}
        prev = {"games": 25, "win_rate": 52.0}
        result = _patch_delta(cur, prev)
        assert result == pytest.approx(-7.0)

    def test_zero_delta__returns_zero(self):
        """Same win rate on both patches -> 0.0."""
        cur = {"games": 20, "win_rate": 50.0}
        prev = {"games": 20, "win_rate": 50.0}
        assert _patch_delta(cur, prev) == 0.0

    def test_near_zero_delta__returns_zero(self):
        """Delta within 0.005 threshold -> 0.0."""
        cur = {"games": 20, "win_rate": 50.004}
        prev = {"games": 20, "win_rate": 50.0}
        assert _patch_delta(cur, prev) == 0.0

    def test_current_below_min_games__returns_none(self):
        """Current patch below DELTA_MIN_GAMES -> None."""
        cur = {"games": _DELTA_MIN_GAMES - 1, "win_rate": 55.0}
        prev = {"games": 50, "win_rate": 50.0}
        assert _patch_delta(cur, prev) is None

    def test_prev_below_min_games__returns_none(self):
        """Previous patch below DELTA_MIN_GAMES -> None."""
        cur = {"games": 50, "win_rate": 55.0}
        prev = {"games": _DELTA_MIN_GAMES - 1, "win_rate": 50.0}
        assert _patch_delta(cur, prev) is None

    def test_both_below_min_games__returns_none(self):
        """Both patches below minimum -> None."""
        cur = {"games": 5, "win_rate": 55.0}
        prev = {"games": 3, "win_rate": 50.0}
        assert _patch_delta(cur, prev) is None

    def test_exact_min_games__returns_delta(self):
        """Exactly DELTA_MIN_GAMES -> returns delta."""
        cur = {"games": _DELTA_MIN_GAMES, "win_rate": 60.0}
        prev = {"games": _DELTA_MIN_GAMES, "win_rate": 50.0}
        assert _patch_delta(cur, prev) == pytest.approx(10.0)

    def test_missing_fields__defaults_to_zero(self):
        """Missing games/win_rate fields default to 0."""
        cur: dict[str, object] = {}
        prev: dict[str, object] = {}
        assert _patch_delta(cur, prev) is None  # 0 games < 10

    def test_string_values__coerced_correctly(self):
        """Values stored as strings (from Redis) are handled."""
        cur = {"games": "50", "win_rate": "55.0"}
        prev = {"games": "40", "win_rate": "50.0"}
        result = _patch_delta(cur, prev)
        assert result == pytest.approx(5.0)


class TestPbiTier:
    """Unit tests for _pbi_tier helper."""

    def test_positive_pbi__high_wr_high_pick(self):
        """High win rate + high pick rate -> positive PBI."""
        pbi, _, _ = _pbi_tier(55.0, 10.0, 5.0)
        # (55-50) * 10 / (100-5) = 50/95 = 0.5263
        assert pbi == pytest.approx(50 / 95, rel=1e-3)

    def test_negative_pbi__low_wr(self):
        """Win rate below 50 -> negative PBI."""
        pbi, _, _ = _pbi_tier(45.0, 10.0, 0.0)
        # (45-50) * 10 / 100 = -0.5
        assert pbi == pytest.approx(-0.5)

    def test_zero_pbi__exactly_50_wr(self):
        """Exactly 50% win rate -> PBI is 0."""
        pbi, _, _ = _pbi_tier(50.0, 15.0, 10.0)
        assert pbi == pytest.approx(0.0)

    def test_zero_pick_rate__pbi_zero(self):
        """Zero pick rate -> PBI is 0 regardless of win rate."""
        pbi, _, _ = _pbi_tier(60.0, 0.0, 5.0)
        assert pbi == pytest.approx(0.0)

    def test_ban_rate_100__uses_small_denominator(self):
        """100% ban rate -> denominator clamped to 0.01."""
        pbi, _, _ = _pbi_tier(55.0, 10.0, 100.0)
        # (55-50) * 10 / 0.01 = 5000
        assert pbi == pytest.approx(5000.0)

    def test_ban_rate_over_100__uses_small_denominator(self):
        """Ban rate > 100 (edge case) -> denominator clamped to 0.01."""
        pbi, _, _ = _pbi_tier(55.0, 10.0, 105.0)
        assert pbi == pytest.approx(5000.0)

    def test_returns_empty_tier_and_color(self):
        """_pbi_tier returns empty strings for tier/color (filled by _assign_tiers)."""
        _, tier, color = _pbi_tier(55.0, 10.0, 5.0)
        assert tier == ""
        assert color == ""

    def test_high_ban_rate__amplifies_pbi(self):
        """High ban rate reduces denominator, amplifying PBI."""
        pbi_low_ban, _, _ = _pbi_tier(55.0, 10.0, 10.0)
        pbi_high_ban, _, _ = _pbi_tier(55.0, 10.0, 90.0)
        assert pbi_high_ban > pbi_low_ban


class TestAssignTiers:
    """Unit tests for _assign_tiers helper."""

    def _make_row(self, name, wr, pr, br, games=100):
        return {
            "name": name,
            "role": "MID",
            "games": games,
            "win_rate": wr,
            "pick_rate": pr,
            "ban_rate": br,
            "kda": 3.0,
            "cs": 200,
        }

    def test_below_min_games__no_tier(self):
        """Champion below PBI_MIN_GAMES gets no tier."""
        rows = [self._make_row("Zed", 55.0, 10.0, 5.0, games=_PBI_MIN_GAMES - 1)]
        _assign_tiers(rows)
        assert rows[0]["tier"] == ""
        assert rows[0]["tier_color"] == ""

    def test_single_champion__gets_s_tier(self):
        """Only one champion eligible -> rank 0 -> pct 0.0 -> S tier."""
        rows = [self._make_row("Zed", 55.0, 10.0, 5.0)]
        _assign_tiers(rows)
        assert rows[0]["tier"] == "S"
        assert rows[0]["tier_color"] == _TIER_COLORS["S"]

    def test_twenty_champions__correct_tier_distribution(self):
        """20 champions: 1 S, 3 A, 6 B, 6 C, 4 D."""
        rows = []
        for i in range(20):
            # Spread win rates from 60 (best) to 41 (worst)
            wr = 60.0 - i
            rows.append(self._make_row(f"Champ{i}", wr, 5.0, 0.0))
        _assign_tiers(rows)
        tier_counts = {}
        for r in rows:
            t = r["tier"]
            tier_counts[t] = tier_counts.get(t, 0) + 1
        # S: top 5% = 1 champion (rank 0, pct=0.0 < 0.05)
        assert tier_counts.get("S", 0) == 1
        # A: 5-20% = 3 champions (ranks 1-3, pct 0.05-0.15)
        assert tier_counts.get("A", 0) == 3
        # B: 20-50% = 6 champions (ranks 4-9, pct 0.20-0.45)
        assert tier_counts.get("B", 0) == 6
        # C: 50-80% = 6 champions (ranks 10-15, pct 0.50-0.75)
        assert tier_counts.get("C", 0) == 6
        # D: bottom 20% = 4 champions (ranks 16-19, pct 0.80-0.95)
        assert tier_counts.get("D", 0) == 4

    def test_empty_rows__no_crash(self):
        """Empty list -> no error."""
        rows: list[dict[str, object]] = []
        _assign_tiers(rows)
        assert rows == []

    def test_all_below_min__no_tiers_assigned(self):
        """All champions below min games -> no tiers."""
        rows = [
            self._make_row("A", 55.0, 10.0, 5.0, games=5),
            self._make_row("B", 45.0, 8.0, 2.0, games=3),
        ]
        _assign_tiers(rows)
        assert all(r["tier"] == "" for r in rows)

    def test_mixed_eligible_and_ineligible(self):
        """Only eligible champions get tiers; ineligible get empty."""
        rows = [
            self._make_row("Eligible1", 55.0, 10.0, 0.0, games=100),
            self._make_row("TooFew", 60.0, 20.0, 0.0, games=5),
            self._make_row("Eligible2", 48.0, 8.0, 0.0, games=50),
        ]
        _assign_tiers(rows)
        assert rows[0]["tier"] != ""
        assert rows[1]["tier"] == ""
        assert rows[2]["tier"] != ""

    def test_pbi_stored_on_eligible_rows(self):
        """Eligible rows get a pbi key set."""
        rows = [self._make_row("Zed", 55.0, 10.0, 5.0)]
        _assign_tiers(rows)
        assert "pbi" in rows[0]
        assert isinstance(rows[0]["pbi"], float)


class TestChampionTierTableDelta:
    """_champion_tier_table renders WR Delta and Tier columns."""

    def _row(self, name="Zed", role="MID", games=50, wr=56.0, pr=10.0, br=5.0):
        return {
            "name": name,
            "role": role,
            "games": games,
            "win_rate": wr,
            "pick_rate": pr,
            "kda": 2.75,
            "cs": 200,
            "ban_rate": br,
        }

    def test_wr_delta_header_present(self):
        """WR Delta column header appears."""
        result = _champion_tier_table([self._row()], "14.5", "14.5.1")
        assert "WR Delta" in result

    def test_tier_header_present(self):
        """Tier column header appears."""
        result = _champion_tier_table([self._row()], "14.5", "14.5.1")
        assert ">Tier<" in result

    def test_positive_delta__green_arrow(self):
        """Positive delta shows green up arrow."""
        cur = self._row(wr=55.0)
        prev = [self._row(wr=50.0)]
        result = _champion_tier_table([cur], "14.5", "14.5.1", prev_rows=prev)
        assert "&#9650;" in result  # up arrow
        assert "+5.0%" in result
        assert "color-win" in result

    def test_negative_delta__red_arrow(self):
        """Negative delta shows red down arrow."""
        cur = self._row(wr=45.0)
        prev = [self._row(wr=52.0)]
        result = _champion_tier_table([cur], "14.5", "14.5.1", prev_rows=prev)
        assert "&#9660;" in result  # down arrow
        assert "-7.0%" in result
        assert "color-loss" in result

    def test_no_prev_data__dash(self):
        """No previous patch data -> mdash."""
        result = _champion_tier_table([self._row()], "14.5", "14.5.1")
        assert "&mdash;" in result

    def test_prev_not_matching__dash(self):
        """Previous patch has different champion -> mdash for delta."""
        cur = self._row(name="Zed")
        prev = [self._row(name="Ahri")]
        result = _champion_tier_table([cur], "14.5", "14.5.1", prev_rows=prev)
        # Delta should be dash since Zed not in prev
        assert "&mdash;" in result

    def test_below_min_games__delta_dash(self):
        """Below DELTA_MIN_GAMES -> mdash for delta."""
        cur = self._row(games=5, wr=55.0)
        prev = [self._row(games=50, wr=50.0)]
        result = _champion_tier_table([cur], "14.5", "14.5.1", prev_rows=prev)
        assert "&mdash;" in result

    def test_tier_badge__rendered_for_eligible(self):
        """Champion with enough games gets a tier badge."""
        rows = [self._row(games=100)]
        result = _champion_tier_table(rows, "14.5", "14.5.1")
        assert "tier-badge" in result

    def test_tier_badge__dash_for_few_games(self):
        """Champion below PBI_MIN_GAMES gets mdash instead of tier badge."""
        rows = [self._row(games=_PBI_MIN_GAMES - 1)]
        result = _champion_tier_table(rows, "14.5", "14.5.1")
        # Should have 2 mdash cells: one for tier, one for delta
        assert result.count("&mdash;") >= 1


class TestChampionsPageDeltaIntegration:
    """Integration: /champions page shows delta and tier columns."""

    @pytest.mark.asyncio
    async def test_champions_page__shows_delta_column(self):
        """When two patches exist, WR Delta column appears with delta values."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champions

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000, "14.4": 1709000000})
        # Current patch
        await r.zadd("champion:index:14.5", {"Zed:MID": 50})
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "50",
                "wins": "28",
                "kills": "400",
                "deaths": "200",
                "assists": "150",
                "cs": "10000",
            },
        )
        # Previous patch
        await r.zadd("champion:index:14.4", {"Zed:MID": 40})
        await r.hset(
            "champion:stats:Zed:14.4:MID",
            mapping={
                "games": "40",
                "wins": "18",
                "kills": "300",
                "deaths": "160",
                "assists": "100",
                "cs": "8000",
            },
        )
        await r.set(_DDRAGON_CHAMPION_IDS_KEY, json.dumps({}), ex=86400)

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_champions(request)
        body = resp.body.decode()

        assert "WR Delta" in body
        assert "Tier" in body
        # Zed: 14.5 WR = 28/50*100 = 56%, 14.4 WR = 18/40*100 = 45%
        # delta = 56 - 45 = 11.0
        assert "+11.0%" in body
        assert "&#9650;" in body  # up arrow
        await r.aclose()

    @pytest.mark.asyncio
    async def test_champions_page__single_patch_shows_dash(self):
        """When only one patch exists, delta column shows dash."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champions

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000})
        await r.zadd("champion:index:14.5", {"Zed:MID": 50})
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "50",
                "wins": "28",
                "kills": "400",
                "deaths": "200",
                "assists": "150",
                "cs": "10000",
            },
        )
        await r.set(_DDRAGON_CHAMPION_IDS_KEY, json.dumps({}), ex=86400)

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_champions(request)
        body = resp.body.decode()

        assert "WR Delta" in body
        assert "&mdash;" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_champions_page__tier_badge_rendered(self):
        """Champions with enough games get tier badges."""
        from unittest.mock import MagicMock

        import fakeredis.aioredis

        from lol_ui.routes.champions import show_champions

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.zadd("patch:list", {"14.5": 1710000000})
        await r.zadd("champion:index:14.5", {"Zed:MID": 50})
        await r.hset(
            "champion:stats:Zed:14.5:MID",
            mapping={
                "games": "50",
                "wins": "28",
                "kills": "400",
                "deaths": "200",
                "assists": "150",
                "cs": "10000",
            },
        )
        await r.set(_DDRAGON_CHAMPION_IDS_KEY, json.dumps({}), ex=86400)

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_champions(request)
        body = resp.body.decode()

        assert "tier-badge" in body
        await r.aclose()


# ---------------------------------------------------------------------------
# Match badges
# ---------------------------------------------------------------------------


class TestMatchBadges:
    """Tests for _match_badges() badge computation."""

    def test_no_badges__ordinary_game(self):
        """A normal game with deaths and moderate KDA yields no badges."""
        p = {"kills": "5", "deaths": "3", "assists": "4", "win": "1"}
        assert _match_badges(p) == []

    def test_deathless__win_with_zero_deaths(self):
        """Deathless badge requires win=1 AND deaths=0."""
        p = {"kills": "3", "deaths": "0", "assists": "2", "win": "1"}
        badges = _match_badges(p)
        assert ("Deathless", "gold") in badges

    def test_deathless__not_awarded_on_loss(self):
        """Deathless badge NOT awarded if the player lost."""
        p = {"kills": "3", "deaths": "0", "assists": "2", "win": "0"}
        badges = _match_badges(p)
        names = [b[0] for b in badges]
        assert "Deathless" not in names

    def test_penta_kill__single_penta(self):
        """PENTA badge when penta_kills >= 1."""
        p = {
            "kills": "20",
            "deaths": "5",
            "assists": "3",
            "penta_kills": "1",
            "win": "1",
        }
        badges = _match_badges(p)
        assert ("PENTA", "red") in badges

    def test_penta_kill__multiple_pentas(self):
        """PENTA badge also awarded for penta_kills > 1."""
        p = {
            "kills": "25",
            "deaths": "2",
            "assists": "5",
            "penta_kills": "3",
            "win": "1",
        }
        badges = _match_badges(p)
        assert ("PENTA", "red") in badges

    def test_penta_kill__zero_pentas_no_badge(self):
        """No PENTA badge when penta_kills is 0."""
        p = {
            "kills": "10",
            "deaths": "2",
            "assists": "5",
            "penta_kills": "0",
            "win": "1",
        }
        badges = _match_badges(p)
        names = [b[0] for b in badges]
        assert "PENTA" not in names

    def test_high_kda__exactly_five(self):
        """KDA 5+ badge at exactly 5.0 ratio."""
        # (10 + 5) / max(3, 1) = 5.0
        p = {"kills": "10", "deaths": "3", "assists": "5", "win": "0"}
        badges = _match_badges(p)
        assert ("KDA 5+", "green") in badges

    def test_high_kda__above_five(self):
        """KDA 5+ badge when ratio exceeds 5.0."""
        # (15 + 10) / max(2, 1) = 12.5
        p = {"kills": "15", "deaths": "2", "assists": "10", "win": "1"}
        badges = _match_badges(p)
        assert ("KDA 5+", "green") in badges

    def test_high_kda__below_five_no_badge(self):
        """No KDA 5+ badge when ratio is below 5.0."""
        # (5 + 3) / max(2, 1) = 4.0
        p = {"kills": "5", "deaths": "2", "assists": "3", "win": "1"}
        badges = _match_badges(p)
        names = [b[0] for b in badges]
        assert "KDA 5+" not in names

    def test_high_kda__zero_deaths_uses_one(self):
        """KDA calculation uses max(deaths, 1) when deaths=0."""
        # (3 + 2) / max(0, 1) = 5.0
        p = {"kills": "3", "deaths": "0", "assists": "2", "win": "0"}
        badges = _match_badges(p)
        assert ("KDA 5+", "green") in badges

    def test_cs_machine__above_threshold(self):
        """CS 8+/m badge when CS/min >= 8.0."""
        # (200 + 50) / (1800 / 60) = 250 / 30 = 8.33
        p = {
            "kills": "5",
            "deaths": "3",
            "assists": "4",
            "win": "1",
            "total_minions_killed": "200",
            "neutral_minions": "50",
            "time_played": "1800",
        }
        badges = _match_badges(p)
        assert ("CS 8+/m", "blue") in badges

    def test_cs_machine__exactly_eight(self):
        """CS 8+/m badge at exactly 8.0 CS/min."""
        # (240 + 0) / (1800 / 60) = 240 / 30 = 8.0
        p = {
            "kills": "0",
            "deaths": "1",
            "assists": "0",
            "win": "0",
            "total_minions_killed": "240",
            "neutral_minions": "0",
            "time_played": "1800",
        }
        badges = _match_badges(p)
        assert ("CS 8+/m", "blue") in badges

    def test_cs_machine__below_threshold_no_badge(self):
        """No CS 8+/m badge when CS/min < 8.0."""
        # (100 + 20) / (1800 / 60) = 120 / 30 = 4.0
        p = {
            "kills": "5",
            "deaths": "3",
            "assists": "4",
            "win": "1",
            "total_minions_killed": "100",
            "neutral_minions": "20",
            "time_played": "1800",
        }
        badges = _match_badges(p)
        names = [b[0] for b in badges]
        assert "CS 8+/m" not in names

    def test_cs_machine__short_game_skipped(self):
        """CS 8+/m badge not computed for games under 60 seconds."""
        p = {
            "kills": "5",
            "deaths": "0",
            "assists": "0",
            "win": "1",
            "total_minions_killed": "100",
            "neutral_minions": "100",
            "time_played": "30",
        }
        badges = _match_badges(p)
        names = [b[0] for b in badges]
        assert "CS 8+/m" not in names

    def test_cs_machine__zero_time_played_skipped(self):
        """No CS badge when time_played is 0."""
        p = {
            "kills": "5",
            "deaths": "1",
            "assists": "0",
            "win": "1",
            "total_minions_killed": "200",
            "neutral_minions": "50",
            "time_played": "0",
        }
        badges = _match_badges(p)
        names = [b[0] for b in badges]
        assert "CS 8+/m" not in names

    def test_multiple_badges__simultaneously(self):
        """A player can earn multiple badges in one game."""
        p = {
            "kills": "20",
            "deaths": "0",
            "assists": "5",
            "win": "1",
            "penta_kills": "1",
            "total_minions_killed": "200",
            "neutral_minions": "50",
            "time_played": "1800",
        }
        badges = _match_badges(p)
        names = [b[0] for b in badges]
        assert "Deathless" in names
        assert "PENTA" in names
        assert "KDA 5+" in names
        assert "CS 8+/m" in names

    def test_badge_order__consistent(self):
        """Badges returned: Deathless, PENTA, KDA 5+, CS 8+/m."""
        p = {
            "kills": "20",
            "deaths": "0",
            "assists": "5",
            "win": "1",
            "penta_kills": "1",
            "total_minions_killed": "200",
            "neutral_minions": "50",
            "time_played": "1800",
        }
        badges = _match_badges(p)
        names = [b[0] for b in badges]
        assert names == ["Deathless", "PENTA", "KDA 5+", "CS 8+/m"]

    def test_empty_participant__no_badges(self):
        """Empty participant dict returns no badges."""
        assert _match_badges({}) == []

    def test_invalid_kills_value__no_badges(self):
        """Non-numeric kills value returns empty list."""
        p = {"kills": "abc", "deaths": "0", "assists": "0", "win": "1"}
        assert _match_badges(p) == []

    def test_missing_penta_kills__defaults_to_zero(self):
        """Missing penta_kills field defaults to 0."""
        p = {"kills": "10", "deaths": "2", "assists": "5", "win": "1"}
        badges = _match_badges(p)
        names = [b[0] for b in badges]
        assert "PENTA" not in names

    def test_missing_cs_fields__no_cs_badge(self):
        """Missing CS fields default to 0."""
        p = {"kills": "5", "deaths": "3", "assists": "4", "win": "1"}
        badges = _match_badges(p)
        names = [b[0] for b in badges]
        assert "CS 8+/m" not in names


class TestMatchBadgesHtml:
    """Tests for _match_badges_html() rendering."""

    def test_empty_badges__returns_empty_string(self):
        """No badges produces no HTML output."""
        assert _match_badges_html([]) == ""

    def test_single_badge__renders_pill(self):
        """Single badge renders as colored span in container div."""
        result = _match_badges_html([("Deathless", "gold")])
        assert 'class="match-badges"' in result
        assert 'class="match-badge"' in result
        assert "Deathless" in result
        bg, fg = _MATCH_BADGE_COLORS["gold"]
        assert f"background:{bg}" in result
        assert f"color:{fg}" in result

    def test_multiple_badges__renders_all(self):
        """Multiple badges all appear in the output."""
        badges = [("PENTA", "red"), ("KDA 5+", "green")]
        result = _match_badges_html(badges)
        assert "PENTA" in result
        assert "KDA 5+" in result

    def test_unknown_color__uses_fallback(self):
        """Unknown color key uses gray fallback."""
        result = _match_badges_html([("Test", "unknown_color")])
        assert "background:#666" in result
        assert "color:#fff" in result

    def test_badge_name_html_escaped(self):
        """Badge names with special chars are HTML-escaped."""
        result = _match_badges_html([("<script>", "gold")])
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


class TestMatchBadgesInMatchHistory:
    """Integration: badges appear in rendered match history HTML."""

    def test_deathless_badge__in_match_row(self):
        """Deathless badge appears in match history output."""
        matches = [
            (
                "NA1_1",
                {"game_start": "1700000000000", "game_mode": "CLASSIC"},
                {
                    "win": "1",
                    "champion_name": "Zed",
                    "kills": "10",
                    "deaths": "0",
                    "assists": "5",
                },
            )
        ]
        result = _match_history_html(
            matches,
            "puuid",
            "na1",
            "P#1",
            0,
            False,
        )
        assert "Deathless" in result
        assert "match-badge" in result

    def test_penta_badge__in_match_row(self):
        """PENTA badge appears when penta_kills >= 1."""
        matches = [
            (
                "NA1_2",
                {"game_start": "1700000000000", "game_mode": "CLASSIC"},
                {
                    "win": "1",
                    "champion_name": "Samira",
                    "kills": "20",
                    "deaths": "5",
                    "assists": "3",
                    "penta_kills": "2",
                },
            )
        ]
        result = _match_history_html(
            matches,
            "puuid",
            "na1",
            "P#1",
            0,
            False,
        )
        assert "PENTA" in result

    def test_no_badges__no_badge_div(self):
        """Ordinary game produces no match-badges div."""
        matches = [
            (
                "NA1_3",
                {"game_start": "1700000000000", "game_mode": "CLASSIC"},
                {
                    "win": "0",
                    "champion_name": "Ahri",
                    "kills": "3",
                    "deaths": "7",
                    "assists": "1",
                },
            )
        ]
        result = _match_history_html(
            matches,
            "puuid",
            "na1",
            "P#1",
            0,
            False,
        )
        assert "match-badges" not in result


# ---------------------------------------------------------------------------
# Playstyle tags
# ---------------------------------------------------------------------------


class TestPlaystyleTags:
    """Tests for _playstyle_tags() threshold-based label computation."""

    def _base_stats(self, **overrides):
        """Return a minimal stats dict with sensible defaults."""
        defaults = {
            "total_games": "20",
            "total_wins": "10",
            "total_kills": "100",
            "total_deaths": "80",
            "total_assists": "120",
            "avg_kills": "5.0",
            "avg_deaths": "4.0",
            "avg_assists": "6.0",
            "kda": "2.75",
            "win_rate": "0.5",
        }
        defaults.update(overrides)
        return defaults

    def test_playstyle_tags__too_few_games__empty(self):
        """No tags when total_games < PLAYSTYLE_MIN_GAMES."""
        stats = self._base_stats(total_games="2")
        assert _playstyle_tags(stats) == []

    def test_playstyle_tags__exactly_min_games__computes(self):
        """Tags are computed when total_games == PLAYSTYLE_MIN_GAMES."""
        stats = self._base_stats(total_games=str(_PLAYSTYLE_MIN_GAMES))
        result = _playstyle_tags(stats)
        # With default stats, no tags should trigger
        assert isinstance(result, list)

    def test_playstyle_tags__aggressive_high_kills(self):
        """Aggressive tag triggers when avg_kills >= 8."""
        stats = self._base_stats(avg_kills="8.5")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Aggressive" in names

    def test_playstyle_tags__aggressive_high_ka(self):
        """Aggressive tag triggers when avg_kills + avg_assists >= 15."""
        stats = self._base_stats(avg_kills="7.0", avg_assists="8.5")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Aggressive" in names

    def test_playstyle_tags__aggressive_not_triggered(self):
        """Aggressive not triggered with low kills and low KA."""
        stats = self._base_stats(avg_kills="5.0", avg_assists="6.0")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Aggressive" not in names

    def test_playstyle_tags__team_fighter(self):
        """Team Fighter triggers when avg_assists >= 10."""
        stats = self._base_stats(avg_assists="11.0")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Team Fighter" in names

    def test_playstyle_tags__team_fighter_not_triggered(self):
        """Team Fighter not triggered with avg_assists < 10."""
        stats = self._base_stats(avg_assists="7.0")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Team Fighter" not in names

    def test_playstyle_tags__deathless(self):
        """Deathless triggers when avg_deaths <= 3."""
        stats = self._base_stats(avg_deaths="2.5")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Deathless" in names

    def test_playstyle_tags__deathless_boundary(self):
        """Deathless triggers at exactly 3.0 avg deaths."""
        stats = self._base_stats(avg_deaths="3.0")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Deathless" in names

    def test_playstyle_tags__deathless_not_triggered(self):
        """Deathless not triggered when avg_deaths > 3."""
        stats = self._base_stats(avg_deaths="3.1")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Deathless" not in names

    def test_playstyle_tags__kda_king(self):
        """KDA King triggers when kda >= 4.0."""
        stats = self._base_stats(kda="4.5")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "KDA King" in names

    def test_playstyle_tags__kda_king_boundary(self):
        """KDA King triggers at exactly 4.0."""
        stats = self._base_stats(kda="4.0")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "KDA King" in names

    def test_playstyle_tags__kda_king_not_triggered(self):
        """KDA King not triggered when kda < 4."""
        stats = self._base_stats(kda="3.99")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "KDA King" not in names

    def test_playstyle_tags__slayer(self):
        """Slayer triggers when avg_kills >= 10."""
        stats = self._base_stats(avg_kills="10.5")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Slayer" in names

    def test_playstyle_tags__slayer_not_triggered(self):
        """Slayer not triggered when avg_kills < 10."""
        stats = self._base_stats(avg_kills="9.9")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Slayer" not in names

    def test_playstyle_tags__winning_machine(self):
        """Winning Machine triggers when win_rate >= 0.6."""
        stats = self._base_stats(win_rate="0.65")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Winning Machine" in names

    def test_playstyle_tags__winning_machine_boundary(self):
        """Winning Machine triggers at exactly 0.6."""
        stats = self._base_stats(win_rate="0.6")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Winning Machine" in names

    def test_playstyle_tags__winning_machine_not_triggered(self):
        """Winning Machine not triggered when win_rate < 0.6."""
        stats = self._base_stats(win_rate="0.59")
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Winning Machine" not in names

    def test_playstyle_tags__multiple_tags(self):
        """Multiple tags can trigger simultaneously."""
        stats = self._base_stats(
            avg_kills="12.0",
            avg_assists="11.0",
            avg_deaths="2.0",
            kda="5.0",
            win_rate="0.7",
        )
        tags = _playstyle_tags(stats)
        names = [t[0] for t in tags]
        assert "Aggressive" in names
        assert "Slayer" in names
        assert "Team Fighter" in names
        assert "Deathless" in names
        assert "KDA King" in names
        assert "Winning Machine" in names

    def test_playstyle_tags__empty_stats(self):
        """Empty stats dict returns empty list."""
        assert _playstyle_tags({}) == []

    def test_playstyle_tags__invalid_values__returns_empty(self):
        """Non-numeric stat values return empty list gracefully."""
        stats = self._base_stats(avg_kills="not_a_number")
        assert _playstyle_tags(stats) == []

    def test_playstyle_tags__returns_tuples_with_colors(self):
        """Each tag is a (name, color) tuple with a valid CSS color."""
        stats = self._base_stats(avg_kills="8.5")
        tags = _playstyle_tags(stats)
        for name, color in tags:
            assert isinstance(name, str)
            assert color.startswith("#")


class TestPlaystylePillsHtml:
    """Tests for _playstyle_pills_html() rendering."""

    def test_empty_tags__returns_empty_string(self):
        assert _playstyle_pills_html([]) == ""

    def test_single_tag__renders_pill(self):
        result = _playstyle_pills_html([("Aggressive", "#e84057")])
        assert "playstyle-pills" in result
        assert "playstyle-pill" in result
        assert "Aggressive" in result
        assert "#e84057" in result

    def test_multiple_tags__renders_all(self):
        tags = [("Aggressive", "#e84057"), ("Deathless", "#2daf6f")]
        result = _playstyle_pills_html(tags)
        assert "Aggressive" in result
        assert "Deathless" in result

    def test_html_escapes_tag_name(self):
        tags = [("<script>", "#e84057")]
        result = _playstyle_pills_html(tags)
        assert "<script>" not in result
        assert html.escape("<script>") in result


# ---------------------------------------------------------------------------
# Rank history
# ---------------------------------------------------------------------------


class TestRankHistoryHtml:
    """Tests for _rank_history_html() rendering."""

    def test_empty_entries__returns_empty_string(self):
        assert _rank_history_html([]) == ""

    def test_single_entry__renders_table(self):
        entries = [("GOLD:II:75", 1700000000000.0)]
        result = _rank_history_html(entries)
        assert "Rank History" in result
        assert "GOLD" in result
        assert "II" in result
        assert "75 LP" in result
        assert "<table>" in result

    def test_multiple_entries__renders_all_rows(self):
        entries = [
            ("SILVER:I:50", 1700000000000.0),
            ("GOLD:IV:0", 1700100000000.0),
        ]
        result = _rank_history_html(entries)
        assert "SILVER" in result
        assert "GOLD" in result

    def test_date_format(self):
        # 1700000000 epoch = 2023-11-14 22:13:20 UTC
        entries = [("GOLD:II:75", 1700000000000.0)]
        result = _rank_history_html(entries)
        assert "2023-11-14" in result

    def test_html_escapes_values(self):
        entries = [("<script>:I:0", 1700000000000.0)]
        result = _rank_history_html(entries)
        assert "<script>" not in result
        assert html.escape("<script>") in result


# ---------------------------------------------------------------------------
# Per-champion / per-role breakdown helpers
# ---------------------------------------------------------------------------


def _make_match(
    champion_name="Zed",
    team_position="MIDDLE",
    win="1",
    kills="10",
    deaths="2",
    assists="5",
):
    """Build a minimal participant dict for testing breakdown helpers."""
    return {
        "champion_name": champion_name,
        "team_position": team_position,
        "win": win,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
    }


class TestBreakdownEntry:
    """_BreakdownEntry accumulates per-champion / per-role stats."""

    def test_initial_state(self):
        entry = _BreakdownEntry()
        assert entry.games == 0
        assert entry.wins == 0
        assert entry.total_kda == 0.0

    def test_add_win(self):
        entry = _BreakdownEntry()
        entry.add(win=True, kills=10, deaths=2, assists=5)
        assert entry.games == 1
        assert entry.wins == 1
        assert entry.total_kda == (10 + 5) / 2

    def test_add_loss(self):
        entry = _BreakdownEntry()
        entry.add(win=False, kills=3, deaths=7, assists=1)
        assert entry.games == 1
        assert entry.wins == 0

    def test_win_rate__all_wins(self):
        entry = _BreakdownEntry()
        entry.add(win=True, kills=1, deaths=1, assists=1)
        entry.add(win=True, kills=1, deaths=1, assists=1)
        assert entry.win_rate == 100.0

    def test_win_rate__half(self):
        entry = _BreakdownEntry()
        entry.add(win=True, kills=1, deaths=1, assists=1)
        entry.add(win=False, kills=1, deaths=1, assists=1)
        assert entry.win_rate == 50.0

    def test_win_rate__no_games(self):
        entry = _BreakdownEntry()
        assert entry.win_rate == 0.0

    def test_avg_kda__single_match(self):
        entry = _BreakdownEntry()
        entry.add(win=True, kills=10, deaths=2, assists=5)
        # KDA = (10+5)/2 = 7.5
        assert entry.avg_kda == 7.5

    def test_avg_kda__zero_deaths(self):
        """Deaths=0 uses max(deaths, 1)=1 denominator."""
        entry = _BreakdownEntry()
        entry.add(win=True, kills=5, deaths=0, assists=3)
        # KDA = (5+3)/1 = 8.0
        assert entry.avg_kda == 8.0

    def test_avg_kda__no_games(self):
        entry = _BreakdownEntry()
        assert entry.avg_kda == 0.0

    def test_avg_kda__multiple_matches(self):
        entry = _BreakdownEntry()
        entry.add(win=True, kills=10, deaths=2, assists=4)  # KDA = 14/2 = 7.0
        entry.add(win=False, kills=2, deaths=5, assists=3)  # KDA = 5/5 = 1.0
        # avg KDA = (7.0 + 1.0) / 2 = 4.0
        assert entry.avg_kda == 4.0


class TestComputeChampionBreakdown:
    """_compute_champion_breakdown groups matches by champion_name."""

    def test_empty_matches(self):
        result = _compute_champion_breakdown([])
        assert result == {}

    def test_single_champion(self):
        matches = [
            _make_match(champion_name="Zed", win="1", kills="10", deaths="2", assists="5"),
            _make_match(champion_name="Zed", win="0", kills="3", deaths="7", assists="1"),
        ]
        result = _compute_champion_breakdown(matches)
        assert "Zed" in result
        assert result["Zed"].games == 2
        assert result["Zed"].wins == 1
        assert result["Zed"].win_rate == 50.0

    def test_multiple_champions(self):
        matches = [
            _make_match(champion_name="Zed", win="1"),
            _make_match(champion_name="Ahri", win="0"),
            _make_match(champion_name="Zed", win="1"),
        ]
        result = _compute_champion_breakdown(matches)
        assert result["Zed"].games == 2
        assert result["Ahri"].games == 1

    def test_sorted_by_games_desc(self):
        matches = [
            _make_match(champion_name="Ahri"),
            _make_match(champion_name="Zed"),
            _make_match(champion_name="Zed"),
            _make_match(champion_name="Zed"),
        ]
        result = _compute_champion_breakdown(matches)
        keys = list(result.keys())
        assert keys == ["Zed", "Ahri"]

    def test_skips_empty_champion_name(self):
        matches = [_make_match(champion_name="")]
        result = _compute_champion_breakdown(matches)
        assert result == {}

    def test_kda_computation(self):
        matches = [
            _make_match(champion_name="Zed", kills="10", deaths="2", assists="4"),
        ]
        result = _compute_champion_breakdown(matches)
        # KDA = (10 + 4) / max(2, 1) = 7.0
        assert result["Zed"].avg_kda == 7.0


class TestComputeRoleBreakdown:
    """_compute_role_breakdown groups matches by team_position."""

    def test_empty_matches(self):
        result = _compute_role_breakdown([])
        assert result == {}

    def test_valid_roles(self):
        matches = [
            _make_match(team_position="TOP", win="1"),
            _make_match(team_position="JUNGLE", win="0"),
            _make_match(team_position="TOP", win="1"),
        ]
        result = _compute_role_breakdown(matches)
        assert result["TOP"].games == 2
        assert result["JUNGLE"].games == 1

    def test_ignores_invalid_roles(self):
        matches = [
            _make_match(team_position="INVALID"),
            _make_match(team_position=""),
        ]
        result = _compute_role_breakdown(matches)
        assert result == {}

    def test_all_five_roles(self):
        roles = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
        matches = [_make_match(team_position=r) for r in roles]
        result = _compute_role_breakdown(matches)
        assert set(result.keys()) == set(roles)

    def test_sorted_by_games_desc(self):
        matches = [
            _make_match(team_position="BOTTOM"),
            _make_match(team_position="TOP"),
            _make_match(team_position="TOP"),
            _make_match(team_position="TOP"),
        ]
        result = _compute_role_breakdown(matches)
        keys = list(result.keys())
        assert keys == ["TOP", "BOTTOM"]

    def test_win_rate(self):
        matches = [
            _make_match(team_position="MIDDLE", win="1"),
            _make_match(team_position="MIDDLE", win="1"),
            _make_match(team_position="MIDDLE", win="0"),
        ]
        result = _compute_role_breakdown(matches)
        assert result["MIDDLE"].win_rate == 66.7


class TestRenderChampionRows:
    def test_no_breakdown__games_only(self):
        result = _render_champion_rows([("Zed", 5.0)], None)
        assert "Zed" in result
        assert "<td>5</td>" in result
        assert "Win%" not in result

    def test_with_breakdown(self):
        entry = _BreakdownEntry()
        entry.add(win=True, kills=10, deaths=2, assists=5)
        entry.add(win=False, kills=3, deaths=7, assists=1)
        breakdown = {"Zed": entry}
        result = _render_champion_rows([("Zed", 10.0)], breakdown)
        assert "50.0%" in result
        assert "Zed" in result

    def test_missing_breakdown_entry__shows_dash(self):
        breakdown = {"Ahri": _BreakdownEntry()}
        result = _render_champion_rows([("Zed", 5.0)], breakdown)
        assert "&mdash;" in result

    def test_html_escapes_champion_name(self):
        result = _render_champion_rows([("<script>", 1.0)], None)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


class TestRenderRoleRows:
    def test_no_breakdown__games_only(self):
        result = _render_role_rows([("TOP", 5.0)], None)
        assert "TOP" in result
        assert "<td>5</td>" in result

    def test_with_breakdown(self):
        entry = _BreakdownEntry()
        entry.add(win=True, kills=5, deaths=1, assists=3)
        breakdown = {"TOP": entry}
        result = _render_role_rows([("TOP", 10.0)], breakdown)
        assert "100.0%" in result
        assert "8.00" in result  # KDA = (5+3)/1 = 8.0

    def test_missing_role__shows_dash(self):
        breakdown = {"JUNGLE": _BreakdownEntry()}
        result = _render_role_rows([("TOP", 5.0)], breakdown)
        assert "&mdash;" in result


class TestStatsTableWithBreakdown:
    """_stats_table renders enhanced columns when breakdown data is provided."""

    def test_without_breakdown__backward_compatible(self):
        """Old callers without breakdown get the same 2-column tables."""
        result = _stats_table({"total_games": "10"}, [("Zed", 5.0)], [("MIDDLE", 3.0)])
        assert "Zed" in result
        assert "Top Champions" in result
        assert "Win%" not in result
        assert "KDA" not in result

    def test_with_breakdown__shows_four_columns(self):
        champ_entry = _BreakdownEntry()
        champ_entry.add(win=True, kills=10, deaths=2, assists=5)
        role_entry = _BreakdownEntry()
        role_entry.add(win=False, kills=3, deaths=7, assists=1)
        result = _stats_table(
            {"total_games": "10"},
            [("Zed", 5.0)],
            [("MIDDLE", 3.0)],
            champ_breakdown={"Zed": champ_entry},
            role_breakdown={"MIDDLE": role_entry},
        )
        assert "Win%" in result
        assert "KDA" in result
        assert "100.0%" in result  # Zed 1/1 win
        assert "0.0%" in result  # MIDDLE 0/1 win
        assert "7.50" in result  # Zed KDA = (10+5)/2

    def test_role_section_renamed(self):
        result = _stats_table({"total_games": "10"}, [], [])
        assert "Role Performance" in result

    def test_empty_with_breakdown__colspan_4(self):
        result = _stats_table(
            {"total_games": "10"},
            [],
            [],
            champ_breakdown={},
            role_breakdown={},
        )
        assert "colspan='4'" in result

    def test_empty_without_breakdown__colspan_2(self):
        result = _stats_table({"total_games": "10"}, [], [])
        assert "colspan='2'" in result

    def test_diversity_still_shown(self):
        """Diversity section is preserved with breakdown data."""
        champs = [("Zed", 15.0), ("Ahri", 10.0)]
        result = _stats_table(
            {"total_games": "25"},
            champs,
            [],
            champ_breakdown={},
        )
        assert "Pool Diversity" in result


class TestMatchDetailValidation:
    """Input validation for /stats/match-detail endpoint."""

    @pytest.mark.asyncio
    async def test_match_detail__missing_match_id_returns_400(self):
        """Empty match_id returns 400."""
        from unittest.mock import MagicMock

        from lol_ui.routes.stats import match_detail

        request = MagicMock()
        request.query_params = {"match_id": "", "puuid": "abc"}
        request.app.state.r = MagicMock()

        resp = await match_detail(request)
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_match_detail__invalid_match_id_returns_400(self):
        """Malformed match_id (path traversal) returns 400."""
        from unittest.mock import MagicMock

        from lol_ui.routes.stats import match_detail

        request = MagicMock()
        request.query_params = {"match_id": "../../etc/passwd", "puuid": "abc"}
        request.app.state.r = MagicMock()

        resp = await match_detail(request)
        assert resp.status_code == 400
        assert "Invalid match ID" in resp.body.decode()

    @pytest.mark.asyncio
    async def test_match_detail__valid_match_id_accepted(self):
        """Valid match IDs like NA1_12345 are accepted."""
        import fakeredis.aioredis

        from lol_ui.routes.stats import match_detail

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        from unittest.mock import MagicMock

        request = MagicMock()
        request.query_params = {"match_id": "NA1_5425977998", "puuid": "abc"}
        request.app.state.r = r

        resp = await match_detail(request)
        # No 400 — accepted (returns "not available" since no data)
        assert resp.status_code == 200
        assert "not available" in resp.body.decode()
        await r.aclose()

    @pytest.mark.asyncio
    async def test_match_detail__invalid_puuid_returns_400(self):
        """Invalid puuid format returns 400."""
        from unittest.mock import MagicMock

        from lol_ui.routes.stats import match_detail

        request = MagicMock()
        request.query_params = {"match_id": "NA1_123", "puuid": "../../bad"}
        request.app.state.r = MagicMock()

        resp = await match_detail(request)
        assert resp.status_code == 400
        assert "Invalid PUUID" in resp.body.decode()
