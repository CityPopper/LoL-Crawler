"""Build tab — item purchase order, skill order grid, rune/spell integration.

Provides:
- ``_item_sequence_html(item_ids, version)`` — item icons in order with arrows
- ``_skill_cell(skill_slot, level)`` — single colored dot cell
- ``_skill_order_grid_html(skill_order)`` — 4-row x 18-column skill grid
- ``_build_tab_html(...)`` — full Build tab content
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any

from lol_ui.rendering import _champion_icon_html, _item_icon_html
from lol_ui.rune_display import _build_rune_lookup, _rune_page_html
from lol_ui.spell_display import _summoner_spell_icons_html
from lol_ui.strings import t


@dataclass(frozen=True, slots=True)
class BuildContext:
    """Shared rendering context for build tab components."""

    spell_map: dict[str, str]
    rune_lookup: dict[int, dict[str, str]]
    version: str | None
    current_puuid: str


# Skill slot colors: Q=blue, W=green, E=yellow, R=red
_SKILL_COLORS: dict[str, str] = {
    "Q": "var(--color-win)",
    "W": "var(--color-success)",
    "E": "var(--color-warning)",
    "R": "var(--color-loss)",
}

_SKILL_SLOTS = ["Q", "W", "E", "R"]

# R-unlock levels (0-indexed columns: 5, 10, 15 for levels 6, 11, 16)
_R_UNLOCK_LEVELS = frozenset({6, 11, 16})

_MAX_SKILL_LEVEL = 18


def _item_sequence_html(item_ids: list[str], version: str | None) -> str:
    """Render item icons in purchase order with arrow separators.

    Returns empty string when *item_ids* is empty.
    """
    if not item_ids:
        return ""
    parts: list[str] = []
    for i, item_id in enumerate(item_ids):
        parts.append(_item_icon_html(item_id, version))
        if i < len(item_ids) - 1:
            parts.append('<span class="build-arrow">\u2192</span>')
    return '<div class="build-sequence">' + "".join(parts) + "</div>"


def _final_items_html(
    participant: dict[str, str],
    version: str | None,
) -> str:
    """Render the 7-slot final items grid from participant hash."""
    raw_items = participant.get("items", "")
    try:
        if raw_items.startswith("["):
            item_list = json.loads(raw_items)
        else:
            item_list = raw_items.split(",") if raw_items else []
    except (json.JSONDecodeError, AttributeError):
        item_list = []
    item_ids = (list(map(str, item_list)) + ["0"] * 7)[:7]
    icons = "".join(_item_icon_html(iid, version) for iid in item_ids)
    return '<div class="build-final-items">' + icons + "</div>"


def _skill_cell(skill_slot: str, level: int) -> str:
    """Render a single skill grid cell — a colored dot if skill was taken.

    *skill_slot* is one of Q/W/E/R. *level* is the character level (1-18).
    The dot is colored per skill slot. Empty cells are transparent.
    This function renders the "taken" state — callers check whether to use it.
    """
    color = _SKILL_COLORS.get(skill_slot, "var(--color-muted)")
    highlight = " skill-cell--r-unlock" if level in _R_UNLOCK_LEVELS else ""
    return (
        '<td class="skill-cell' + highlight + '">'
        '<span class="skill-dot" style="background:' + color + '"></span>'
        "</td>"
    )


def _skill_empty_cell(level: int) -> str:
    """Render an empty skill grid cell (no skill taken at this level)."""
    highlight = " skill-cell--r-unlock" if level in _R_UNLOCK_LEVELS else ""
    return '<td class="skill-cell' + highlight + '"></td>'


def _skill_order_grid_html(skill_order: list[str]) -> str:
    """Render a 4-row x 18-column skill order grid.

    *skill_order* is a list of skill slot strings (e.g. ``["Q","W","E","Q",...]``)
    where index 0 = level 1. Max 18 entries.

    Returns an HTML table wrapped in ``.table-scroll``.
    Returns a placeholder message when *skill_order* is empty.
    """
    if not skill_order:
        return '<p class="warning">' + t("no_skill_data") + "</p>"

    # Cap at 18 levels
    order = skill_order[:_MAX_SKILL_LEVEL]

    # Build header row (level numbers)
    header = "<tr><th></th>"
    for level in range(1, _MAX_SKILL_LEVEL + 1):
        highlight = " skill-cell--r-unlock" if level in _R_UNLOCK_LEVELS else ""
        header += '<th class="skill-header' + highlight + '">' + str(level) + "</th>"
    header += "</tr>"

    # Build one row per skill slot
    rows: list[str] = []
    for slot in _SKILL_SLOTS:
        row = "<tr>"
        color = _SKILL_COLORS.get(slot, "var(--color-muted)")
        row += '<td class="skill-label" style="color:' + color + '">' + slot + "</td>"
        for level_idx in range(_MAX_SKILL_LEVEL):
            level = level_idx + 1
            if level_idx < len(order) and order[level_idx].upper() == slot:
                row += _skill_cell(slot, level)
            else:
                row += _skill_empty_cell(level)
        row += "</tr>"
        rows.append(row)

    table = (
        '<div class="table-scroll">'
        '<table class="skill-grid">' + header + "".join(rows) + "</table></div>"
    )
    return table


def _player_build_row_html(
    puuid: str,
    participant: dict[str, str],
    build_order: list[str],
    skill_order: list[str],
    ctx: BuildContext,
) -> str:
    """Render a single player's build section within the Build tab.

    Shows: champion icon + name, summoner spells, final items,
    build order (if available), skill order grid (if available), rune page.
    """
    is_me = puuid == ctx.current_puuid
    me_cls = " build-player--me" if is_me else ""
    champ = participant.get("champion_name", "?")
    icon = _champion_icon_html(champ, ctx.version)

    # Summoner spells
    spell1 = participant.get("summoner1_id", "0")
    spell2 = participant.get("summoner2_id", "0")
    spells_html = _summoner_spell_icons_html(spell1, spell2, ctx.spell_map, ctx.version)

    # Final items
    final_items = _final_items_html(participant, ctx.version)

    # Build order (only if we have timeline-derived data)
    build_html = ""
    if build_order:
        build_html = (
            '<div class="build-section">'
            '<div class="build-section__label">'
            + t("build_order")
            + "</div>"
            + _item_sequence_html(build_order, ctx.version)
            + "</div>"
        )

    # Skill order
    skill_html = ""
    if skill_order:
        skill_html = '<div class="build-section">' + _skill_order_grid_html(skill_order) + "</div>"

    # Rune page
    rune_html = _rune_page_html(participant, ctx.rune_lookup, ctx.version)
    if rune_html:
        rune_html = '<div class="build-section">' + rune_html + "</div>"

    return (
        '<div class="build-player' + me_cls + '">'
        '<div class="build-player__header">'
        + icon
        + '<span class="build-player__name">'
        + html.escape(champ)
        + "</span>"
        + spells_html
        + "</div>"
        + final_items
        + build_html
        + skill_html
        + rune_html
        + "</div>"
    )


def _build_tab_html(  # noqa: PLR0913
    blue_team: list[tuple[str, dict[str, str], dict[str, str], list[str]]],
    red_team: list[tuple[str, dict[str, str], dict[str, str], list[str]]],
    version: str | None,
    has_timeline: bool,
    current_puuid: str,
    spell_map: dict[str, str],
    runes_data: list[dict[str, Any]],
    skill_orders: dict[str, list[str]],
) -> str:
    """Render the full Build tab content.

    Parameters
    ----------
    blue_team, red_team:
        Each entry: (puuid, participant_hash, player_hash, build_order_list).
    version:
        DDragon version string.
    has_timeline:
        Whether timeline data was fetched (controls skill order display).
    current_puuid:
        The focused player's PUUID for highlighting.
    spell_map:
        {spell_id: image_filename} from DDragon summoner.json.
    runes_data:
        Parsed runesReforged.json array.
    skill_orders:
        {puuid: [skill_slot_per_level]} from Redis skill order keys.
    """
    rune_lookup = _build_rune_lookup(runes_data)
    ctx = BuildContext(
        spell_map=spell_map,
        rune_lookup=rune_lookup,
        version=version,
        current_puuid=current_puuid,
    )

    all_teams = [
        (t("blue_team"), "build-team--blue", blue_team),
        (t("red_team"), "build-team--red", red_team),
    ]

    parts: list[str] = ['<div class="build-tab">']
    for team_label, team_cls, team in all_teams:
        parts.append('<div class="build-team ' + team_cls + '">')
        parts.append('<div class="build-team__label">' + team_label + "</div>")
        for puuid, participant, _player, build_order in team:
            skill_order = skill_orders.get(puuid, [])
            if not has_timeline:
                # Without timeline, no skill order or build order is available
                skill_order = []
                build_order = []
            parts.append(_player_build_row_html(puuid, participant, build_order, skill_order, ctx))
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)
