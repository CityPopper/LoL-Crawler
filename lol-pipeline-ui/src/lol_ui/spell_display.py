"""Summoner spell icon rendering using DDragon data."""

from __future__ import annotations

import html
import json
from typing import Any

import redis.asyncio as aioredis

from lol_ui.ddragon import (
    _DDRAGON_TTL_S,
    _get_ddragon_json,
    _get_ddragon_version,
    _mem_get,
    _mem_put,
)

_DDRAGON_SUMMONERS_KEY = "ddragon:summoners"


async def _get_summoner_spell_map(
    r: aioredis.Redis,
) -> dict[str, str]:
    """Return {spell_numeric_id: spell_image_filename} from DDragon.

    Cached in-memory (with Redis read fallback) for 24h. Returns empty dict on failure.
    """
    mem = _mem_get(_DDRAGON_SUMMONERS_KEY)
    if mem is not None and isinstance(mem, dict):
        return mem
    cached_map = await r.get(_DDRAGON_SUMMONERS_KEY)
    if cached_map:
        try:
            mapping = json.loads(str(cached_map))
            _mem_put(_DDRAGON_SUMMONERS_KEY, mapping)
            return mapping  # type: ignore[no-any-return]
        except (json.JSONDecodeError, TypeError):
            pass
    version = await _get_ddragon_version(r)
    if not version:
        return {}
    url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/summoner.json"
    data: dict[str, Any] | None = await _get_ddragon_json(
        r, "_tmp:summoner_raw", url, ttl=_DDRAGON_TTL_S
    )
    if not data:
        return {}
    mapping: dict[str, str] = {}
    for spell_data in data.get("data", {}).values():
        key = str(spell_data.get("key", ""))
        image_file = spell_data.get("image", {}).get("full", "")
        if key and image_file:
            mapping[key] = image_file
    _mem_put(_DDRAGON_SUMMONERS_KEY, mapping)
    return mapping


def _summoner_spell_icon_html(
    spell_id: str,
    spell_map: dict[str, str],
    version: str | None,
) -> str:
    """Render a single 28px summoner spell icon.

    Returns an empty-slot span when spell data is unavailable.
    """
    if not version or not spell_id or spell_id == "0":
        return '<span class="spell-icon spell-icon--empty"></span>'
    image_file = spell_map.get(str(spell_id), "")
    if not image_file:
        return '<span class="spell-icon spell-icon--empty"></span>'
    safe_v = html.escape(version)
    safe_img = html.escape(image_file)
    url = "https://ddragon.leagueoflegends.com/cdn/" + safe_v + "/img/spell/" + safe_img
    return (
        '<img src="' + url + '"'
        ' alt="spell"'
        ' class="spell-icon"'
        ' loading="lazy"'
        " onerror=\"this.style.display='none'\">"
    )


def _summoner_spell_icons_html(
    spell1_id: str,
    spell2_id: str,
    spell_map: dict[str, str],
    version: str | None,
) -> str:
    """Render two 28px summoner spell icons side by side."""
    icon1 = _summoner_spell_icon_html(spell1_id, spell_map, version)
    icon2 = _summoner_spell_icon_html(spell2_id, spell_map, version)
    return '<div class="spell-pair">' + icon1 + icon2 + "</div>"
