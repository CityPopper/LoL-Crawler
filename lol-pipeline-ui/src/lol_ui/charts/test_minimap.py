"""Tests for minimap kill overlay (T5-1)."""

from __future__ import annotations

from lol_ui.charts.minimap import (
    _kill_dot_svg,
    _minimap_html,
    _normalize_map_coords,
)

# ---------------------------------------------------------------------------
# _normalize_map_coords
# ---------------------------------------------------------------------------


class TestNormalizeMapCoords:
    """_normalize_map_coords converts game coordinates to CSS percentages."""

    def test_origin__returns_bottom_left(self):
        x, y = _normalize_map_coords(0, 0)
        assert x == 0.0
        assert y == 100.0  # Y inverted

    def test_max__returns_top_right(self):
        x, y = _normalize_map_coords(15000, 15000)
        assert x == 100.0
        assert y == 0.0  # Y inverted

    def test_midpoint__returns_50_50(self):
        x, y = _normalize_map_coords(7500, 7500)
        assert x == 50.0
        assert y == 50.0

    def test_negative_coords__clamps_to_zero(self):
        x, y = _normalize_map_coords(-100, -100)
        assert x == 0.0
        assert y == 100.0

    def test_above_max__clamps_to_100(self):
        x, y = _normalize_map_coords(20000, 20000)
        assert x == 100.0
        assert y == 0.0


# ---------------------------------------------------------------------------
# _kill_dot_svg
# ---------------------------------------------------------------------------


class TestKillDotSvg:
    """_kill_dot_svg renders an SVG circle element."""

    def test_returns_circle_element(self):
        result = _kill_dot_svg(50.0, 50.0, "#5383e8")
        assert "<circle" in result

    def test_contains_coordinates(self):
        result = _kill_dot_svg(25.0, 75.0, "#e84057")
        assert 'cx="25.0%"' in result
        assert 'cy="75.0%"' in result

    def test_contains_fill_color(self):
        result = _kill_dot_svg(50.0, 50.0, "#5383e8")
        assert 'fill="#5383e8"' in result

    def test_default_radius(self):
        result = _kill_dot_svg(50.0, 50.0, "#5383e8")
        assert 'r="5"' in result

    def test_custom_radius(self):
        result = _kill_dot_svg(50.0, 50.0, "#5383e8", radius=8)
        assert 'r="8"' in result

    def test_has_opacity(self):
        result = _kill_dot_svg(50.0, 50.0, "#5383e8")
        assert "opacity" in result


# ---------------------------------------------------------------------------
# _minimap_html
# ---------------------------------------------------------------------------


class TestMinimapHtml:
    """_minimap_html renders map image with positioned SVG kill dots."""

    def test_empty_events__returns_empty_map(self):
        result = _minimap_html([], "14.10.1")
        assert "minimap" in result
        assert "<circle" not in result

    def test_with_events__contains_circles(self):
        events = [
            {
                "x": 7500,
                "y": 7500,
                "killer": "Ahri",
                "victim": "Zed",
                "t": 60000,
                "killer_team": "100",
            },
        ]
        result = _minimap_html(events, "14.10.1")
        assert "<circle" in result

    def test_blue_team_kill__blue_color(self):
        events = [
            {
                "x": 7500,
                "y": 7500,
                "killer": "Ahri",
                "victim": "Zed",
                "t": 60000,
                "killer_team": "100",
            },
        ]
        result = _minimap_html(events, "14.10.1")
        assert "#5383e8" in result

    def test_red_team_kill__red_color(self):
        events = [
            {
                "x": 7500,
                "y": 7500,
                "killer": "Zed",
                "victim": "Ahri",
                "t": 60000,
                "killer_team": "200",
            },
        ]
        result = _minimap_html(events, "14.10.1")
        assert "#e84057" in result

    def test_container_max_width(self):
        result = _minimap_html([], "14.10.1")
        assert "max-width" in result
        assert "300px" in result

    def test_container_full_width(self):
        result = _minimap_html([], "14.10.1")
        assert "width" in result

    def test_uses_ddragon_map_image(self):
        result = _minimap_html([], "14.10.1")
        assert "ddragon.leagueoflegends.com" in result
        assert "map11" in result

    def test_no_version__still_renders(self):
        result = _minimap_html([], None)
        assert "minimap" in result

    def test_scrubber_present_when_events_exist(self):
        events = [
            {
                "x": 7500,
                "y": 7500,
                "killer": "Ahri",
                "victim": "Zed",
                "t": 60000,
                "killer_team": "100",
            },
            {
                "x": 3000,
                "y": 12000,
                "killer": "Zed",
                "victim": "Ahri",
                "t": 120000,
                "killer_team": "200",
            },
        ]
        result = _minimap_html(events, "14.10.1")
        assert "range" in result.lower() or "scrubber" in result.lower()

    def test_invalid_coords__skipped(self):
        events = [
            {
                "x": "bad",
                "y": "bad",
                "killer": "Ahri",
                "victim": "Zed",
                "t": 60000,
                "killer_team": "100",
            },
        ]
        result = _minimap_html(events, "14.10.1")
        assert "<circle" not in result

    def test_svg_viewbox_present(self):
        result = _minimap_html([], "14.10.1")
        assert "viewBox" in result

    def test_multiple_events__all_rendered(self):
        events = [
            {
                "x": 1000,
                "y": 2000,
                "killer": "Ahri",
                "victim": "Zed",
                "t": 60000,
                "killer_team": "100",
            },
            {
                "x": 3000,
                "y": 4000,
                "killer": "Zed",
                "victim": "Ahri",
                "t": 120000,
                "killer_team": "200",
            },
            {
                "x": 5000,
                "y": 6000,
                "killer": "Lux",
                "victim": "Garen",
                "t": 180000,
                "killer_team": "100",
            },
        ]
        result = _minimap_html(events, "14.10.1")
        assert result.count("<circle") == 3
