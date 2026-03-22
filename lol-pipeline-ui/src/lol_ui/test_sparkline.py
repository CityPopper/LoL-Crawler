"""Tests for 7-day win rate sparkline (T5-2)."""

from __future__ import annotations

import time

from lol_ui.sparkline import _bucket_by_day, _sparkline_html

_DAY_S = 86400


# ---------------------------------------------------------------------------
# _bucket_by_day
# ---------------------------------------------------------------------------


class TestBucketByDay:
    """_bucket_by_day groups matches into (wins, losses) per day."""

    def test_empty_matches__returns_seven_zeros(self):
        result = _bucket_by_day([], days=7)
        assert len(result) == 7
        assert all(w == 0 and lo == 0 for w, lo in result)

    def test_single_win_today__last_bucket_has_win(self):
        now_ms = int(time.time() * 1000)
        matches = [{"win": "1", "game_start": str(now_ms)}]
        result = _bucket_by_day(matches, days=7)
        assert result[-1] == (1, 0)

    def test_single_loss_today__last_bucket_has_loss(self):
        now_ms = int(time.time() * 1000)
        matches = [{"win": "0", "game_start": str(now_ms)}]
        result = _bucket_by_day(matches, days=7)
        assert result[-1] == (0, 1)

    def test_match_from_yesterday__in_second_to_last(self):
        yesterday_ms = int((time.time() - _DAY_S) * 1000)
        matches = [{"win": "1", "game_start": str(yesterday_ms)}]
        result = _bucket_by_day(matches, days=7)
        assert result[-2] == (1, 0)
        assert result[-1] == (0, 0)

    def test_match_older_than_7_days__ignored(self):
        old_ms = int((time.time() - 8 * _DAY_S) * 1000)
        matches = [{"win": "1", "game_start": str(old_ms)}]
        result = _bucket_by_day(matches, days=7)
        assert all(w == 0 and lo == 0 for w, lo in result)

    def test_custom_days(self):
        result = _bucket_by_day([], days=3)
        assert len(result) == 3

    def test_multiple_matches_same_day(self):
        now_ms = int(time.time() * 1000)
        matches = [
            {"win": "1", "game_start": str(now_ms)},
            {"win": "0", "game_start": str(now_ms - 1000)},
            {"win": "1", "game_start": str(now_ms - 2000)},
        ]
        result = _bucket_by_day(matches, days=7)
        assert result[-1] == (2, 1)

    def test_missing_game_start__skipped(self):
        matches = [{"win": "1"}]
        result = _bucket_by_day(matches, days=7)
        assert all(w == 0 and lo == 0 for w, lo in result)

    def test_invalid_game_start__skipped(self):
        matches = [{"win": "1", "game_start": "not_a_number"}]
        result = _bucket_by_day(matches, days=7)
        assert all(w == 0 and lo == 0 for w, lo in result)


# ---------------------------------------------------------------------------
# _sparkline_html
# ---------------------------------------------------------------------------


class TestSparklineHtml:
    """_sparkline_html renders CSS-based stacked bars per day."""

    def test_empty_matches__returns_sparkline_container(self):
        result = _sparkline_html([])
        assert "sparkline" in result

    def test_with_wins__contains_blue_bar(self):
        now_ms = int(time.time() * 1000)
        matches = [{"win": "1", "game_start": str(now_ms)}]
        result = _sparkline_html(matches)
        assert "sparkline__win" in result

    def test_with_losses__contains_red_bar(self):
        now_ms = int(time.time() * 1000)
        matches = [{"win": "0", "game_start": str(now_ms)}]
        result = _sparkline_html(matches)
        assert "sparkline__loss" in result

    def test_no_js_used(self):
        """Sparkline must be pure CSS, no JavaScript."""
        now_ms = int(time.time() * 1000)
        matches = [{"win": "1", "game_start": str(now_ms)}]
        result = _sparkline_html(matches)
        assert "<script" not in result

    def test_has_seven_day_columns(self):
        result = _sparkline_html([])
        assert result.count("sparkline__day") == 7

    def test_all_empty_days__still_renders(self):
        result = _sparkline_html([])
        assert "sparkline" in result
        assert "sparkline__day" in result
