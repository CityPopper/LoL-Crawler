"""Dashboard route — GET /."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from lol_ui.constants import _HALT_BANNER, _REGIONS, _STREAM_KEYS
from lol_ui.rendering import _badge, _depth_badge, _page
from lol_ui.strings import t

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
    system_badge = _badge("error", t("halted")) if halted else _badge("success", t("running"))
    dlq_badge = (
        _badge("error", f"{dlq_depth} {t('errors')}")
        if dlq_depth > 0
        else _badge("success", t("clean"))
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
<h2>{t("dashboard")}</h2>
<div class="dashboard-grid">
  <div class="card">
    <h3 class="card__title">{t("system_status")}</h3>
    <div>{system_badge}</div>
    <p style="margin:var(--space-sm) 0 0">
      <a href="/streams">{t("view_streams")} &rarr;</a>
    </p>
  </div>
  <div class="card">
    <h3 class="card__title">{t("players_tracked")}</h3>
    <div class="stat">
      <span class="stat__value">{total_players}</span>
      <span class="stat__label">{t("total_players")}</span>
    </div>
    <p style="margin:var(--space-sm) 0 0">
      <a href="/players">{t("browse_players")} &rarr;</a>
    </p>
  </div>
  <div class="card">
    <h3 class="card__title">{t("dead_letter_queue")}</h3>
    <div>{dlq_badge}</div>
    <p style="margin:var(--space-sm) 0 0">
      <a href="/dlq">{t("view_dlq")} &rarr;</a>
    </p>
  </div>
</div>

<div class="card">
  <h3 class="card__title">{t("stream_depths")}</h3>
  <div class="table-scroll">
  <table class="streams">
    <thead><tr><th scope="col">{t("key")}</th>
    <th scope="col" class="text-right">{t("length")}</th>
    <th scope="col">{t("status")}</th></tr></thead>
    <tbody>{stream_rows}</tbody>
  </table>
  </div>
</div>

<div class="card">
  <h3 class="card__title">{t("look_up_player")}</h3>
  <p style="color:var(--color-muted);font-size:var(--font-size-sm)">
    {t("look_up_player_desc")}
  </p>
  <form class="form-inline" method="get" action="/stats" id="dash-lookup-form">
    <label for="dash-riot-id">{t("riot_id")}:</label>
    <input id="dash-riot-id" name="riot_id" placeholder="GameName#TagLine" required>
    <label for="dash-region">{t("region")}:</label>
    <select id="dash-region" name="region">
        {region_options}
    </select>
    <button type="submit">{t("look_up")}</button>
  </form>
  <script>
(function() {{
  var form = document.getElementById('dash-lookup-form');
  if (!form) return;
  form.addEventListener('submit', function(e) {{
    var input = document.getElementById('dash-riot-id');
    if (input && input.value.indexOf('#') !== -1) {{
      e.preventDefault();
      var region = document.getElementById('dash-region');
      var url = '/stats?riot_id=' + encodeURIComponent(input.value)
        + '&region=' + (region ? region.value : 'na1');
      window.location.href = url;
    }}
  }});
}})();
</script>
  <p><a href="/stats">{t("all_regions")} &rarr;</a></p>
</div>
"""
    return HTMLResponse(_page(t("dashboard"), body, path="/"))
