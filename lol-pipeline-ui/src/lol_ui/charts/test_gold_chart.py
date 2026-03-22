"""Tests for gold-over-time SVG chart (T4-1)."""

from __future__ import annotations

from lol_ui.charts.gold_chart import (
    _format_gold_label,
    _gold_axis_labels,
    _gold_chart_svg,
    _gold_legend,
    _gold_polyline,
    _normalize_gold_points,
)

# ---------------------------------------------------------------------------
# _normalize_gold_points
# ---------------------------------------------------------------------------


class TestNormalizeGoldPoints:
    """_normalize_gold_points maps gold values to SVG coordinate strings."""

    def test_empty_values__returns_empty(self):
        result = _normalize_gold_points([], 10000, 600, 300, 20, 50)
        assert result == []

    def test_zero_max_gold__returns_empty(self):
        result = _normalize_gold_points([100, 200], 0, 600, 300, 20, 50)
        assert result == []

    def test_single_value__returns_single_point(self):
        result = _normalize_gold_points([5000], 10000, 600, 300, 20, 50)
        assert len(result) == 1
        # x should be padding_left since single point
        x, _y = result[0].split(",")
        assert int(x) == 50

    def test_two_values__first_at_left_last_at_right(self):
        result = _normalize_gold_points([0, 10000], 10000, 600, 300, 20, 50)
        assert len(result) == 2
        x0 = int(result[0].split(",")[0])
        x1 = int(result[1].split(",")[0])
        assert x0 == 50  # padding_left
        assert x1 == 590  # width - padding_right

    def test_max_gold_at_top__zero_at_bottom(self):
        result = _normalize_gold_points([0, 10000], 10000, 600, 300, 20, 50)
        # gold=0 should be at bottom (padding_top + chart_h)
        y0 = int(result[0].split(",")[1])
        # gold=10000 should be at top (padding_top)
        y1 = int(result[1].split(",")[1])
        assert y0 > y1  # bottom > top in SVG coords
        assert y1 == 20  # at padding_top

    def test_returns_string_coords(self):
        result = _normalize_gold_points([500, 1000], 2000, 600, 300, 20, 50)
        for pt in result:
            assert "," in pt
            parts = pt.split(",")
            assert len(parts) == 2
            int(parts[0])  # should not raise
            int(parts[1])  # should not raise


# ---------------------------------------------------------------------------
# _gold_polyline
# ---------------------------------------------------------------------------


class TestGoldPolyline:
    """_gold_polyline renders an SVG polyline element."""

    def test_empty_points__returns_empty(self):
        assert _gold_polyline([], "red", "2", "1") == ""

    def test_valid_points__contains_polyline_tag(self):
        result = _gold_polyline(["10,20", "30,40"], "red", "2", "1")
        assert "<polyline" in result

    def test_valid_points__contains_all_points(self):
        result = _gold_polyline(["10,20", "30,40"], "red", "2", "1")
        assert "10,20 30,40" in result

    def test_valid_points__contains_stroke_attributes(self):
        result = _gold_polyline(["10,20"], "blue", "2.5", "0.6")
        assert 'stroke="blue"' in result
        assert 'stroke-width="2.5"' in result
        assert 'opacity="0.6"' in result

    def test_valid_points__contains_linecap_round(self):
        result = _gold_polyline(["10,20"], "red", "2", "1")
        assert 'stroke-linecap="round"' in result

    def test_valid_points__fill_none(self):
        result = _gold_polyline(["10,20"], "red", "2", "1")
        assert 'fill="none"' in result


# ---------------------------------------------------------------------------
# _gold_axis_labels
# ---------------------------------------------------------------------------


class TestGoldAxisLabels:
    """_gold_axis_labels renders SVG text for axes."""

    def test_has_y_axis_labels(self):
        result = _gold_axis_labels(10000, 30, 600, 300, 20, 50)
        assert "10k" in result
        assert "5k" in result
        assert "0" in result

    def test_has_x_axis_every_five_minutes(self):
        result = _gold_axis_labels(10000, 30, 600, 300, 20, 50)
        assert "0m" in result
        assert "5m" in result
        assert "10m" in result
        assert "15m" in result
        assert "30m" in result

    def test_x_axis_no_intermediate_minutes(self):
        """Should NOT have 1m, 2m, 3m labels (only every 5)."""
        result = _gold_axis_labels(10000, 30, 600, 300, 20, 50)
        assert "1m<" not in result
        assert "2m<" not in result

    def test_zero_max_minutes__no_crash(self):
        result = _gold_axis_labels(10000, 0, 600, 300, 20, 50)
        assert isinstance(result, str)

    def test_contains_text_elements(self):
        result = _gold_axis_labels(10000, 10, 600, 300, 20, 50)
        assert "<text" in result
        assert "</text>" in result


# ---------------------------------------------------------------------------
# _format_gold_label
# ---------------------------------------------------------------------------


