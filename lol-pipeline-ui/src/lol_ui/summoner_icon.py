"""Summoner profile icon with level badge overlay."""

from __future__ import annotations

import html


def _summoner_icon_html(
    icon_id: str | None,
    level: str | None,
    version: str | None,
) -> str:
    """Render a DDragon profile icon with a level badge overlay.

    Falls back to a letter-circle placeholder when *icon_id* or *version*
    is unavailable.  The level badge is only shown when *level* is truthy.

    DDragon URL pattern:
    ``https://ddragon.leagueoflegends.com/cdn/{version}/img/profileicon/{icon_id}.png``
    """
    level_badge = ""
    if level:
        safe_level = html.escape(str(level))
        level_badge = (
            '<div style="position:absolute;bottom:-4px;left:50%;'
            "transform:translateX(-50%);"
            "background:var(--color-surface2);border:2px solid var(--color-win);"
            "border-radius:10px;padding:0 6px;"
            "font-size:11px;font-weight:700;color:var(--color-text);"
            'white-space:nowrap;line-height:18px">'
            f"{safe_level}</div>"
        )

    if not icon_id or not version:
        # Placeholder — empty circle with level badge
        return (
            '<div style="position:relative;display:inline-block">'
            '<div style="width:64px;height:64px;border-radius:50%;'
            "background:var(--color-surface2);"
            "display:flex;align-items:center;justify-content:center;"
            "border:3px solid var(--color-win);flex-shrink:0;"
            "font-family:var(--font-sans);font-size:28px;"
            'font-weight:700;color:var(--color-win)">?</div>'
            f"{level_badge}</div>"
        )

    safe_icon = html.escape(str(icon_id))
    safe_version = html.escape(str(version))
    url = f"https://ddragon.leagueoflegends.com/cdn/{safe_version}/img/profileicon/{safe_icon}.png"
    return (
        '<div style="position:relative;display:inline-block">'
        f'<img src="{url}" alt="Profile Icon"'
        ' style="width:64px;height:64px;border-radius:50%;'
        "border:3px solid var(--color-win);flex-shrink:0;"
        'object-fit:cover"'
        ' loading="lazy" onerror="this.style.display=\'none\'">'
        f"{level_badge}</div>"
    )
