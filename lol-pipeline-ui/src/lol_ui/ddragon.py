"""Data Dragon (DDragon) — version, champion ID/name maps, and generic JSON cache helper."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.i18n import DDRAGON_LOCALE_MAP

from lol_ui._helpers import _validate_ddragon_version

_log = logging.getLogger("ui.ddragon")

_DDRAGON_VERSION_KEY = "ddragon:version"
_DDRAGON_CHAMPION_IDS_KEY = "ddragon:champion_ids"
_DDRAGON_CHAMPION_NAMES_KEY_PREFIX = "ddragon:champion_names"
_DDRAGON_TTL_S = 86400  # 24 hours
_DDRAGON_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB

# zh_CN champion.json uses "name" for the localized display name,
# while Western locales use "name" for the English display name.
# The "id" field is always the English programmatic ID (e.g. "MonkeyKing").
_ZH_CN_NAME_FIELD = "name"
_WESTERN_NAME_FIELD = "name"

# In-memory cache: {key: (data, expiry_timestamp)}
_mem_cache: dict[str, tuple[Any, float]] = {}

# Lazy-loaded config singleton for ddragon_timeout_s
_cfg: Config | None = None


_DDRAGON_TIMEOUT_DEFAULT = 5.0


def _get_timeout() -> float:
    """Return the DDragon HTTP timeout from Config (lazy-loaded)."""
    global _cfg
    if _cfg is None:
        try:
            _cfg = Config()
        except Exception:
            return _DDRAGON_TIMEOUT_DEFAULT
    return _cfg.ddragon_timeout_s


def _mem_get(key: str) -> Any | None:
    """Read from in-memory cache, returning None if missing or expired."""
    entry = _mem_cache.get(key)
    if entry is None:
        return None
    data, expiry = entry
    if time.monotonic() > expiry:
        _mem_cache.pop(key, None)
        return None
    return data


def _mem_put(key: str, data: Any, ttl: int = _DDRAGON_TTL_S) -> None:
    """Store data in the in-memory cache with a TTL."""
    _mem_cache[key] = (data, time.monotonic() + ttl)


async def _get_ddragon_json(
    r: aioredis.Redis,
    cache_key: str,
    url: str,
    ttl: int = _DDRAGON_TTL_S,
) -> Any:
    """Fetch a DDragon JSON resource with read-only Redis + in-memory caching.

    Checks in-memory cache first, then Redis *cache_key*.  On miss, fetches
    *url* via HTTP, validates the response size (max 5 MB), stores in memory
    with *ttl* seconds, and returns the parsed object.

    Returns ``None`` on any failure (network, size, parse).
    """
    mem = _mem_get(cache_key)
    if mem is not None:
        return mem
    cached = await r.get(cache_key)
    if cached:
        try:
            data = json.loads(str(cached))
            _mem_put(cache_key, data, ttl)
            return data
        except (json.JSONDecodeError, TypeError):
            pass
    try:
        async with httpx.AsyncClient(timeout=_get_timeout()) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            if len(resp.content) > _DDRAGON_MAX_RESPONSE_BYTES:
                return None
            data = resp.json()
            _mem_put(cache_key, data, ttl)
            return data
    except Exception:
        _log.warning("DDragon fetch failed", extra={"url": url}, exc_info=True)
        return None


async def _get_ddragon_version(r: aioredis.Redis) -> str | None:
    """Return the current Data Dragon version, cached in-memory then Redis for 24h.

    Validates version format (``X.Y.Z``) before accepting.
    """
    mem = _mem_get(_DDRAGON_VERSION_KEY)
    if mem is not None and _validate_ddragon_version(str(mem)):
        return str(mem)
    cached = await r.get(_DDRAGON_VERSION_KEY)
    if cached:
        version = str(cached)
        if _validate_ddragon_version(version):
            _mem_put(_DDRAGON_VERSION_KEY, version)
            return version
    try:
        async with httpx.AsyncClient(timeout=_get_timeout()) as client:
            resp = await client.get("https://ddragon.leagueoflegends.com/api/versions.json")
            resp.raise_for_status()
            if len(resp.content) > _DDRAGON_MAX_RESPONSE_BYTES:
                return None
            versions: list[str] = resp.json()
            version = versions[0]
            if not _validate_ddragon_version(version):
                return None
            _mem_put(_DDRAGON_VERSION_KEY, version)
            return version
    except Exception:
        _log.warning("DDragon version fetch failed", exc_info=True)
        return None


async def _get_champion_id_map(r: aioredis.Redis) -> dict[str, str]:
    """Return {champion_numeric_id: champion_name} mapping from Data Dragon.

    Cached in-memory (with Redis read fallback) for 24h. Returns empty dict on failure.
    """
    mem = _mem_get(_DDRAGON_CHAMPION_IDS_KEY)
    if mem is not None and isinstance(mem, dict):
        return mem
    cached = await r.get(_DDRAGON_CHAMPION_IDS_KEY)
    if cached:
        try:
            mapping = json.loads(str(cached))
            _mem_put(_DDRAGON_CHAMPION_IDS_KEY, mapping)
            return mapping  # type: ignore[no-any-return]
        except (json.JSONDecodeError, TypeError):
            pass
    version = await _get_ddragon_version(r)
    if not version:
        return {}
    url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
    data = await _get_ddragon_json(r, _DDRAGON_CHAMPION_IDS_KEY, url)
    if not data:
        return {}
    mapping: dict[str, str] = {}
    for champ_data in data.get("data", {}).values():
        key = champ_data.get("key", "")
        name = champ_data.get("id", "")
        if key and name:
            mapping[key] = name
    _mem_put(_DDRAGON_CHAMPION_IDS_KEY, mapping)
    return mapping


async def get_champion_name_map(
    r: aioredis.Redis,
    lang: str = "en",
) -> dict[str, str]:
    """Return ``{english_champion_id: localized_display_name}`` mapping.

    Cache key: ``ddragon:champion_names:{ddragon_locale}`` with 24h TTL.
    For ``zh_CN`` the ``name`` field contains the Chinese display name.
    For Western locales the ``name`` field is the English display name.
    Falls back to English on failure.
    """
    ddragon_locale = DDRAGON_LOCALE_MAP.get(lang, "en_US")
    cache_key = f"{_DDRAGON_CHAMPION_NAMES_KEY_PREFIX}:{ddragon_locale}"
    mem = _mem_get(cache_key)
    if mem is not None and isinstance(mem, dict):
        return mem
    cached = await r.get(cache_key)
    if cached:
        try:
            mapping = json.loads(str(cached))
            _mem_put(cache_key, mapping)
            return mapping  # type: ignore[no-any-return]
        except (json.JSONDecodeError, TypeError):
            pass
    version = await _get_ddragon_version(r)
    if not version:
        return {}
    url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/{ddragon_locale}/champion.json"
    data = await _get_ddragon_json(r, f"_tmp:{cache_key}", url)
    if not data:
        # Fall back to English if a non-English locale failed
        if ddragon_locale != "en_US":
            return await get_champion_name_map(r, "en")
        return {}
    name_field = _ZH_CN_NAME_FIELD if ddragon_locale == "zh_CN" else _WESTERN_NAME_FIELD
    mapping: dict[str, str] = {}
    for champ_data in data.get("data", {}).values():
        champ_id = champ_data.get("id", "")
        display_name = champ_data.get(name_field, champ_id)
        if champ_id:
            mapping[champ_id] = display_name
    _mem_put(cache_key, mapping)
    return mapping


def localize_champion_name(
    name_map: dict[str, str],
    champion_id: str,
) -> str:
    """Look up the localized display name for *champion_id*.

    Returns *champion_id* unchanged when the map has no entry (safe fallback).
    """
    return name_map.get(champion_id, champion_id)
