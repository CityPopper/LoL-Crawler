"""Playstyle tag computation and HTML rendering."""

from __future__ import annotations

import html

from lol_ui._helpers import _safe_int
from lol_ui.constants import _PLAYSTYLE_MIN_GAMES, _PLAYSTYLE_RULES


def _playstyle_tags(stats: dict[str, str]) -> list[tuple[str, str]]:
    """Compute threshold-based playstyle labels from aggregate player stats.

    Returns a list of ``(tag_name, css_color)`` tuples.  Tags are derived from
    the ``player:stats:{puuid}`` hash fields (avg_kills, avg_deaths,
    avg_assists, kda, win_rate).

    Rules are defined in ``_PLAYSTYLE_RULES`` — each entry maps a stat field
    to a comparison operator and threshold.  The synthetic field ``ka_sum``
    is the sum of ``avg_kills + avg_assists``.
    """
    games = _safe_int(stats.get("total_games", "0"))
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

    values: dict[str, float] = {
        "avg_kills": avg_kills,
        "avg_deaths": avg_deaths,
        "avg_assists": avg_assists,
        "kda": kda,
        "win_rate": win_rate,
        "ka_sum": avg_kills + avg_assists,
    }

    seen: set[str] = set()
    tags: list[tuple[str, str]] = []
    for name, color, field, op, threshold in _PLAYSTYLE_RULES:
        if name in seen:
            continue
        val = values.get(field, 0.0)
        matched = (op == "ge" and val >= threshold) or (op == "le" and val <= threshold)
        if matched:
            tags.append((name, color))
            seen.add(name)
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
