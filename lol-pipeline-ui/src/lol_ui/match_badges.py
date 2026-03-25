"""Match badge computation and rendering."""

from __future__ import annotations

import html

from lol_ui._helpers import _kda
from lol_ui.constants import (
    _BADGE_CS_MIN_TIME_PLAYED,
    _BADGE_CS_PER_MIN_THRESHOLD,
    _BADGE_KDA_THRESHOLD,
    _BADGE_PENTA_MIN,
    _MATCH_BADGE_COLORS,
)


def _match_badges(participant: dict[str, str]) -> list[tuple[str, str]]:
    """Return a list of (badge_name, color_key) for notable match achievements.

    All participant values are strings from Redis hashes.
    Badge thresholds are defined in ``constants.py`` as ``_BADGE_*`` constants.
    """
    badges: list[tuple[str, str]] = []
    try:
        kills = int(participant.get("kills", "0"))
        deaths = int(participant.get("deaths", "0"))
        assists = int(participant.get("assists", "0"))
    except ValueError:
        return badges

    win = participant.get("win") == "1"

    # Deathless: 0 deaths AND a win
    if deaths == 0 and win:
        badges.append(("Deathless", "gold"))

    # Penta Kill
    try:
        penta = int(participant.get("penta_kills", "0"))
    except ValueError:
        penta = 0
    if penta >= _BADGE_PENTA_MIN:
        badges.append(("PENTA", "red"))

    # High KDA: (kills + assists) / max(deaths, 1) >= threshold
    kda = _kda(kills, deaths, assists)
    if kda >= _BADGE_KDA_THRESHOLD:
        badges.append(("KDA 5+", "green"))

    # CS Machine: cs_per_min >= threshold (only for games >= min time)
    try:
        total_cs = int(participant.get("total_minions_killed", "0"))
        neutral = int(participant.get("neutral_minions", "0"))
        time_played = int(participant.get("time_played", "0"))
    except ValueError:
        total_cs = neutral = time_played = 0
    if time_played >= _BADGE_CS_MIN_TIME_PLAYED:
        cs_per_min = (total_cs + neutral) / (time_played / 60)
        if cs_per_min >= _BADGE_CS_PER_MIN_THRESHOLD:
            badges.append(("CS 8+/m", "blue"))

    return badges


def _match_badges_html(badges: list[tuple[str, str]]) -> str:
    """Render badge pills as HTML spans."""
    if not badges:
        return ""
    parts = ""
    for name, color_key in badges:
        bg, fg = _MATCH_BADGE_COLORS.get(color_key, ("#666", "#fff"))
        parts += (
            f'<span class="match-badge" style="background:{bg};color:{fg}">'
            f"{html.escape(name)}</span>"
        )
    return f'<div class="match-badges">{parts}</div>'