class TestFormatGoldLabel:
    """_format_gold_label formats gold values for display."""

    def test_small_value__no_suffix(self):
        assert _format_gold_label(500) == "500"

    def test_thousands__k_suffix(self):
        assert _format_gold_label(15000) == "15k"

    def test_zero(self):
        assert _format_gold_label(0) == "0"

    def test_exactly_1000(self):
        assert _format_gold_label(1000) == "1k"

    def test_below_1000(self):
        assert _format_gold_label(999) == "999"


# ---------------------------------------------------------------------------
# _gold_legend
# ---------------------------------------------------------------------------


class TestGoldLegend:
    """_gold_legend renders champion color swatches + final gold."""

    def test_empty_players__returns_empty(self):
        assert _gold_legend([], None) == ""

    def test_renders_swatch(self):
        players = [{"champion_name": "Ahri", "color_hex": "#5383e8", "final_gold": "15k"}]
        result = _gold_legend(players, None)
        assert "gold-legend__swatch" in result
        assert "#5383e8" in result

    def test_renders_champion_name(self):
        players = [{"champion_name": "Ahri", "color_hex": "#5383e8", "final_gold": "15k"}]
        result = _gold_legend(players, None)
        assert "Ahri" in result

    def test_renders_final_gold(self):
        players = [{"champion_name": "Ahri", "color_hex": "#5383e8", "final_gold": "15k"}]
        result = _gold_legend(players, None)
        assert "15k" in result

    def test_html_escapes_champion_name(self):
        players = [{"champion_name": "<script>", "color_hex": "#888", "final_gold": "0"}]
        result = _gold_legend(players, None)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result


# ---------------------------------------------------------------------------
# _gold_chart_svg (integration)
# ---------------------------------------------------------------------------


class TestGoldChartSvg:
    """_gold_chart_svg assembles the complete chart."""

    def test_empty_data__returns_empty(self):
        assert _gold_chart_svg({}, "puuid1") == ""

    def test_all_zero_gold__returns_empty(self):
        data = {
            "p1": {
                "gold_values": [0, 0, 0],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            }
        }
        assert _gold_chart_svg(data, "p1") == ""

    def test_valid_data__contains_svg(self):
        data = {
            "p1": {
                "gold_values": [0, 500, 1500, 3000],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            }
        }
        result = _gold_chart_svg(data, "p1")
        assert "<svg" in result
        assert "</svg>" in result

    def test_valid_data__contains_viewbox(self):
        data = {
            "p1": {
                "gold_values": [0, 1000],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            }
        }
        result = _gold_chart_svg(data, "p1")
        assert 'viewBox="0 0 600 300"' in result

    def test_valid_data__responsive_width(self):
        data = {
            "p1": {
                "gold_values": [0, 1000],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            }
        }
        result = _gold_chart_svg(data, "p1")
        assert 'width="100%"' in result
        assert 'preserveAspectRatio="xMidYMid meet"' in result

    def test_valid_data__geometric_precision(self):
        data = {
            "p1": {
                "gold_values": [0, 1000],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            }
        }
        result = _gold_chart_svg(data, "p1")
        assert 'shape-rendering="geometricPrecision"' in result

    def test_focused_player__thicker_stroke(self):
        data = {
            "p1": {
                "gold_values": [0, 5000],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            },
            "p2": {
                "gold_values": [0, 4000],
                "team_id": "100",
                "champion_name": "Zed",
                "team_index": 1,
            },
        }
        result = _gold_chart_svg(data, "p1")
        assert 'stroke-width="2.5"' in result
        assert 'stroke-width="1.5"' in result

    def test_focused_player__full_opacity(self):
        data = {
            "p1": {
                "gold_values": [0, 5000],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            },
            "p2": {
                "gold_values": [0, 4000],
                "team_id": "200",
                "champion_name": "Zed",
                "team_index": 0,
            },
        }
        result = _gold_chart_svg(data, "p1")
        assert 'opacity="1"' in result
        assert 'opacity="0.6"' in result

    def test_red_team__uses_red_colors(self):
        data = {
            "p1": {
                "gold_values": [0, 5000],
                "team_id": "200",
                "champion_name": "Zed",
                "team_index": 0,
            }
        }
        result = _gold_chart_svg(data, "p1")
        assert "var(--chart-r0)" in result

    def test_blue_team__uses_blue_colors(self):
        data = {
            "p1": {
                "gold_values": [0, 5000],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            }
        }
        result = _gold_chart_svg(data, "p1")
        assert "var(--chart-b0)" in result

    def test_has_legend(self):
        data = {
            "p1": {
                "gold_values": [0, 5000],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            }
        }
        result = _gold_chart_svg(data, "p1")
        assert "gold-legend" in result

    def test_has_chart_wrapper(self):
        data = {
            "p1": {
                "gold_values": [0, 5000],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            }
        }
        result = _gold_chart_svg(data, "p1")
        assert "gold-chart" in result

    def test_contains_polyline(self):
        data = {
            "p1": {
                "gold_values": [0, 1000, 3000],
                "team_id": "100",
                "champion_name": "Ahri",
                "team_index": 0,
            }
        }
        result = _gold_chart_svg(data, "p1")
        assert "<polyline" in result
