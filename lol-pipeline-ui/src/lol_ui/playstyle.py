"""Playstyle tag computation and HTML rendering."""

from __future__ import annotations

import html

from lol_ui.constants import _PLAYSTYLE_MIN_GAMES


def _playstyle_tags(stats: dict[str, str]) -> list[tuple[str, str]]:
    """Compute threshold-based playstyle labels from aggregate player stats.

    Returns a list of ``(tag_name, css_color)`` tuples.  Tags are derived from
    the ``player:stats:{puuid}`` hash fields (avg_kills, avg_deaths,
    avg_assists, kda, win_rate).
    """
    games = int(stats.get("total_games", "0"))
    if games < _PLAYSTYLE_MIN_GAMES:
        return []

    try:
        avg_kills = float(stats.get("avg_kills", "0"))
        avg_deaths = float(stats.get("avg_deaths", "0"))
        avg_assists = float(stats.get("avg_assists", "0"))
        kda = float(stats.get("kda", "0"))
        win_rate = float(stats.get("win_rate", "0"))
    except (ValueError, TypeError):
        return []

    tags: list[tuple[str, str]] = []
    if avg_kills >= 8 or (avg_kills + avg_assists) >= 15:
        tags.append(("Aggressive", "#e84057"))
    if avg_assists >= 10:
        tags.append(("Team Fighter", "#5383e8"))
    if avg_deaths <= 3:
        tags.append(("Deathless", "#2ecc40"))
    if kda >= 4.0:
        tags.append(("KDA King", "#ffdc00"))
    if avg_kills >= 10:
        tags.append(("Slayer", "#ff6b35"))
    if win_rate >= 0.6:
        tags.append(("Winning Machine", "#9b59b6"))
    return tags


def _playstyle_pills_html(tags: list[tuple[str, str]]) -> str:
    """Render playstyle tags as colored pill badges."""
    if not tags:
        return ""
    pills = "".join(
        f'<span class="playstyle-pill" style="background:{color};color:#111">'
        f"{html.escape(name)}</span>"
        for name, color in tags
    )
    return f'<div class="playstyle-pills">{pills}</div>'
