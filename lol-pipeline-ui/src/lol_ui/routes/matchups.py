"""Matchups route — GET /matchups."""

from __future__ import annotations

import html

import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from lol_pipeline.i18n import label

from lol_ui._helpers import _safe_int
from lol_ui.constants import _CHAMPION_NAME_RE, _MATCHUP_ROLES, _PATCH_RE
from lol_ui.ddragon import get_champion_name_map, localize_champion_name
from lol_ui.language import _current_lang
from lol_ui.rendering import _empty_state, _page
from lol_ui.strings import t

router = APIRouter()


def _champion_datalist(name_map: dict[str, str]) -> str:
    """Render a ``<datalist>`` element with champion names for autocomplete."""
    if not name_map:
        return ""
    options = "\n".join(
        f'<option value="{html.escape(display_name)}">'
        for display_name in sorted(name_map.values())
    )
    return f'<datalist id="champion-list">\n{options}\n</datalist>'


def _role_options(lang: str) -> str:
    """Render localized ``<option>`` elements for the role dropdown."""
    roles = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    return "\n    ".join(
        f'<option value="{key}">{html.escape(label("role", key, lang))}</option>' for key in roles
    )


@router.get("/matchups", response_class=HTMLResponse)
async def show_matchups(request: Request) -> HTMLResponse:
    """Champion matchup lookup page."""
    r: aioredis.Redis = request.app.state.r
    champ_a = request.query_params.get("champ_a", "")
    champ_b = request.query_params.get("champ_b", "")
    role = request.query_params.get("role", "")
    patch = request.query_params.get("patch", "")
    lang = _current_lang.get()

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
        name_map = await get_champion_name_map(r, lang)
        datalist_html = _champion_datalist(name_map)
        body = f"""<h2>{t("page_champion_matchups")}</h2>
<form class="form-inline" method="get" action="/matchups">
  <label for="matchup-a">{t("matchups_champ_a")}
    <input id="matchup-a" name="champ_a" placeholder="{t("matchups_placeholder_champ")}" required\
 list="champion-list">
  </label>
  <label for="matchup-b">{t("matchups_champ_b")}
    <input id="matchup-b" name="champ_b" placeholder="{t("matchups_placeholder_champ")}" required\
 list="champion-list">
  </label>
  <label for="matchup-role">{t("matchups_role")}
    <select id="matchup-role" name="role">
      {_role_options(lang)}
    </select>
  </label>
  <label for="matchup-patch">{t("matchups_patch_optional")}
    <input id="matchup-patch" name="patch" placeholder="{t("matchups_placeholder_patch")}">
  </label>
  <button type="submit">{t("matchups_compare")}</button>
</form>
{datalist_html}"""
        return HTMLResponse(_page(t("page_matchups"), body, path="/matchups"))

    # Resolve current patch if not provided
    if not patch:
        patches_raw: list[tuple[str, float]] = await r.zrevrange(
            "patch:list", 0, 0, withscores=True
        )
        patch = patches_raw[0][0] if patches_raw else ""

    if not patch:
        body = _empty_state(
            t("matchups_no_patch_data"),
            t("matchups_no_patch_hint"),
        )
        return HTMLResponse(_page(t("page_matchups"), body, path="/matchups"))

    key = f"matchup:{champ_a}:{champ_b}:{role}:{patch}"
    data: dict[str, str] = await r.hgetall(key)  # type: ignore[misc]

    # Localize champion display names for results
    name_map = await get_champion_name_map(r, lang)
    display_a = html.escape(localize_champion_name(name_map, champ_a))
    display_b = html.escape(localize_champion_name(name_map, champ_b))
    safe_role = html.escape(label("role", role, lang))

    if not data:
        body = _empty_state(
            t("matchups_no_matchup_data"),
            f"{t('matchups_no_games_for')} {display_a} {t('matchups_vs')}"
            f" {display_b} {t('matchups_as')} {safe_role}.",
        )
        return HTMLResponse(_page(t("page_matchups"), body, path="/matchups"))

    games = _safe_int(data.get("games", "0"))
    wins = _safe_int(data.get("wins", "0"))
    win_rate = (wins / games * 100) if games > 0 else 0.0
    safe_patch = html.escape(patch)
    wr_a = f"{win_rate:.1f}%"
    wr_b = f"{100 - win_rate:.1f}%"
    body = f"""<h2>{display_a} vs {display_b} ({safe_role})</h2>
<p>Patch {safe_patch} &mdash; {games} {t("matchups_games")}</p>
<div class="card">
  <p>{t("matchups_win_rate")} ({display_a}): <strong>{wr_a}</strong></p>
  <p>{t("matchups_win_rate")} ({display_b}): <strong>{wr_b}</strong></p>
</div>
<p><a href="/matchups">&larr; {t("matchups_new_lookup")}</a></p>"""
    return HTMLResponse(_page(t("page_matchups"), body, path="/matchups"))
