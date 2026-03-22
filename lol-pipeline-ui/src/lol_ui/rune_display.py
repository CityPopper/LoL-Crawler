"""Rune page rendering with DDragon icons and stat shard labels."""

from __future__ import annotations

import html
import json
from typing import Any

import redis.asyncio as aioredis

from lol_ui.ddragon import _DDRAGON_TTL_S, _get_ddragon_json, _get_ddragon_version
from lol_ui.strings import t

_DDRAGON_RUNES_KEY = "ddragon:runes"

# Stat shard IDs are NOT in runesReforged.json — hardcoded mapping required.
_STAT_SHARD_LABELS: dict[str, str] = {
    "5001": "+15-140 Health",
    "5002": "+6 Armor",
    "5003": "+8 Magic Resist",
    "5005": "+10% Attack Speed",
    "5007": "+8 Ability Haste",
    "5008": "+9 Adaptive Force",
    "5010": "+16% Tenacity/Slow Resist",
    "5011": "+65 Health",
    "5013": "+10% Tenacity/Slow Resist",
}


async def _get_runes_data(
    r: aioredis.Redis,
) -> list[dict[str, Any]]:
    """Return runesReforged.json data, cached in Redis for 24h.

    Returns empty list on failure.
    """
    version = await _get_ddragon_version(r)
    if not version:
        return []
    url = "https://ddragon.leagueoflegends.com/cdn/" + version + "/data/en_US/runesReforged.json"
    data = await _get_ddragon_json(r, _DDRAGON_RUNES_KEY, url, ttl=_DDRAGON_TTL_S)
    if isinstance(data, list):
        return data
    return []


def _build_rune_lookup(
    runes_data: list[dict[str, Any]],
) -> dict[int, dict[str, str]]:
    """Build a flat {perk_id: {name, icon, tree}} lookup from runesReforged.json.

    Each entry includes:
    - ``name``: rune display name
    - ``icon``: DDragon icon path (e.g. ``perk-images/Styles/Domination/...``)
    - ``tree``: tree name (e.g. ``Domination``)
    - ``is_keystone``: ``"1"`` for keystones, ``"0"`` for other runes
    """
    lookup: dict[int, dict[str, str]] = {}
    for tree in runes_data:
        tree_name = tree.get("name", "")
        tree_icon = tree.get("icon", "")
        tree_id = tree.get("id", 0)
        # Add tree itself so we can look up the path icon
        lookup[int(tree_id)] = {
            "name": tree_name,
            "icon": tree_icon,
            "tree": tree_name,
            "is_keystone": "0",
        }
        for slot_idx, slot in enumerate(tree.get("slots", [])):
            for rune in slot.get("runes", []):
                rune_id = int(rune.get("id", 0))
                lookup[rune_id] = {
                    "name": rune.get("name", ""),
                    "icon": rune.get("icon", ""),
                    "tree": tree_name,
                    "is_keystone": "1" if slot_idx == 0 else "0",
                }
    return lookup


def _rune_icon_html(
    perk_id: int,
    lookup: dict[int, dict[str, str]],
    version: str | None,
    *,
    large: bool = False,
) -> str:
    """Render a single rune icon <img> tag.

    *large* renders at 36px (keystone), otherwise 28px.
    Falls back to the perk name as text when icon is unavailable.
    """
    info = lookup.get(perk_id)
    if not info or not version:
        return '<span class="rune-icon rune-icon--empty"></span>'
    icon_path = info.get("icon", "")
    rune_name = html.escape(info.get("name", ""))
    if not icon_path:
        return '<span class="rune-icon" title="' + rune_name + '">' + rune_name + "</span>"
    safe_icon = html.escape(icon_path)
    url = "https://ddragon.leagueoflegends.com/cdn/img/" + safe_icon
    size_cls = "rune-icon--lg" if large else "rune-icon"
    return (
        '<img src="' + url + '"'
        ' alt="' + rune_name + '"'
        ' class="' + size_cls + '"'
        ' title="' + rune_name + '"'
        ' loading="lazy"'
        " onerror=\"this.style.display='none'\">"
    )


