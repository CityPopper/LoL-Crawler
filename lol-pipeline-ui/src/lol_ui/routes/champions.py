"""Champions routes — GET /champions, GET /champions/{name}."""

from __future__ import annotations

import asyncio
import html

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from lol_ui.champions_helpers import (
    _build_champion_rows,
    _champion_detail_html,
    _champion_filter_html,
    _champion_tier_table,
    _fetch_champion_matchups,
    _fetch_patch_history,
    _matchup_table_html,
)
from lol_ui.constants import _CHAMPION_NAME_RE, _CHAMPION_ROLES_SET
from lol_ui.ddragon import _get_champion_id_map, _get_ddragon_version
from lol_ui.rendering import _empty_state, _page

router = APIRouter()


@router.get("/champions", response_class=HTMLResponse)
async def show_champions(request: Request) -> HTMLResponse:
    """Champion tier list page."""
    r: aioredis.Redis = request.app.state.r
    patches_raw: list[tuple[str, float]] = await r.zrevrange("patch:list", 0, 19, withscores=True)
    if not patches_raw:
        body = _empty_state(
            "No champion data yet",
            "Seed some players and wait for matches to be analyzed.",
        )
        return HTMLResponse(_page("Champions", body, path="/champions"))
    patch_list = [p for p, _s in patches_raw]
    patch = request.query_params.get("patch", "")
    if not patch or patch not in patch_list:
        patch = patch_list[0]
    role = request.query_params.get("role", "")
    if role and role not in _CHAMPION_ROLES_SET:
        role = ""
    # Fetch ban data and champion ID->name mapping concurrently
    ban_hash_coro = r.hgetall(f"champion:bans:{patch}")
    id_map_coro = _get_champion_id_map(r)
    ban_hash, champ_id_map = await asyncio.gather(ban_hash_coro, id_map_coro)  # type: ignore[arg-type]
    # Build reverse mapping: champion_name -> numeric_id
    name_to_id = {v: k for k, v in champ_id_map.items()}
    rows = await _build_champion_rows(
        r,
        patch,
        role,
        ban_hash=ban_hash,
        name_to_id=name_to_id,
    )
    # Fetch previous-patch rows for delta column
    patch_idx = patch_list.index(patch) if patch in patch_list else 0
    prev_rows: list[dict[str, object]] | None = None
    if patch_idx + 1 < len(patch_list):
        prev_patch = patch_list[patch_idx + 1]
        prev_ban_hash: dict[str, str] = await r.hgetall(f"champion:bans:{prev_patch}")  # type: ignore[misc]
        prev_rows = await _build_champion_rows(
            r,
            prev_patch,
            role,
            ban_hash=prev_ban_hash,
            name_to_id=name_to_id,
        )
    version = await _get_ddragon_version(r)
    filter_html = _champion_filter_html(patch_list, patch, role)
    table_html = _champion_tier_table(rows, patch, version, prev_rows=prev_rows)
    body = f"<h2>Champions &mdash; Patch {html.escape(patch)}</h2>\n{filter_html}\n{table_html}"
    return HTMLResponse(_page("Champions", body, path="/champions"))


@router.get("/champions/{name}", response_class=HTMLResponse)
async def show_champion_detail(request: Request, name: str) -> HTMLResponse:
    """Single champion detail page."""
    if not _CHAMPION_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid champion name")
    r: aioredis.Redis = request.app.state.r
    patches_raw: list[tuple[str, float]] = await r.zrevrange("patch:list", 0, 19, withscores=True)
    if not patches_raw:
        body = _empty_state(
            "No champion data yet",
            "Seed some players and wait for matches to be analyzed.",
        )
        return HTMLResponse(_page("Champion Detail", body, path="/champions"))
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
            f"No data for {html.escape(name)}",
            "This champion has no stats on this patch.",
        )
        return HTMLResponse(_page(f"{html.escape(name)}", body, path="/champions"))
    if not role or role not in all_roles:
        role = all_roles[0]
    # Fetch current stats
    stats: dict[str, str] = await r.hgetall(f"champion:stats:{name}:{patch}:{role}")  # type: ignore[misc]
    if not stats:
        stats = {}
    history = await _fetch_patch_history(r, name, role, patch_list)
    version = await _get_ddragon_version(r)
    matchups = await _fetch_champion_matchups(r, name, role, patch)
    mu_html = _matchup_table_html(matchups)
    detail = _champion_detail_html(
        name, role, stats, history, all_roles, version, matchups_html=mu_html
    )
    safe_name = html.escape(name)
    return HTMLResponse(_page(f"{safe_name} — Champions", detail, path="/champions"))
