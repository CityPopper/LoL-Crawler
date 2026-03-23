"""Matchups route — GET /matchups."""

from __future__ import annotations

import html

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from lol_ui._helpers import _safe_int
from lol_ui.constants import _CHAMPION_NAME_RE, _MATCHUP_ROLES, _PATCH_RE
from lol_ui.rendering import _empty_state, _page

router = APIRouter()


@router.get("/matchups", response_class=HTMLResponse)
async def show_matchups(request: Request) -> HTMLResponse:
    """Champion matchup lookup page."""
    r: aioredis.Redis = request.app.state.r
    champ_a = request.query_params.get("champ_a", "")
    champ_b = request.query_params.get("champ_b", "")
    role = request.query_params.get("role", "")
    patch = request.query_params.get("patch", "")

    # Validate inputs to prevent Redis key injection
    if champ_a and not _CHAMPION_NAME_RE.match(champ_a):
        raise HTTPException(status_code=400, detail="Invalid champion name")
    if champ_b and not _CHAMPION_NAME_RE.match(champ_b):
        raise HTTPException(status_code=400, detail="Invalid champion name")
    if role and role not in _MATCHUP_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    if patch and not _PATCH_RE.match(patch):
        raise HTTPException(status_code=400, detail="Invalid patch format")

    if not champ_a or not champ_b:
        body = """<h2>Champion Matchups</h2>
<form class="form-inline" method="get" action="/matchups">
  <label for="matchup-a">Champion A:</label>
  <input id="matchup-a" name="champ_a" placeholder="e.g. Jinx" required>
  <label for="matchup-b">Champion B:</label>
  <input id="matchup-b" name="champ_b" placeholder="e.g. Caitlyn" required>
  <label for="matchup-role">Role:</label>
  <select id="matchup-role" name="role">
    <option value="TOP">Top</option>
    <option value="JUNGLE">Jungle</option>
    <option value="MIDDLE">Mid</option>
    <option value="BOTTOM">Bot</option>
    <option value="UTILITY">Support</option>
  </select>
  <label for="matchup-patch">Patch (optional):</label>
  <input id="matchup-patch" name="patch" placeholder="e.g. 14.5">
  <button type="submit">Compare</button>
</form>"""
        return HTMLResponse(_page("Matchups", body, path="/matchups"))

    # Resolve current patch if not provided
    if not patch:
        patches_raw: list[tuple[str, float]] = await r.zrevrange(
            "patch:list", 0, 0, withscores=True
        )
        patch = patches_raw[0][0] if patches_raw else ""

    if not patch:
        body = _empty_state(
            "No patch data",
            "No patches found. Seed players and wait for analysis.",
        )
        return HTMLResponse(_page("Matchups", body, path="/matchups"))

    key = f"matchup:{champ_a}:{champ_b}:{role}:{patch}"
    data: dict[str, str] = await r.hgetall(key)  # type: ignore[misc]

    if not data:
        safe_a = html.escape(champ_a)
        safe_b = html.escape(champ_b)
        safe_role = html.escape(role)
        body = _empty_state(
            "No matchup data",
            f"No games found for {safe_a} vs {safe_b} as {safe_role}.",
        )
        return HTMLResponse(_page("Matchups", body, path="/matchups"))

    games = _safe_int(data.get("games", "0"))
    wins = _safe_int(data.get("wins", "0"))
    win_rate = (wins / games * 100) if games > 0 else 0.0
    safe_a = html.escape(champ_a)
    safe_b = html.escape(champ_b)
    safe_role = html.escape(role)
    safe_patch = html.escape(patch)
    wr_a = f"{win_rate:.1f}%"
    wr_b = f"{100 - win_rate:.1f}%"
    body = f"""<h2>{safe_a} vs {safe_b} ({safe_role})</h2>
<p>Patch {safe_patch} &mdash; {games} games</p>
<div class="card">
  <p>Win Rate ({safe_a}): <strong>{wr_a}</strong></p>
  <p>Win Rate ({safe_b}): <strong>{wr_b}</strong></p>
</div>
<p><a href="/matchups">&larr; New matchup lookup</a></p>"""
    return HTMLResponse(_page("Matchups", body, path="/matchups"))
