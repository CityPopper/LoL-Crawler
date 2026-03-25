"""Champions routes — GET /champions, GET /champions/{name}."""

from __future__ import annotations

import asyncio
import html

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from lol_ui.champions_helpers import (
    _build_champion_rows,
    _champion_builds_html,
    _champion_detail_html,
    _champion_filter_html,
    _champion_tier_table,
    _fetch_champion_builds,
    _fetch_champion_matchups,
    _fetch_patch_history,
    _matchup_table_html,
)
from lol_ui.constants import _CHAMPION_NAME_RE, _CHAMPION_ROLES_SET
from lol_ui.ddragon import (
    _get_champion_id_map,
    _get_ddragon_version,
    get_champion_name_map,
    localize_champion_name,
)
from lol_ui.language import _current_lang
from lol_ui.rendering import _empty_state, _page
from lol_ui.strings import t

router = APIRouter()


@router.get("/champions", response_class=HTMLResponse)
async def show_champions(request: Request) -> HTMLResponse:
    """Champion tier list page."""
    r: aioredis.Redis = request.app.state.r
    lang = _current_lang.get()
    patches_raw: list[tuple[str, float]] = await r.zrevrange("patch:list", 0, 19, withscores=True)
    if not patches_raw:
        body = _empty_state(
            t("champions_no_data"),
            t("champions_no_data_hint"),
        )
        return HTMLResponse(_page(t("page_champions"), body, path="/champions"))
    patch_list = [p for p, _s in patches_raw]
    patch = request.query_params.get("patch", "")
    if not patch or patch not in patch_list:
        patch = patch_list[0]
    role = request.query_params.get("role", "")
    if role and role not in _CHAMPION_ROLES_SET:
        role = ""

    # Fetch ban data, champion ID->name mapping, and localized name map concurrently
    async def _bans() -> dict[str, str]:
        result: dict[str, str] = await r.hgetall(f"champion:bans:{patch}")  # type: ignore[misc]
        return result

    ban_hash, champ_id_map, champ_name_map = await asyncio.gather(
        _bans(),
        _get_champion_id_map(r),
        get_champion_name_map(r, lang),
    )
    # Build reverse mapping: champion_name -> numeric_id
    name_to_id = {v: k for k, v in champ_id_map.items()}
    # Fetch current rows, previous-patch rows, and DDragon version concurrently
    patch_idx = patch_list.index(patch) if patch in patch_list else 0
    has_prev = patch_idx + 1 < len(patch_list)

    async def _current_rows() -> list[dict[str, object]]:
        return await _build_champion_rows(
            r,
            patch,
            role,
            ban_hash=ban_hash,
            name_to_id=name_to_id,
        )

    async def _prev_rows() -> list[dict[str, object]] | None:
        if not has_prev:
            return None
        prev_patch = patch_list[patch_idx + 1]
        prev_ban_hash: dict[str, str] = await r.hgetall(f"champion:bans:{prev_patch}")  # type: ignore[misc]
        return await _build_champion_rows(
            r,
            prev_patch,
            role,
            ban_hash=prev_ban_hash,
            name_to_id=name_to_id,
        )

    rows, prev_rows, version = await asyncio.gather(
        _current_rows(),
        _prev_rows(),
        _get_ddragon_version(r),
    )
    filter_html = _champion_filter_html(patch_list, patch, role)
    table_html = _champion_tier_table(
        rows, patch, version, prev_rows=prev_rows, name_map=champ_name_map
    )
    body = (
        f"<h2>{t('champions_patch_prefix')} {html.escape(patch)}</h2>\n{filter_html}\n{table_html}"
    )
    return HTMLResponse(_page(t("page_champions"), body, path="/champions"))


@router.get("/champions/{name}", response_class=HTMLResponse)
async def show_champion_detail(request: Request, name: str) -> HTMLResponse:
    """Single champion detail page."""
    if not _CHAMPION_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid champion name")
    r: aioredis.Redis = request.app.state.r
    lang = _current_lang.get()
    patches_raw: list[tuple[str, float]] = await r.zrevrange("patch:list", 0, 19, withscores=True)
    if not patches_raw:
        body = _empty_state(
            t("champions_no_data"),
            t("champions_no_data_hint"),
        )
        return HTMLResponse(_page(t("page_champion_detail"), body, path="/champions"))
    patch_list = [p for p, _s in patches_raw]
    patch = request.query_params.get("patch", "")
    if not patch or patch not in patch_list:
        patch = patch_list[0]
    role = request.query_params.get("role", "")
    # Determine all roles this champion appears in on this patch
    index_key = f"champion:index:{patch}"
    all_members: list[tuple[str, float]] = await r.zrevrange(index_key, 0, -1, withscores=True)
    all_roles = [m.rsplit(":", 1)[1] for m, _s in all_members if m.rsplit(":", 1)[0] == name]
    if not all_roles:
        body = _empty_state(
            f"{t('champions_no_data_for')} {html.escape(name)}",
            t("champions_no_stats_hint"),
        )
        return HTMLResponse(_page(f"{html.escape(name)}", body, path="/champions"))
    if not role or role not in all_roles:
        role = all_roles[0]

    # Fetch stats, patch history, DDragon version, matchups, builds, names concurrently
    async def _get_stats() -> dict[str, str]:
        result: dict[str, str] = await r.hgetall(  # type: ignore[misc]
            f"champion:stats:{name}:{patch}:{role}"
        )
        return result or {}

    stats, history, version, matchups, builds_tuple, champ_name_map = await asyncio.gather(
        _get_stats(),
        _fetch_patch_history(r, name, role, patch_list),
        _get_ddragon_version(r),
        _fetch_champion_matchups(r, name, role, patch),
        _fetch_champion_builds(r, name, patch, role),
        get_champion_name_map(r, lang),
    )
    top_builds, top_keystones, top_spells = builds_tuple
    mu_html = _matchup_table_html(matchups, name_map=champ_name_map)
    builds_html = _champion_builds_html(top_builds, top_keystones, top_spells, version)
    detail = _champion_detail_html(
        name,
        role,
        stats,
        history,
        all_roles,
        version,
        matchups_html=mu_html,
        name_map=champ_name_map,
        builds_html=builds_html,
    )
    display_name = html.escape(localize_champion_name(champ_name_map, name))
    return HTMLResponse(
        _page(f"{display_name} \u2014 {t('page_champions')}", detail, path="/champions")
    )
