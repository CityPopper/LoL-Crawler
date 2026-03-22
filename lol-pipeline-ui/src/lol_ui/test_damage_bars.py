"""Tests for damage breakdown bars (T2-2)."""

from __future__ import annotations

from lol_ui.damage_bars import _damage_bar_html, _damage_segments


class TestDamageSegments:
    """_damage_segments returns (color_var, pct) tuples."""

    def test_typical_damage__returns_three_segments(self):
        result = _damage_segments(5000, 3000, 2000)
        assert len(result) == 3
        # physical 50%, magic 30%, true 20%
        assert result[0] == ("var(--color-dmg-physical)", 50.0)
        assert result[1] == ("var(--color-dmg-magic)", 30.0)
        assert result[2] == ("var(--color-dmg-true)", 20.0)

    def test_zero_total_damage__returns_empty_list(self):
        result = _damage_segments(0, 0, 0)
        assert result == []

    def test_only_physical__returns_single_segment(self):
        result = _damage_segments(1000, 0, 0)
        assert len(result) == 1
        assert result[0] == ("var(--color-dmg-physical)", 100.0)

    def test_only_magic__returns_single_segment(self):
        result = _damage_segments(0, 500, 0)
        assert len(result) == 1
        assert result[0] == ("var(--color-dmg-magic)", 100.0)

    def test_only_true__returns_single_segment(self):
        result = _damage_segments(0, 0, 300)
        assert len(result) == 1
        assert result[0] == ("var(--color-dmg-true)", 100.0)

    def test_percentages_sum_to_100(self):
        result = _damage_segments(3333, 3333, 3334)
        total = sum(pct for _, pct in result)
        assert abs(total - 100.0) < 0.2

    def test_zero_segments_omitted(self):
        """Segments with zero damage are not included."""
        result = _damage_segments(7000, 0, 3000)
        assert len(result) == 2
        assert result[0][0] == "var(--color-dmg-physical)"
        assert result[1][0] == "var(--color-dmg-true)"


class TestDamageBarHtml:
    """_damage_bar_html returns an HTML string with flex segments."""

    def test_typical_damage__contains_flex_container(self):
        result = _damage_bar_html(5000, 3000, 2000)
        assert "display:flex" in result

    def test_typical_damage__contains_all_three_colors(self):
        result = _damage_bar_html(5000, 3000, 2000)
        assert "var(--color-dmg-physical)" in result
        assert "var(--color-dmg-magic)" in result
        assert "var(--color-dmg-true)" in result

    def test_typical_damage__contains_width_percentages(self):
        result = _damage_bar_html(5000, 3000, 2000)
        assert "width:50.0%" in result
        assert "width:30.0%" in result
        assert "width:20.0%" in result

    def test_zero_damage__returns_empty_bar(self):
        result = _damage_bar_html(0, 0, 0)
        assert "dmg-bar" in result
        # No segment divs when total is zero
        assert "var(--color-dmg-physical)" not in result
        assert "var(--color-dmg-magic)" not in result
        assert "var(--color-dmg-true)" not in result

    def test_bar_has_max_width(self):
        result = _damage_bar_html(100, 100, 100)
        assert "max-width:200px" in result

    def test_segments_have_min_width_zero(self):
        result = _damage_bar_html(5000, 3000, 2000)
        assert "min-width:0" in result

    def test_bar_has_height(self):
        result = _damage_bar_html(100, 50, 50)
        assert "height:8px" in result
