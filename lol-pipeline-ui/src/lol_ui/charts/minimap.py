"""Minimap kill overlay — static DDragon map with SVG kill dots.

Provides:
- ``_kill_dot_svg(x, y, team_color, radius)`` -- SVG circle element
- ``_normalize_map_coords(game_x, game_y)`` -- convert game coords to CSS %
- ``_minimap_html(events, version)`` -- map image + positioned SVG circles + range scrubber
"""

from __future__ import annotations

import html

_MAP_SIZE = 15000
_BLUE_HEX = "#5383e8"
_RED_HEX = "#e84057"


def _normalize_map_coords(game_x: int, game_y: int) -> tuple[float, float]:
    """Convert game coordinates to CSS percentage positions.

    Formula: ``svg_x = game_x / 15000 * 100``,
             ``svg_y = (1 - game_y / 15000) * 100`` (Y inverted).
    Values are clamped to [0, 100].
    """
    clamped_x = max(0, min(game_x, _MAP_SIZE))
    clamped_y = max(0, min(game_y, _MAP_SIZE))
    css_x = clamped_x / _MAP_SIZE * 100
    css_y = (1 - clamped_y / _MAP_SIZE) * 100
    return css_x, css_y


def _kill_dot_svg(x: float, y: float, team_color: str, radius: int = 5) -> str:
    """Render an SVG circle element at the given percentage coordinates."""
    return (
        "<circle"
        ' cx="' + str(x) + '%"'
        ' cy="' + str(y) + '%"'
        ' r="' + str(radius) + '"'
        ' fill="' + team_color + '"'
        ' opacity="0.85"'
        ' stroke="#000" stroke-width="0.5"'
        "/>"
    )


def _minimap_html(
    events: list[dict[str, object]],
    version: str | None,
) -> str:
    """Render a minimap with kill dots overlaid as SVG circles.

    *events* is a list of kill event dicts with keys: x, y, t, killer_team.
    *version* is the DDragon version string for the map image URL.
    Returns the complete HTML container with optional time range scrubber.
    """
    safe_version = html.escape(version) if version else ""
    if safe_version:
        map_url = "https://ddragon.leagueoflegends.com/cdn/" + safe_version + "/img/map/map11.png"
        img_tag = (
            '<img src="' + map_url + '" alt="Summoner\'s Rift"'
            ' class="minimap__bg"'
            ' style="width:100%;height:100%;display:block"'
            ' loading="lazy">'
        )
    else:
        img_tag = (
            '<div class="minimap__bg"'
            ' style="width:100%;height:100%;background:var(--color-surface2)">'
            "</div>"
        )

    # Build SVG dots for valid events
    dots: list[str] = []
    max_time = 0
    for event in events:
        raw_x = event.get("x")
        raw_y = event.get("y")
        raw_t = event.get("t", 0)
        if not isinstance(raw_x, int) or not isinstance(raw_y, int):
            continue
        if not isinstance(raw_t, int):
            raw_t = 0
        max_time = max(max_time, raw_t)
        team = str(event.get("killer_team", "100"))
        color = _RED_HEX if team == "200" else _BLUE_HEX
        cx, cy = _normalize_map_coords(raw_x, raw_y)
        time_s = raw_t // 1000
        dot = (
            "<circle"
            ' cx="' + str(cx) + '%"'
            ' cy="' + str(cy) + '%"'
            ' r="5"'
            ' fill="' + color + '"'
            ' opacity="0.85"'
            ' stroke="#000" stroke-width="0.5"'
            ' data-time="' + str(time_s) + '"'
            "/>"
        )
        dots.append(dot)

    dots_str = "".join(dots)
    svg = (
        '<svg viewBox="0 0 100 100"'
        ' class="minimap__overlay"'
        ' style="position:absolute;top:0;left:0;width:100%;height:100%"'
        ' xmlns="http://www.w3.org/2000/svg"'
        ">" + dots_str + "</svg>"
    )

    # Scrubber: only show when there are events
    scrubber_html = ""
    if dots:
        max_sec = max(max_time // 1000, 1)
        scrubber_html = (
            '<div class="minimap__scrubber" style="margin-top:4px">'
            '<input type="range" min="0" max="' + str(max_sec) + '"'
            ' value="' + str(max_sec) + '"'
            ' class="minimap__range"'
            ' style="width:100%"'
            ' aria-label="Kill timeline scrubber">'
            "</div>"
            "<script>"
            "(function(){"
            "var c=document.querySelector('.minimap__range');"
            "if(!c)return;"
            "c.addEventListener('input',function(){"
            "var t=+this.value;"
            "var dots=document.querySelectorAll('.minimap__overlay circle');"
            "for(var i=0;i<dots.length;i++){"
            "dots[i].style.display=+dots[i].getAttribute('data-time')<=t?'':'none';"
            "}"
            "});"
            "})()"
            "</script>"
        )

    return (
        '<div class="minimap"'
        ' style="max-width:300px;width:100%;position:relative">'
        + img_tag
        + svg
        + scrubber_html
        + "</div>"
    )