def _stat_shard_html(shard_id: str) -> str:
    """Render a stat shard as a text label."""
    label = _STAT_SHARD_LABELS.get(str(shard_id), "Shard " + html.escape(str(shard_id)))
    return '<span class="rune-shard">' + html.escape(label) + "</span>"


def _secondary_path_html(
    sub_style: str,
    sub_selections: list[int],
    lookup: dict[int, dict[str, str]],
    version: str | None,
) -> str:
    """Render the secondary rune path section. Returns empty string if absent."""
    if not sub_style and not sub_selections:
        return ""
    parts: list[str] = ['<div class="rune-path rune-path--secondary">']
    if sub_style:
        tree_info = lookup.get(int(sub_style))
        tree_label = html.escape(tree_info["name"]) if tree_info else ""
        parts.append('<div class="rune-path__label">' + tree_label + "</div>")
    if sub_selections:
        parts.append('<div class="rune-path__selections">')
        for perk_id in sub_selections:
            parts.append(_rune_icon_html(perk_id, lookup, version))
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def _rune_page_html(
    participant: dict[str, str],
    lookup: dict[int, dict[str, str]],
    version: str | None,
) -> str:
    """Render the full rune page for a participant.

    Reads from participant hash fields:
    - ``perk_keystone``: keystone perk ID (always present)
    - ``perk_primary_style``: primary tree ID
    - ``perk_sub_style``: secondary tree ID
    - ``perk_primary_selections``: JSON array of primary perk IDs (from T1-3)
    - ``perk_sub_selections``: JSON array of secondary perk IDs (from T1-3)
    - ``perk_stat_shards``: JSON array of stat shard IDs (from T1-3)

    Degrades gracefully: shows keystone-only when full data is absent.
    """
    keystone_id = participant.get("perk_keystone", "")
    primary_style = participant.get("perk_primary_style", "")
    sub_style = participant.get("perk_sub_style", "")

    if not keystone_id:
        return ""

    # Parse extended perk data (T1-3 fields)
    primary_selections = _parse_int_list(participant.get("perk_primary_selections", ""))
    sub_selections = _parse_int_list(participant.get("perk_sub_selections", ""))
    stat_shards = _parse_str_list(participant.get("perk_stat_shards", ""))

    has_full_data = bool(primary_selections or sub_selections)

    # Keystone (always shown, large)
    keystone_html = _rune_icon_html(int(keystone_id), lookup, version, large=True)

    parts: list[str] = ['<div class="rune-page">']

    # Primary path
    parts.append('<div class="rune-path rune-path--primary">')
    if primary_style:
        tree_info = lookup.get(int(primary_style))
        tree_label = html.escape(tree_info["name"]) if tree_info else t("build")
        parts.append('<div class="rune-path__label">' + tree_label + "</div>")
    parts.append('<div class="rune-path__keystone">' + keystone_html + "</div>")
    if has_full_data and primary_selections:
        parts.append('<div class="rune-path__selections">')
        for perk_id in primary_selections:
            parts.append(_rune_icon_html(perk_id, lookup, version))
        parts.append("</div>")
    parts.append("</div>")

    # Secondary path + stat shards
    parts.append(_secondary_path_html(sub_style, sub_selections, lookup, version))

    if stat_shards:
        parts.append('<div class="rune-shards">')
        for shard_id in stat_shards:
            parts.append(_stat_shard_html(shard_id))
        parts.append("</div>")

    parts.append("</div>")
    return "".join(parts)


def _parse_int_list(raw: str) -> list[int]:
    """Parse a JSON array string into a list of ints. Returns [] on failure."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [int(x) for x in data]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


def _parse_str_list(raw: str) -> list[str]:
    """Parse a JSON array string into a list of strings. Returns [] on failure."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x) for x in data]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return []
