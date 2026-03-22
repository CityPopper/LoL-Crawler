"""Data Dragon (champion icons) — version + champion-ID-map helpers."""

from __future__ import annotations

import json
from typing import Any

import httpx
import redis.asyncio as aioredis

_DDRAGON_VERSION_KEY = "ddragon:version"
_DDRAGON_CHAMPION_IDS_KEY = "ddragon:champion_ids"
_DDRAGON_TTL_S = 86400  # 24 hours


async def _get_ddragon_version(r: aioredis.Redis) -> str | None:
    """Return the current Data Dragon version, cached in Redis for 24h."""
    cached = await r.get(_DDRAGON_VERSION_KEY)
    if cached:
        return str(cached)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://ddragon.leagueoflegends.com/api/versions.json")
            resp.raise_for_status()
            versions: list[str] = resp.json()
            version = versions[0]
            await r.set(_DDRAGON_VERSION_KEY, version, ex=_DDRAGON_TTL_S)
            return version
    except Exception:
        return None


async def _get_champion_id_map(r: aioredis.Redis) -> dict[str, str]:
    """Return {champion_numeric_id: champion_name} mapping from Data Dragon.

    Cached in Redis for 24h. Returns empty dict on failure.
    """
    cached = await r.get(_DDRAGON_CHAMPION_IDS_KEY)
    if cached:
        return json.loads(str(cached))  # type: ignore[no-any-return]
    version = await _get_ddragon_version(r)
    if not version:
        return {}
    try:
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            mapping: dict[str, str] = {}
            for champ_data in data.get("data", {}).values():
                key = champ_data.get("key", "")
                name = champ_data.get("id", "")
                if key and name:
                    mapping[key] = name
            await r.set(
                _DDRAGON_CHAMPION_IDS_KEY,
                json.dumps(mapping),
                ex=_DDRAGON_TTL_S,
            )
            return mapping
    except Exception:
        return {}
