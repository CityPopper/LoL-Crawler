"""Players route — GET /players."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from lol_pipeline._helpers import is_system_halted

from lol_ui._render_helpers import (
    _pagination_html,
    _player_filter_script,
    _region_select,
    _sort_link,
)
from lol_ui.constants import (
    _HALT_BANNER,
    _PLAYERS_PAGE_SIZE,
    _PLAYERS_SORT_OPTIONS,
    _REGIONS_SET,
    _PlayerRow,
)
from lol_ui.player_helpers import _apply_player_sort, _render_player_rows
from lol_ui.rendering import _empty_state, _page
from lol_ui.strings import t, t_raw

router = APIRouter()


async def _build_player_rows(
    r: Any,
    puuids: list[str],
) -> list[_PlayerRow]:
    """Fetch player metadata + rank data for a list of PUUIDs."""
    if not puuids:
        return []
    async with r.pipeline(transaction=False) as pipe:
        for puuid in puuids:
            pipe.hmget(
                f"player:{puuid}",
                ["game_name", "tag_line", "region", "seeded_at"],
            )
            pipe.hgetall(f"player:rank:{puuid}")
        results = await pipe.execute()

    player_rows: list[_PlayerRow] = []
    for i in range(len(puuids)):
        meta = results[i * 2]
        rank = results[i * 2 + 1]
        g, t_val, region, seeded_at = meta
        if g and t_val:
            player_rows.append((g, t_val, (region or "na1"), (seeded_at or ""), rank or {}))
    return player_rows


@router.get("/players", response_class=HTMLResponse)
async def show_players(request: Request) -> HTMLResponse:
    """Show paginated player list with rank sort and region filter."""
    r = request.app.state.r
    halted = await is_system_halted(r)
    halt_html = _HALT_BANNER if halted else ""
    try:
        page = int(request.query_params.get("page", "0"))
    except ValueError:
        page = 0
    page = max(0, page)

    sort = request.query_params.get("sort", "rank")
    if sort not in _PLAYERS_SORT_OPTIONS:
        sort = "rank"

    region_filter = request.query_params.get("region", "")
    if region_filter and region_filter not in _REGIONS_SET:
        region_filter = ""

    total: int = await r.zcard("players:all")
    if total == 0:
        body = (
            halt_html
            + f"<h2>{t('page_players')}</h2>"
            + _empty_state(
                t("players_no_players"),
                t_raw("players_seed_hint"),
            )
        )
        return HTMLResponse(_page(t("page_players"), body, path="/players"))

    # Paginate at Redis level: fetch only the current page of PUUIDs.
    start = page * _PLAYERS_PAGE_SIZE
    stop = start + _PLAYERS_PAGE_SIZE - 1
    page_puuids: list[str] = await r.zrevrange("players:all", start, stop)
    player_rows = await _build_player_rows(r, page_puuids)

    if region_filter:
        player_rows = [row for row in player_rows if row[2] == region_filter]

    _apply_player_sort(player_rows, sort)
    display_total = total
    total_pages = max(1, (display_total + _PLAYERS_PAGE_SIZE - 1) // _PLAYERS_PAGE_SIZE)
    has_prev = page > 0
    has_next = stop < total - 1

    rows = _render_player_rows(player_rows)

    sort_controls = f"""<div class="sort-controls">
  <span>{t("players_sort")}</span>
  {_sort_link("rank", "players_sort_rank", sort, page, region_filter)}
  {_sort_link("name", "players_sort_name", sort, page, region_filter)}
  {_sort_link("region", "players_sort_region", sort, page, region_filter)}
</div>"""

    region_sel = _region_select(sort, region_filter)

    page_indicator = f"{t('players_page')} {page + 1} / {total_pages}"
    prev_url = (
        f"/players?sort={sort}&amp;region={region_filter}&amp;page={page - 1}" if has_prev else None
    )
    next_url = (
        f"/players?sort={sort}&amp;region={region_filter}&amp;page={page + 1}" if has_next else None
    )
    pagination = _pagination_html(prev_url, next_url, page_indicator)
    filter_script = _player_filter_script()

    body = f"""{halt_html}<h2>{t("page_players")} ({display_total}\
 {t("players_total_page")} {page + 1} {t("players_of")} {total_pages})</h2>
{sort_controls}
{region_sel}
<input id="player-search" placeholder="{t("players_filter")}" type="text"
  aria-label="{t("players_filter_aria")}">
<div class="table-scroll">
<table id="players-table">
  <thead><tr><th scope="col">{t("players_col_riot_id")}</th>
  <th scope="col">{t("players_col_region")}</th>
  <th scope="col">{t("players_col_rank")}</th>
  <th scope="col">{t("players_col_seeded")}</th></tr></thead>
  <tbody>
  {rows}
  </tbody>
</table>
</div>
{pagination}
{filter_script}
"""
    return HTMLResponse(_page(t("page_players"), body, path="/players"))
