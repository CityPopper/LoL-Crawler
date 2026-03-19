"""Unit tests for lol_ui.main — helper functions and data loading."""

from __future__ import annotations

import html
import json
import re
from unittest.mock import patch

import pytest

from lol_ui.main import (
    _PUUID_RE,
    _aggregate_by_mode,
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
