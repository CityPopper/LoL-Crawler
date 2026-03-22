"""Dashboard route — GET /."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from lol_ui.constants import _HALT_BANNER, _REGIONS, _STREAM_KEYS
from lol_ui.rendering import _badge, _depth_badge, _page

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Home dashboard — system status, stream depths, quick links."""
    r = request.app.state.r

    async with r.pipeline(transaction=False) as pipe:
        for s in _STREAM_KEYS:
            pipe.xlen(s)
        pipe.zcard("delayed:messages")
        pipe.get("system:halted")
        pipe.zcard("players:all")
        pipe.xlen("stream:dlq")
        results = await pipe.execute()

    stream_lengths: list[int] = results[: len(_STREAM_KEYS)]
    delayed: int = results[len(_STREAM_KEYS)]
    halted = results[len(_STREAM_KEYS) + 1]
    total_players: int = results[len(_STREAM_KEYS) + 2]
    dlq_depth: int = results[len(_STREAM_KEYS) + 3]

    halt_html = _HALT_BANNER if halted else ""
    system_badge = _badge("error", "HALTED") if halted else _badge("success", "Running")
    dlq_badge = (
        _badge("error", f"{dlq_depth} errors") if dlq_depth > 0 else _badge("success", "Clean")
    )

    stream_rows = ""
    for s, length in zip(_STREAM_KEYS, stream_lengths, strict=True):
        stream_rows += (
            f"<tr><td>{s}</td>"
            f"<td class='text-right'>{length}</td>"
            f"<td>{_depth_badge(s, length)}</td></tr>"
        )
    stream_rows += (
        f"<tr><td>delayed:messages</td>"
        f"<td class='text-right'>{delayed}</td>"
        f"<td>{_depth_badge('delayed:messages', delayed)}</td></tr>"
    )

    region_options = "\n        ".join(f'<option value="{reg}">{reg}</option>' for reg in _REGIONS)

    body = f"""{halt_html}
<h2>Dashboard</h2>
<div class="dashboard-grid">
  <div class="card">
    <h3 class="card__title">System Status</h3>
    <div>{system_badge}</div>
    <p style="margin:var(--space-sm) 0 0">
      <a href="/streams">View streams &rarr;</a>
    </p>
  </div>
  <div class="card">
    <h3 class="card__title">Players Tracked</h3>
    <div class="stat">
      <span class="stat__value">{total_players}</span>
      <span class="stat__label">total players</span>
    </div>
    <p style="margin:var(--space-sm) 0 0">
      <a href="/players">Browse players &rarr;</a>
    </p>
  </div>
  <div class="card">
    <h3 class="card__title">Dead Letter Queue</h3>
    <div>{dlq_badge}</div>
    <p style="margin:var(--space-sm) 0 0">
      <a href="/dlq">View DLQ &rarr;</a>
    </p>
  </div>
</div>

<div class="card">
  <h3 class="card__title">Stream Depths</h3>
  <div class="table-scroll">
  <table class="streams">
    <thead><tr><th scope="col">Key</th>
    <th scope="col" class="text-right">Length</th>
    <th scope="col">Status</th></tr></thead>
    <tbody>{stream_rows}</tbody>
  </table>
  </div>
</div>

<div class="card">
  <h3 class="card__title">Look Up a Player</h3>
  <p style="color:var(--color-muted);font-size:var(--font-size-sm)">
    Enter a Riot ID to view stats or auto-seed the player into the pipeline.
  </p>
  <form class="form-inline" method="get" action="/stats">
    <label for="dash-riot-id">Riot ID:</label>
    <input id="dash-riot-id" name="riot_id" placeholder="GameName#TagLine" required>
    <label for="dash-region">Region:</label>
    <select id="dash-region" name="region">
        {region_options}
    </select>
    <button type="submit">Look Up</button>
  </form>
  <p><a href="/stats">All regions &rarr;</a></p>
</div>

<p style="color:var(--color-muted);font-size:var(--font-size-sm)">
  Quick links:
  <a href="/stats">Stats</a> &middot;
  <a href="/players">Players</a> &middot;
  <a href="/streams">Streams</a> &middot;
  <a href="/dlq">DLQ</a> &middot;
  <a href="/logs">Logs</a>
</p>
"""
    return HTMLResponse(_page("Dashboard", body, path="/"))
