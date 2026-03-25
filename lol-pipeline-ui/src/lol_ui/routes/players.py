"""Players route — GET /players."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from lol_pipeline.helpers import is_system_halted

from lol_ui.constants import (
    _HALT_BANNER,
    _PLAYERS_PAGE_SIZE,
    _PLAYERS_SORT_OPTIONS,
    _REGIONS,
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

    def _sort_link(key: str, label_key: str) -> str:
        cls = ' class="active"' if sort == key else ""
        label = t(label_key)
        return (
            f'<a href="/players?sort={key}&amp;page={page}'
            f'&amp;region={region_filter}"{cls}'
            f' aria-label="Sort by {label}">{label}</a>'
        )

    sort_controls = f"""<div class="sort-controls">
  <span>{t("players_sort")}</span>
  {_sort_link("rank", "players_sort_rank")}
  {_sort_link("name", "players_sort_name")}
  {_sort_link("region", "players_sort_region")}
</div>"""

    region_options = f'<option value="">{t("players_all_regions")}</option>\n'
    region_options += "\n".join(
        f'<option value="{reg}"{" selected" if reg == region_filter else ""}>{reg}</option>'
        for reg in _REGIONS
    )
    region_select = (
        '<div class="filter-controls" style="margin:var(--space-sm) 0">'
        f'<label for="region-filter">{t("players_col_region")}:</label>'
        f'<select id="region-filter"'
        f" onchange=\"window.location.href='/players?sort={sort}"
        f"&region='+this.value\">"
        f"{region_options}</select></div>"
    )

    prev_link = (
        f'<a class="page-link" href="/players?sort={sort}'
        f'&amp;region={region_filter}&amp;page={page - 1}">'
        f"&larr; {t('players_prev')}</a>"
        if has_prev
        else ""
    )
    page_indicator = f"{t('players_page')} {page + 1} / {total_pages}"
    next_link = (
        f'<a class="page-link" href="/players?sort={sort}'
        f'&amp;region={region_filter}&amp;page={page + 1}">'
        f"{t('players_next')} &rarr;</a>"
        if has_next
        else ""
    )
    pagination = (
        f'<p style="display:flex;gap:var(--space-md);align-items:center">'
        f"{prev_link}{page_indicator}{next_link}</p>"
    )

    filter_script = """
<script>
(function() {
  var input = document.getElementById('player-search');
  if (!input) return;
  input.addEventListener('input', function() {
    var filter = input.value.toLowerCase();
    var rows = document.querySelectorAll('#players-table tbody tr');
    for (var i = 0; i < rows.length; i++) {
      var cell = rows[i].cells[0];
      var text = cell ? cell.textContent.toLowerCase() : '';
      rows[i].style.display = text.indexOf(filter) !== -1 ? '' : 'none';
    }
  });
})();
</script>
"""

    body = f"""{halt_html}<h2>{t("page_players")} ({display_total}\
 {t("players_total_page")} {page + 1} {t("players_of")} {total_pages})</h2>
{sort_controls}
{region_select}
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
