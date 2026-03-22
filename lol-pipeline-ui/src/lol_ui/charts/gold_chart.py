"""Gold-over-time SVG line chart — 10 polylines with per-player colors.

Provides:
- ``_normalize_gold_points(...)`` — map gold values to SVG coordinates
- ``_gold_polyline(...)`` — render a single SVG polyline element
- ``_gold_axis_labels(...)`` — render X/Y axis labels
- ``_gold_legend(...)`` — render legend panel HTML
- ``_gold_chart_svg(...)`` — assemble the complete chart + legend
"""

from __future__ import annotations

import html

from lol_ui.rendering import _champion_icon_html

# Per-player colors: 5 blue side, 5 red side (CSS variable names).
_BLUE_COLORS = [
    "var(--chart-b0)",
    "var(--chart-b1)",
    "var(--chart-b2)",
    "var(--chart-b3)",
    "var(--chart-b4)",
]
_RED_COLORS = [
    "var(--chart-r0)",
    "var(--chart-r1)",
    "var(--chart-r2)",
    "var(--chart-r3)",
    "var(--chart-r4)",
]

# Hex fallbacks for legend swatches (CSS vars don't work in inline HTML).
_BLUE_HEX = ["#5383e8", "#3cbec0", "#2daf6f", "#9e6cd9", "#f4c874"]
_RED_HEX = ["#e84057", "#e89240", "#ffdc00", "#ff6b6b", "#c0a060"]

_SVG_WIDTH = 600
_SVG_HEIGHT = 300
_PADDING_TOP = 20
_PADDING_LEFT = 50
_PADDING_BOTTOM = 25
_PADDING_RIGHT = 10


def _normalize_gold_points(
    gold_values: list[int],
    max_gold: int,
    width: int,
    height: int,
    padding_top: int,
    padding_left: int,
) -> list[str]:
    """Map gold values to SVG coordinate strings "x,y".

    X is evenly distributed across the chart area.
    Y is inverted (SVG y=0 is top) and scaled to max_gold.
    Returns an empty list when gold_values is empty or max_gold is zero.
    """
    if not gold_values or max_gold <= 0:
        return []

    chart_w = width - padding_left - _PADDING_RIGHT
    chart_h = height - padding_top - _PADDING_BOTTOM
    count = len(gold_values)
    points: list[str] = []
    for i, gold in enumerate(gold_values):
        x = padding_left if count == 1 else padding_left + round(i * chart_w / (count - 1))
        y = padding_top + chart_h - round(gold * chart_h / max_gold)
        points.append(str(x) + "," + str(y))
    return points


def _gold_polyline(
    points: list[str],
    color: str,
    stroke_width: str,
    opacity: str,
) -> str:
    """Render an SVG polyline element from coordinate strings.

    Returns an empty string when points is empty.
    """
    if not points:
        return ""
    pts = " ".join(points)
    return (
        "<polyline"
        ' points="' + pts + '"'
        ' fill="none"'
        ' stroke="' + color + '"'
        ' stroke-width="' + stroke_width + '"'
        ' opacity="' + opacity + '"'
        ' stroke-linecap="round"'
        ' stroke-linejoin="round"'
        "/>"
    )


def _gold_axis_labels(
    max_gold: int,
    max_minutes: int,
    width: int,
    height: int,
    padding_top: int,
    padding_left: int,
) -> str:
    """Render SVG text elements for X-axis (time) and Y-axis (gold) labels."""
    chart_w = width - padding_left - _PADDING_RIGHT
    chart_h = height - padding_top - _PADDING_BOTTOM
    parts: list[str] = []

    # Y-axis: 0, 25%, 50%, 75%, 100% of max_gold
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        val = round(max_gold * frac)
        y = padding_top + chart_h - round(frac * chart_h)
        label = _format_gold_label(val)
        parts.append(
            "<text"
            ' x="' + str(padding_left - 5) + '"'
            ' y="' + str(y + 4) + '"'
            ' text-anchor="end"'
            ' fill="var(--color-muted)"'
            ' font-size="10"'
            ' font-family="system-ui, sans-serif"'
            ">" + label + "</text>"
        )

    # X-axis: every 5 minutes
    if max_minutes > 0:
        step = 5
        minute = 0
        while minute <= max_minutes:
            if max_minutes == 0:
                x = padding_left
            else:
                x = padding_left + round(minute * chart_w / max_minutes)
            y_pos = height - _PADDING_BOTTOM + 15
            parts.append(
                "<text"
                ' x="' + str(x) + '"'
                ' y="' + str(y_pos) + '"'
                ' text-anchor="middle"'
                ' fill="var(--color-muted)"'
                ' font-size="10"'
                ' font-family="system-ui, sans-serif"'
                ">" + str(minute) + "m</text>"
            )
            minute += step

    return "".join(parts)


