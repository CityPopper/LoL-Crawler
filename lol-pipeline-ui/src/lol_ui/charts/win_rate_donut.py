"""Win rate SVG donut chart — inline SVG with stroke-dasharray arc."""

from __future__ import annotations

import math

_DEFAULT_RADIUS = 40
_VIEWBOX_SIZE = 100


def _donut_arc(win_rate: float, radius: int = _DEFAULT_RADIUS) -> tuple[float, float]:
    """Return (dash, gap) tuple for stroke-dasharray from a 0.0-1.0 win rate.

    The circumference is 2 * pi * radius.
    """
    circumference = 2 * math.pi * radius
    circumference = round(circumference, 1)
    dash = round(circumference * win_rate, 1)
    gap = round(circumference - dash, 1)
    return (dash, gap)


def _win_rate_donut_svg(wins: int, total: int) -> str:
    """Return an inline SVG string rendering a win rate donut chart.

    Uses string concatenation (NOT f-strings) to avoid issues with
    literal curly braces in SVG style blocks.

    Handles 0% and 100% edge cases gracefully.
    """
    if total == 0:
        win_rate = 0.0
        pct_display = "0%"
    else:
        win_rate = wins / total
        pct_display = str(round(win_rate * 100)) + "%"

    losses = total - wins
    record = str(wins) + "W " + str(losses) + "L"
    dash, gap = _donut_arc(win_rate)
    cx = str(_VIEWBOX_SIZE // 2)
    cy = str(_VIEWBOX_SIZE // 2)
    r = str(_DEFAULT_RADIUS)
    stroke_width = "8"

    # Background circle (track)
    bg_circle = (
        "<circle"
        ' cx="' + cx + '"'
        ' cy="' + cy + '"'
        ' r="' + r + '"'
        ' fill="none"'
        ' stroke="var(--color-surface2)"'
        ' stroke-width="' + stroke_width + '"'
        "/>"
    )

    # Win rate arc
    dasharray = str(dash) + " " + str(gap)
    win_circle = (
        "<circle"
        ' cx="' + cx + '"'
        ' cy="' + cy + '"'
        ' r="' + r + '"'
        ' fill="none"'
        ' stroke="var(--color-win)"'
        ' stroke-width="' + stroke_width + '"'
        ' stroke-dasharray="' + dasharray + '"'
        ' stroke-linecap="round"'
        ' transform="rotate(-90 ' + cx + " " + cy + ')"'
        "/>"
    )

    # Center text
    center_text = (
        "<text"
        ' x="' + cx + '"'
        ' y="' + str(int(cy) - 4) + '"'
        ' text-anchor="middle"'
        ' dominant-baseline="central"'
        ' fill="var(--color-text)"'
        ' font-size="16"'
        ' font-weight="700"'
        ' font-family="system-ui, sans-serif"'
        ">" + pct_display + "</text>"
    )

    record_text = (
        "<text"
        ' x="' + cx + '"'
        ' y="' + str(int(cy) + 12) + '"'
        ' text-anchor="middle"'
        ' dominant-baseline="central"'
        ' fill="var(--color-muted)"'
        ' font-size="10"'
        ' font-family="system-ui, sans-serif"'
        ">" + record + "</text>"
    )

    svg = (
        '<svg viewBox="0 0 '
        + str(_VIEWBOX_SIZE)
        + " "
        + str(_VIEWBOX_SIZE)
        + '" width="100" height="100"'
        ' xmlns="http://www.w3.org/2000/svg"'
        ">" + bg_circle + win_circle + center_text + record_text + "</svg>"
    )
    return svg
