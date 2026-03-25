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
        level_badge = f'<div class="level-badge">{safe_level}</div>'

    if not icon_id or not version:
        # Placeholder — empty circle with level badge
        return f'<div class="avatar-wrap"><div class="avatar-circle">?</div>{level_badge}</div>'

    safe_icon = html.escape(str(icon_id))
    safe_version = html.escape(str(version))
    url = f"https://ddragon.leagueoflegends.com/cdn/{safe_version}/img/profileicon/{safe_icon}.png"
    return (
        f'<div class="avatar-wrap">'
        f'<img src="{url}" alt="Profile Icon"'
        f' class="summoner-icon"'
        f' loading="lazy" onerror="this.style.display=\'none\'">'
        f"{level_badge}</div>"
    )
