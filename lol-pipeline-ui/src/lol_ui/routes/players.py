"""Players route — GET /players."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from lol_ui.constants import (
    _HALT_BANNER,
    _PLAYERS_PAGE_SIZE,
    _PLAYERS_SORT_OPTIONS,
    _PlayerRow,
)
from lol_ui.player_helpers import _apply_player_sort, _render_player_rows
from lol_ui.rendering import _empty_state, _page

router = APIRouter()


@router.get("/players", response_class=HTMLResponse)
async def show_players(request: Request) -> HTMLResponse:
    r = request.app.state.r
    halted = await r.get("system:halted")
    halt_html = _HALT_BANNER if halted else ""
    try:
        page = int(request.query_params.get("page", "0"))
    except ValueError:
        page = 0
    page = max(0, page)

    sort = request.query_params.get("sort", "date")
    if sort not in _PLAYERS_SORT_OPTIONS:
        sort = "date"

    total: int = await r.zcard("players:all")
    if total == 0:
        body = (
            halt_html
            + "<h2>Players</h2>"
            + _empty_state(
                "No players seeded yet",
                "Run <code>just seed GameName#Tag</code> to get started.",
            )
        )
        return HTMLResponse(_page("Players", body, path="/players"))

    # Always fetch the current page by date order first (ZREVRANGE),
    # then re-sort in Python when a different sort key is requested.
    start = page * _PLAYERS_PAGE_SIZE
    end = start + _PLAYERS_PAGE_SIZE - 1
    page_puuids: list[str] = await r.zrevrange("players:all", start, end)

    # Fetch player metadata for current page only
    async with r.pipeline(transaction=False) as pipe:
        for puuid in page_puuids:
            pipe.hmget(f"player:{puuid}", ["game_name", "tag_line", "region", "seeded_at"])
        results: list[list[str | None]] = await pipe.execute()

    # Build list of (game_name, tag_line, region, seeded_at) tuples for sorting
    player_rows: list[_PlayerRow] = [
        (g, t, (region or "na1"), (seeded_at or ""))
        for g, t, region, seeded_at in results
        if g and t
    ]
    _apply_player_sort(player_rows, sort)
    rows = _render_player_rows(player_rows)

    has_prev = page > 0
    has_next = start + _PLAYERS_PAGE_SIZE < total
    total_pages = max(1, (total + _PLAYERS_PAGE_SIZE - 1) // _PLAYERS_PAGE_SIZE)

    def _sort_link(key: str, label: str) -> str:
        cls = ' class="active"' if sort == key else ""
        return (
            f'<a href="/players?sort={key}&amp;page={page}"{cls}'
            f' aria-label="Sort by {label}">{label}</a>'
        )

    sort_controls = f"""<div class="sort-controls">
  <span>Sort:</span>
  {_sort_link("date", "Date")}
  {_sort_link("name", "Name")}
  {_sort_link("region", "Region")}
</div>"""

    prev_link = (
        f'<a class="page-link" href="/players?sort={sort}&amp;page={page - 1}">&larr; Prev</a>'
        if has_prev
        else ""
    )
    page_indicator = f"page {page + 1} of {total_pages}"
    next_link = (
        f'<a class="page-link" href="/players?sort={sort}&amp;page={page + 1}">Next &rarr;</a>'
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

    body = f"""{halt_html}<h2>Players ({total} total, page {page + 1} of {total_pages})</h2>
{sort_controls}
<input id="player-search" placeholder="Filter players..." type="text"
  aria-label="Filter players by name">
<div class="table-scroll">
<table id="players-table">
  <thead><tr><th scope="col">Riot ID</th>
  <th scope="col">Region</th>
  <th scope="col">Seeded</th></tr></thead>
  <tbody>
  {rows}
  </tbody>
</table>
</div>
{pagination}
{filter_script}
"""
    return HTMLResponse(_page("Players", body, path="/players"))