def _format_gold_label(value: int) -> str:
    """Format a gold value for axis labels (e.g. 15000 -> '15k')."""
    if value >= 1000:
        return str(value // 1000) + "k"
    return str(value)


def _gold_legend(
    players: list[dict[str, str]],
    version: str | None,
) -> str:
    """Render a legend panel showing champion name + color swatch + final gold.

    Each player dict must contain 'champion_name', 'color_hex', and 'final_gold'.
    """
    if not players:
        return ""
    items: list[str] = []
    for p in players:
        champ = html.escape(p.get("champion_name", "?"))
        color = html.escape(p.get("color_hex", "#888"))
        final = html.escape(p.get("final_gold", "0"))
        icon = _champion_icon_html(p.get("champion_name", ""), version)
        items.append(
            '<div class="gold-legend__item">'
            '<span class="gold-legend__swatch" style="background:'
            + color
            + '"></span>'
            + icon
            + '<span class="gold-legend__name">'
            + champ
            + "</span>"
            + '<span class="gold-legend__val">'
            + final
            + "</span>"
            "</div>"
        )
    return '<div class="gold-legend">' + "".join(items) + "</div>"


def _find_gold_bounds(
    gold_data: dict[str, dict[str, object]],
) -> tuple[int, int]:
    """Find global max gold and max frame count across all players."""
    max_gold = 0
    max_frames = 0
    for info in gold_data.values():
        values = info.get("gold_values", [])
        if not isinstance(values, list):
            continue
        for v in values:
            if isinstance(v, int) and v > max_gold:
                max_gold = v
        max_frames = max(max_frames, len(values))
    return max_gold, max_frames


def _resolve_team_color(
    team_id: str,
    team_index: object,
    counters: dict[str, int],
) -> tuple[str, str]:
    """Return (css_color, hex_color) for a player, advancing counters."""
    is_red = team_id == "200"
    colors = _RED_COLORS if is_red else _BLUE_COLORS
    hexes = _RED_HEX if is_red else _BLUE_HEX
    counter_key = "red" if is_red else "blue"
    if isinstance(team_index, int) and 0 <= team_index < 5:
        idx = team_index
    else:
        idx = min(counters.get(counter_key, 0), 4)
        counters[counter_key] = counters.get(counter_key, 0) + 1
    return colors[idx], hexes[idx]


def _build_player_line(
    puuid: str,
    info: dict[str, object],
    max_gold: int,
    focused_puuid: str,
    counters: dict[str, int],
) -> tuple[str, bool, dict[str, str]] | None:
    """Build polyline + legend entry for one player. Returns None to skip."""
    values = info.get("gold_values", [])
    if not isinstance(values, list) or not values:
        return None
    team_id = str(info.get("team_id", "100"))
    champ = str(info.get("champion_name", "?"))
    color, hex_color = _resolve_team_color(team_id, info.get("team_index"), counters)
    int_values = [v if isinstance(v, int) else 0 for v in values]
    points = _normalize_gold_points(
        int_values, max_gold, _SVG_WIDTH, _SVG_HEIGHT, _PADDING_TOP, _PADDING_LEFT
    )
    is_focused = puuid == focused_puuid
    sw = "2.5" if is_focused else "1.5"
    op = "1" if is_focused else "0.6"
    line = _gold_polyline(points, color, sw, op)
    final_gold = _format_gold_label(int_values[-1]) if int_values else "0"
    legend_entry = {"champion_name": champ, "color_hex": hex_color, "final_gold": final_gold}
    return line, is_focused, legend_entry


def _gold_chart_svg(
    gold_data: dict[str, dict[str, object]],
    focused_puuid: str,
    version: str | None = None,
) -> str:
    """Assemble a complete gold-over-time SVG chart with legend.

    *gold_data* maps puuid to a dict with keys:
        - ``gold_values``: list[int] — per-minute gold totals
        - ``team_id``: str — "100" (blue) or "200" (red)
        - ``champion_name``: str
        - ``team_index``: int — 0-4 position within the team

    The focused player's line is drawn with full opacity and thicker stroke.
    Returns empty string when gold_data is empty.
    """
    if not gold_data:
        return ""

    max_gold, max_frames = _find_gold_bounds(gold_data)
    if max_gold == 0 or max_frames == 0:
        return ""

    max_minutes = max_frames - 1
    lines_bg: list[str] = []
    lines_fg: list[str] = []
    legend_players: list[dict[str, str]] = []
    counters: dict[str, int] = {"blue": 0, "red": 0}

    for puuid, info in gold_data.items():
        result = _build_player_line(puuid, info, max_gold, focused_puuid, counters)
        if result is None:
            continue
        line, is_focused, legend_entry = result
        (lines_fg if is_focused else lines_bg).append(line)
        legend_players.append(legend_entry)

    axis = _gold_axis_labels(
        max_gold, max_minutes, _SVG_WIDTH, _SVG_HEIGHT, _PADDING_TOP, _PADDING_LEFT
    )
    all_lines = "".join(lines_bg) + "".join(lines_fg)
    legend = _gold_legend(legend_players, version)

    svg = (
        '<svg viewBox="0 0 ' + str(_SVG_WIDTH) + " " + str(_SVG_HEIGHT) + '"'
        ' width="100%"'
        ' preserveAspectRatio="xMidYMid meet"'
        ' shape-rendering="geometricPrecision"'
        ' xmlns="http://www.w3.org/2000/svg"'
        ">" + axis + all_lines + "</svg>"
    )

    return '<div class="gold-chart">' + svg + legend + "</div>"
