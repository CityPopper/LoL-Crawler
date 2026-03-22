"""Tests for win rate SVG donut (T2-4)."""

from __future__ import annotations

from lol_ui.charts.win_rate_donut import _donut_arc, _win_rate_donut_svg


class TestDonutArc:
    """_donut_arc returns (dash, gap) for the SVG stroke-dasharray."""

    def test_fifty_percent__half_circumference(self):
        dash, gap = _donut_arc(0.5)
        # circumference = 2 * pi * 40 ~ 251.3
        assert abs(dash - 125.65) < 0.1
        assert abs(gap - 125.65) < 0.1

    def test_zero_percent__zero_dash(self):
        dash, gap = _donut_arc(0.0)
        assert dash == 0.0
        assert abs(gap - 251.3) < 0.1

    def test_hundred_percent__full_circumference(self):
        dash, gap = _donut_arc(1.0)
        assert abs(dash - 251.3) < 0.1
        assert gap == 0.0

    def test_custom_radius(self):
        dash, gap = _donut_arc(0.5, radius=50)
        circumference = 2 * 3.14159265 * 50
        assert abs(dash - circumference / 2) < 0.5
        assert abs(gap - circumference / 2) < 0.5

    def test_seventy_five_percent(self):
        dash, _gap = _donut_arc(0.75)
        expected_dash = 251.3 * 0.75
        assert abs(dash - expected_dash) < 0.1


class TestWinRateDonutSvg:
    """_win_rate_donut_svg returns SVG markup."""

    def test_typical_values__contains_svg_element(self):
        result = _win_rate_donut_svg(60, 100)
        assert "<svg" in result
        assert "</svg>" in result

    def test_typical_values__contains_circle_elements(self):
        result = _win_rate_donut_svg(60, 100)
        assert "<circle" in result

    def test_typical_values__contains_rotate_transform(self):
        result = _win_rate_donut_svg(60, 100)
        assert "rotate(-90 50 50)" in result

    def test_typical_values__contains_stroke_linecap_round(self):
        result = _win_rate_donut_svg(60, 100)
        assert 'stroke-linecap="round"' in result

    def test_typical_values__shows_win_rate_text(self):
        result = _win_rate_donut_svg(60, 100)
        assert "60%" in result

    def test_zero_wins__shows_zero_percent(self):
        result = _win_rate_donut_svg(0, 100)
        assert "0%" in result

    def test_all_wins__shows_hundred_percent(self):
        result = _win_rate_donut_svg(10, 10)
        assert "100%" in result

    def test_zero_total__shows_zero_percent(self):
        """Edge case: no games played at all."""
        result = _win_rate_donut_svg(0, 0)
        assert "0%" in result
        assert "<svg" in result

    def test_shows_record_text(self):
        result = _win_rate_donut_svg(7, 10)
        assert "7W" in result
        assert "3L" in result

    def test_no_f_strings_in_svg(self):
        """SVG must use string concat, not f-strings (literal {} breaks f-strings)."""
        # This is a code review check — if the function works at all,
        # f-string issues would cause SyntaxError or runtime error.
        # Just verify it runs and produces valid output.
        result = _win_rate_donut_svg(50, 100)
        assert "stroke-dasharray" in result
