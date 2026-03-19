"""Web UI — view player stats."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import heapq
import html
import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote as _url_quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from lol_pipeline.priority import set_priority
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    RiotClient,
    ServerError,
)
from lol_pipeline.streams import publish

_STREAM_PUUID = "stream:puuid"
_PUUID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_log = get_logger("ui")


# ---------------------------------------------------------------------------
# LCU data loading
# ---------------------------------------------------------------------------


def _load_lcu_data(data_dir: str) -> dict[str, list[dict[str, Any]]]:
    """Load all JSONL files from data_dir into memory keyed by puuid."""
    result: dict[str, list[dict[str, Any]]] = {}
    p = Path(data_dir)
    if not p.exists():
        return result
    for f in sorted(p.glob("*.jsonl")):
        puuid = f.stem
        matches: list[dict[str, Any]] = []
        for line in f.read_text().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                matches.append(json.loads(stripped))
            except json.JSONDecodeError:
                _log.warning("corrupt JSON line in %s, skipping", f.name)
        if matches:
            result[puuid] = matches
    return result


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


async def _lcu_reload_loop(app: FastAPI, data_dir: str, interval_minutes: int) -> None:
    """Reload LCU JSONL data from disk every interval_minutes minutes."""
    while True:
        await asyncio.sleep(interval_minutes * 60)
        app.state.lcu = _load_lcu_data(data_dir)
        _log.info(
            "lcu data reloaded",
            extra={"players": len(app.state.lcu), "interval_minutes": interval_minutes},
        )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = Config()  # type: ignore[call-arg]  # pydantic-settings reads env
    app.state.cfg = cfg
    app.state.r = get_redis(cfg.redis_url)
    app.state.riot = RiotClient(cfg.riot_api_key, r=app.state.r)
    lcu_dir = os.getenv("LCU_DATA_DIR", "./lcu-data")
    app.state.lcu = _load_lcu_data(lcu_dir)
    _log.info("lcu data loaded", extra={"players": len(app.state.lcu), "dir": lcu_dir})

    poll_minutes = int(os.getenv("LCU_POLL_INTERVAL_MINUTES", "0"))
    reload_task: asyncio.Task[None] | None = None
    if poll_minutes > 0:
        reload_task = asyncio.create_task(_lcu_reload_loop(app, lcu_dir, poll_minutes))
        _log.info("lcu reload loop started", extra={"interval_minutes": poll_minutes})

    yield

    if reload_task is not None:
        reload_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reload_task
    await app.state.r.aclose()
    await app.state.riot.close()


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

_NAV_ITEMS = [
    ("/stats", "Stats"),
    ("/players", "Players"),
    ("/streams", "Streams"),
    ("/dlq", "DLQ"),
    ("/lcu", "LCU"),
    ("/logs", "Logs"),
]

_CSS = """
:root {
  --color-bg: #1a1a2e;
  --color-surface: #16213e;
  --color-text: #e0e0e0;
  --color-muted: #999;
  --color-border: #333;
  --color-success: #2ecc40;
  --color-error: #ff4136;
  --color-warning: #ffdc00;
  --color-info: #5a9eff;
  --font-mono: 'Fira Code', 'JetBrains Mono', 'Cascadia Code', monospace;
  --font-size-sm: 12px;
  --font-size-base: 14px;
  --font-size-lg: 16px;
  --font-size-xl: 20px;
  --font-size-2xl: 24px;
  --line-height: 1.6;
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 32px;
  --radius: 4px;
}
body {
  font-family: var(--font-mono);
  font-size: var(--font-size-base);
  background: var(--color-bg);
  color: var(--color-text);
  max-width: min(900px, 100% - 2rem);
  margin: 2rem auto;
  padding: 0 var(--space-sm);
  line-height: var(--line-height);
}
a { color: var(--color-info); }
h1 { border-bottom: 2px solid var(--color-border); padding-bottom: 0.5rem; }
hr { border: none; border-top: 1px solid var(--color-border); }
nav { display: flex; gap: var(--space-sm); overflow-x: auto; padding-bottom: var(--space-xs); }
nav a {
  white-space: nowrap;
  padding: var(--space-sm) var(--space-md);
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  border-radius: var(--radius);
  text-decoration: none;
}
nav a:hover { background: var(--color-surface); }
nav a.active { border-bottom: 2px solid var(--color-info); font-weight: bold; }
:focus-visible { outline: 2px solid var(--color-info); outline-offset: 2px; }
form { margin: 1rem 0; }
input, select {
  background: var(--color-surface);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  padding: var(--space-sm);
  margin: 0.2rem;
  font-size: var(--font-size-lg);
  min-height: 44px;
  border-radius: var(--radius);
}
button, .btn {
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--color-info);
  color: #fff;
  border: none;
  padding: var(--space-sm) var(--space-lg);
  cursor: pointer;
  border-radius: var(--radius);
  min-height: 44px;
  font-size: var(--font-size-lg);
  text-decoration: none;
}
button:hover, .btn:hover { filter: brightness(1.1); }
.success { color: var(--color-success); }
.error { color: var(--color-error); }
.warning { color: var(--color-warning); }
.unverified { color: var(--color-warning); }
table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
td, th { border: 1px solid var(--color-border); padding: 0.4rem 0.8rem; text-align: left; }
th { background: var(--color-surface); color: var(--color-muted); }
pre { background: var(--color-surface); padding: 12px;
  overflow-x: auto; border-radius: var(--radius); }
code { background: var(--color-surface); padding: 2px 6px; border-radius: var(--radius); }
.streams td:last-child { text-align: right; }

/* Cards */
.card { background: var(--color-surface); border: 1px solid var(--color-border);
        border-radius: var(--radius); padding: var(--space-md); margin: var(--space-md) 0; }
.card__title { margin-top: 0; font-size: var(--font-size-lg); color: var(--color-muted); }

/* Badges */
.badge { display: inline-block; padding: 2px 8px; border-radius: var(--radius);
         font-size: var(--font-size-sm); font-weight: bold; }
.badge--success { background: var(--color-success); color: #111; }
.badge--error { background: #cc3333; color: #fff; }
.badge--warning { background: var(--color-warning); color: #111; }
.badge--info { background: var(--color-info); color: #fff; }
.badge--muted { background: var(--color-border); color: var(--color-text); }

/* Stat counters */
.stat { display: inline-block; text-align: center; padding: var(--space-md); }
.stat__value { display: block; font-size: var(--font-size-2xl); font-weight: bold; }
.stat__label { display: block; font-size: var(--font-size-sm); color: var(--color-muted); }

/* Form layout — mobile-first: stacked by default */
.form-inline { display: flex; flex-direction: column; gap: var(--space-sm); }
.form-inline input, .form-inline select, .form-inline button { width: 100%; }
.form-inline label { display: flex; flex-direction: column; gap: 2px;
                     font-size: var(--font-size-sm); color: var(--color-muted); }

/* Table scroll wrapper */
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }

/* Banners */
.banner { padding: var(--space-md); border-radius: var(--radius); margin: var(--space-md) 0;
          border-left: 4px solid; }
.banner--error { background: rgba(255,65,54,0.1); border-color: var(--color-error); }
.banner--success { background: rgba(46,204,64,0.1); border-color: var(--color-success); }
.banner--warning { background: rgba(255,220,0,0.1); border-color: var(--color-warning); }

/* Empty state */
.empty-state { text-align: center; padding: var(--space-xl); color: var(--color-muted); }
.empty-state code { display: block; margin-top: var(--space-sm); }

/* Stats grid */
.stats-grid { display: grid; grid-template-columns: 1fr; gap: var(--space-md); }

/* Skip to content */
.skip-link { position: absolute; top: -40px; left: 0; padding: var(--space-sm);
             background: var(--color-info); color: #fff; z-index: 100; }
.skip-link:focus { top: var(--space-sm); }

/* Log viewer */
.log-wrap { font-family: var(--font-mono); font-size: 0.82em; }
.log-line { display: flex; gap: 0.5rem; padding: 2px 4px;
  border-bottom: 1px solid var(--color-border); align-items: baseline; flex-wrap: wrap; }
.log-critical { background: rgba(255,65,54,0.15); font-weight: bold; }
.log-error { background: rgba(255,65,54,0.08); }
.log-warning { background: rgba(255,220,0,0.08); }
.log-debug { color: var(--color-muted); }
.log-ts { color: var(--color-muted); white-space: nowrap; flex-shrink: 0; }
.log-badge { padding: 0 4px; border-radius: 2px;
  font-size: 0.75em; white-space: nowrap; flex-shrink: 0; }
.log-badge.log-critical { background: #c00; color: #fff; }
.log-badge.log-error { background: #e33; color: #fff; }
.log-badge.log-warning { background: #e80; color: #fff; }
.log-badge.log-debug { background: #555; color: #fff; }
.log-badge.log-info { background: var(--color-info); color: #fff; }
.log-svc { color: var(--color-info); flex-shrink: 0; }
.log-msg { flex: 1; }
.log-extra { color: var(--color-muted); font-size: 0.9em; }
.log-controls { margin: 0.5rem 0; display: flex;
  gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.log-meta { color: var(--color-muted); font-size: 0.85em; margin-bottom: 0.3rem; }
#pause-btn { padding: 0.4rem 1rem; cursor: pointer; }
#pause-btn.paused { background: var(--color-error); color: #fff; }

/* Mobile log lines */
.log-line { flex-direction: column; gap: 2px; }
.log-ts, .log-badge, .log-svc { font-size: 0.75em; }

/* Tablet (768px+) */
@media (min-width: 768px) {
  .form-inline { flex-direction: row; flex-wrap: wrap; align-items: flex-end; }
  .form-inline input, .form-inline select, .form-inline button {
    width: auto; flex: 1; min-width: 0; }
  body { padding: 0 1rem; }
  .log-line { flex-direction: row; gap: 0.5rem; flex-wrap: nowrap; }
  .log-ts, .log-badge, .log-svc { font-size: inherit; }
}

/* Wide desktop (1440px+) */
@media (min-width: 1440px) {
  body { max-width: 1200px; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important; }
}
"""

_FAVICON = (
    "data:image/svg+xml,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='4' fill='%231a1a2e'/>"
    "<text x='16' y='22' text-anchor='middle' fill='%235a9eff' "
    "font-size='20'>L</text></svg>"
)

_BADGE_VARIANTS = frozenset({"success", "error", "warning", "info", "muted"})

_STATS_ORDER = [
    "total_games",
    "total_wins",
    "win_rate",
    "total_kills",
    "total_deaths",
    "total_assists",
    "kda",
    "avg_kills",
    "avg_deaths",
    "avg_assists",
]

_STATS_ORDER_SET = frozenset(_STATS_ORDER)


def _format_stat_value(key: str, value: str) -> str:
    """Format a stat value for display.

    win_rate is multiplied by 100 and shown as %. Averages and kda rounded to 2dp.
    """
    if key == "win_rate":
        try:
            return f"{float(value) * 100:.1f}%"
        except ValueError:
            return value
    if key.startswith("avg_") or key == "kda":
        try:
            return f"{float(value):.2f}"
        except ValueError:
            return value
    return value


def _depth_badge(stream_name: str, depth: int) -> str:
    """Return a status badge based on stream depth thresholds."""
    if stream_name == "stream:dlq":
        if depth > 0:
            return _badge("error", f"{depth} errors")
        return _badge("success", "OK")
    if depth < 100:
        return _badge("success", "OK")
    if depth < 1000:
        return _badge("warning", "Busy")
    return _badge("error", "Backlog")


def _badge(variant: str, text: str) -> str:
    """Render a status badge. text is raw HTML (caller must escape user data).

    variant: success|error|warning|info|muted.
    """
    if variant not in _BADGE_VARIANTS:
        msg = f"Invalid badge variant: {variant}"
        raise ValueError(msg)
    return f'<span class="badge badge--{variant}">{text}</span>'


def _empty_state(title: str, body_html: str) -> str:
    """Render an empty-state message. Both params are raw HTML -- callers MUST
    pre-escape any dynamic content with html.escape().
    """
    return f'<div class="empty-state"><p><strong>{title}</strong></p><p>{body_html}</p></div>'


def _page(title: str, body: str, path: str = "") -> str:
    nav_links = []
    for href, label in _NAV_ITEMS:
        cls = ' class="active"' if path == href else ""
        nav_links.append(f'<a href="{href}"{cls}>{label}</a>')
    nav_html = "\n  ".join(nav_links)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>{title} — LoL Pipeline</title>
  <link rel="icon" href="{_FAVICON}">
  <style>{_CSS}</style>
</head>
<body>
<h1>LoL Pipeline</h1>
<nav>
  {nav_html}
</nav>
<hr>
{body}
</body>
</html>"""


_REGIONS = ["na1", "euw1", "eun1", "kr", "br1", "jp1", "oc1"]


def _stats_form(
    msg: str = "",
    css_class: str = "",
    stats_html: str = "",
    selected_region: str = "na1",
) -> str:
    msg_html = f'<p class="{css_class}">{msg}</p>' if msg else ""
    options = "\n      ".join(
        f'<option value="{r}"{"selected" if r == selected_region else ""}>{r}</option>'
        for r in _REGIONS
    )
    return _page(
        "Player Stats",
        f"""
<h2>Player Stats</h2>
{msg_html}
<form class="form-inline" method="get" action="/stats">
  <label>Riot ID: <input name="riot_id" placeholder="GameName#TagLine" required></label>
  <label>Region:
    <select name="region">
      {options}
    </select>
  </label>
  <button type="submit">Look Up</button>
</form>
{stats_html}
""",
        path="/stats",
    )


def _stats_table(
    stats: dict[str, str],
    champs: list[tuple[str, float]],
    roles: list[tuple[str, float]],
) -> str:
    ordered = [(k, stats[k]) for k in _STATS_ORDER if k in stats]
    remaining = [(k, v) for k, v in sorted(stats.items()) if k not in _STATS_ORDER_SET]
    rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td>{html.escape(_format_stat_value(k, v))}</td></tr>"
        for k, v in ordered + remaining
    )
    champ_rows = "".join(f"<tr><td>{html.escape(c)}</td><td>{int(n)}</td></tr>" for c, n in champs)
    role_rows = "".join(f"<tr><td>{html.escape(r)}</td><td>{int(n)}</td></tr>" for r, n in roles)
    return f"""
<h3>Verified (Riot API) {_badge("success", "&#10003; Verified")}</h3>
<div class="table-scroll">
<table><tr><th>Stat</th><th>Value</th></tr>{rows}</table>
</div>
<div class="stats-grid">
<div>
<h3>Top Champions</h3>
<div class="table-scroll">
<table><tr><th>Champion</th><th>Games</th></tr>
{champ_rows or "<tr><td colspan='2'>No data</td></tr>"}</table>
</div>
</div>
<div>
<h3>Roles</h3>
<div class="table-scroll">
<table><tr><th>Role</th><th>Games</th></tr>
{role_rows or "<tr><td colspan='2'>No data</td></tr>"}</table>
</div>
</div>
</div>
"""


def _match_history_section(puuid: str, region: str, riot_id: str) -> str:
    """Render a lazy-loading match history placeholder section."""
    safe_puuid = html.escape(puuid, quote=True)
    safe_region = html.escape(region, quote=True)
    safe_id = html.escape(riot_id, quote=True)
    href = (
        f"/stats/matches?puuid={safe_puuid}"
        f"&amp;region={safe_region}&amp;riot_id={safe_id}&amp;page=0"
    )
    return f"""
<h3>Match History</h3>
<div id="match-history-container">
  <p><a href="{href}" class="load-matches"
     data-puuid="{safe_puuid}" data-region="{safe_region}"
     data-riot-id="{safe_id}" data-page="0">Load match history</a></p>
</div>
<script>
function loadMatches(puuid, region, riotId, page) {{
  var container = document.getElementById('match-history-container');
  container.innerHTML = '<p>Loading...</p>';
  var url = '/stats/matches?puuid=' + encodeURIComponent(puuid)
    + '&region=' + encodeURIComponent(region)
    + '&riot_id=' + encodeURIComponent(riotId)
    + '&page=' + page;
  fetch(url, {{headers: {{'Accept': 'text/html'}}}})
    .then(function(r) {{ return r.text(); }})
    .then(function(html) {{ container.innerHTML = html; }})
    .catch(function(e) {{
      container.innerHTML = '<p class="error">Failed to load: ' + (e.message || e) + '</p>';
    }});
}}
document.addEventListener('click', function(e) {{
  var el = e.target.closest('.load-matches');
  if (!el) return;
  e.preventDefault();
  loadMatches(el.dataset.puuid, el.dataset.region, el.dataset.riotId, +el.dataset.page);
}});
</script>
"""


def _aggregate_by_mode(matches: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Aggregate win/loss counts by game mode."""
    by_mode: dict[str, dict[str, int]] = {}
    for m in matches:
        mode = str(m.get("game_mode", "UNKNOWN"))
        by_mode.setdefault(mode, {"t": 0, "w": 0})
        by_mode[mode]["t"] += 1
        if m.get("win"):
            by_mode[mode]["w"] += 1
    return by_mode


def _lcu_stats_section(matches: list[dict[str, Any]]) -> str:
    """Render LCU unverified stats section for the /stats page."""
    total = len(matches)
    wins = sum(1 for m in matches if m.get("win"))
    by_mode = _aggregate_by_mode(matches)
    mode_rows = "".join(
        f"<tr><td>{html.escape(mode)}</td><td>{d['t']}</td>"
        f"<td>{d['w']}</td><td>{d['t'] - d['w']}</td></tr>"
        for mode, d in sorted(by_mode.items())
    )
    return f"""
<h3>Unverified (LCU) {_badge("warning", "&#9888; Unverified")}</h3>
<p class="unverified">Collected from the local League client. May include game modes not
tracked by the Riot API (ARAM Mayhem, URF, etc.). May overlap with verified data above.</p>
<div class="table-scroll">
<table>
  <tr><th>Total Games</th><th>Wins</th><th>Losses</th></tr>
  <tr><td>{total}</td><td>{wins}</td><td>{total - wins}</td></tr>
</table>
</div>
<h4>By Game Mode</h4>
<div class="table-scroll">
<table>
  <tr><th>Mode</th><th>Games</th><th>Wins</th><th>Losses</th></tr>
  {mode_rows or "<tr><td colspan='4'>No data</td></tr>"}
</table>
</div>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app = FastAPI(title="LoL Pipeline UI", lifespan=_lifespan)


@app.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse("/stats")


@app.get("/stats", response_class=HTMLResponse)
async def show_stats(request: Request) -> HTMLResponse:  # noqa: PLR0911, C901
    riot_id = request.query_params.get("riot_id", "")
    region = request.query_params.get("region", "na1")

    if not riot_id:
        return HTMLResponse(_stats_form(selected_region=region))

    if "#" not in riot_id:
        return HTMLResponse(
            _stats_form(
                "Invalid Riot ID — expected GameName#TagLine", "error", selected_region=region
            )
        )

    game_name, tag_line = riot_id.split("#", 1)
    r = request.app.state.r
    cfg: Config = request.app.state.cfg
    riot: RiotClient = request.app.state.riot

    cache_key = f"player:name:{game_name.lower()}#{tag_line.lower()}"
    cached_puuid: str | None = await r.get(cache_key)
    if cached_puuid:
        puuid = cached_puuid
    else:
        try:
            account = await riot.get_account_by_riot_id(game_name, tag_line, region)
        except NotFoundError:
            return HTMLResponse(
                _stats_form(
                    "Player not found. Check the spelling of the Riot ID.",
                    "error",
                    selected_region=region,
                )
            )
        except RateLimitError:
            return HTMLResponse(
                _stats_form(
                    "Rate limited. Try again in a few seconds.",
                    "warning",
                    selected_region=region,
                )
            )
        except AuthError:
            return HTMLResponse(
                _stats_form(
                    "API key issue. An admin must run <code>just admin system-resume</code>.",
                    "error",
                    selected_region=region,
                )
            )
        except ServerError:
            return HTMLResponse(
                _stats_form(
                    "Riot servers temporarily unavailable. Try again later.",
                    "warning",
                    selected_region=region,
                )
            )
        puuid = account["puuid"]
        await r.set(cache_key, puuid)
    stats: dict[str, str] = await r.hgetall(f"player:stats:{puuid}")

    lcu_matches: list[dict[str, Any]] = request.app.state.lcu.get(puuid, [])
    lcu_html = _lcu_stats_section(lcu_matches) if lcu_matches else ""

    safe_id = html.escape(riot_id)

    if not stats and not lcu_matches:
        # Auto-seed: publish to stream:puuid if not halted and not already seeded
        if await r.get("system:halted"):
            return HTMLResponse(
                _stats_form(
                    f"System halted. No stats yet for {safe_id}.",
                    "error",
                    selected_region=region,
                )
            )
        existing_seeded: str | None = await r.hget(f"player:{puuid}", "seeded_at")
        if existing_seeded:
            return HTMLResponse(
                _stats_form(
                    f"{safe_id} was seeded recently — pipeline processing. Check back soon.",
                    "warning",
                    selected_region=region,
                )
            )
        envelope = MessageEnvelope(
            source_stream=_STREAM_PUUID,
            type="puuid",
            payload={
                "puuid": puuid,
                "game_name": game_name,
                "tag_line": tag_line,
                "region": region,
            },
            max_attempts=cfg.max_attempts,
            priority="high",
        )
        await publish(r, _STREAM_PUUID, envelope)
        await set_priority(r, puuid)
        now_iso = datetime.now(tz=UTC).isoformat()
        await r.hset(
            f"player:{puuid}",
            mapping={
                "game_name": game_name,
                "tag_line": tag_line,
                "region": region,
                "seeded_at": now_iso,
            },
        )
        _log.info("auto-seeded via stats lookup", extra={"puuid": puuid})
        return HTMLResponse(
            _stats_form(
                f"&#10003; Auto-seeded {safe_id} — pipeline processing. Refresh in a minute.",
                "warning",
                selected_region=region,
            )
        )

    # Check for active priority
    priority_key = await r.get(f"player:priority:{puuid}")
    priority_html = f" {_badge('info', 'Priority')}" if priority_key else ""

    champs: list[tuple[str, float]] = await r.zrevrange(
        f"player:champions:{puuid}", 0, 9, withscores=True
    )
    roles: list[tuple[str, float]] = await r.zrevrange(
        f"player:roles:{puuid}", 0, -1, withscores=True
    )
    api_html = (
        _stats_table(stats, champs, roles)
        if stats
        else ("<p class='warning'>No verified API stats yet (pipeline still processing).</p>")
    )
    history_html = _match_history_section(puuid, region, riot_id)
    safe_name = html.escape(f"{game_name}#{tag_line}")
    heading = f"Stats for {safe_name}{priority_html}"
    return HTMLResponse(
        _stats_form(heading, "success", api_html + lcu_html + history_html, selected_region=region)
    )


_PLAYERS_PAGE_SIZE = 25


@app.get("/players", response_class=HTMLResponse)
async def show_players(request: Request) -> HTMLResponse:
    r = request.app.state.r
    try:
        page = int(request.query_params.get("page", "0"))
    except ValueError:
        page = 0

    # Collect player:{puuid} keys only — exclude player:stats:, player:matches:, etc.
    all_keys: list[str] = []
    async for key in r.scan_iter(match="player:*", count=200):
        if key.count(":") == 1:
            all_keys.append(key)

    if not all_keys:
        body = "<h2>Players</h2>" + _empty_state(
            "No players seeded yet",
            "Run <code>just seed GameName#Tag</code> to get started.",
        )
        return HTMLResponse(_page("Players", body, path="/players"))

    # Fetch all player metadata in a single pipeline round-trip
    async with r.pipeline(transaction=False) as pipe:
        for key in all_keys:
            pipe.hmget(key, ["game_name", "tag_line", "region", "seeded_at"])
        results: list[list[str | None]] = await pipe.execute()

    players: list[dict[str, str]] = []
    for fields in results:
        game_name, tag_line, region, seeded_at = fields
        if not game_name or not tag_line:
            continue  # skip players pending name resolution
        players.append(
            {
                "game_name": game_name,
                "tag_line": tag_line,
                "region": region or "na1",
                "seeded_at": seeded_at or "",
            }
        )

    players.sort(key=lambda p: p["seeded_at"], reverse=True)

    total = len(players)
    start = page * _PLAYERS_PAGE_SIZE
    page_players = players[start : start + _PLAYERS_PAGE_SIZE]

    rows = ""
    for p in page_players:
        href = (
            f"/stats?riot_id={_url_quote(p['game_name'] + '#' + p['tag_line'])}"
            f"&amp;region={html.escape(p['region'])}"
        )
        safe_name = html.escape(f"{p['game_name']}#{p['tag_line']}")
        seeded = html.escape(p["seeded_at"]) if p["seeded_at"] else "?"
        rows += (
            f'<tr><td><a href="{href}">{safe_name}</a></td>'
            f"<td>{html.escape(p['region'])}</td><td>{seeded}</td></tr>"
        )

    has_prev = page > 0
    has_next = start + _PLAYERS_PAGE_SIZE < total
    total_pages = max(1, (total + _PLAYERS_PAGE_SIZE - 1) // _PLAYERS_PAGE_SIZE)
    prev_link = f'<a href="/players?page={page - 1}">&larr; Prev</a>' if has_prev else ""
    page_indicator = f"page {page + 1} of {total_pages}"
    sep = "&nbsp;&nbsp;" if has_prev else ""
    next_link = f'<a href="/players?page={page + 1}">Next &rarr;</a>' if has_next else ""
    sep2 = "&nbsp;&nbsp;" if has_next else ""
    pagination = f"<p>{prev_link}{sep}{page_indicator}{sep2}{next_link}</p>"

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

    body = f"""<h2>Players ({total} total, page {page + 1} of {total_pages})</h2>
<input id="player-search" placeholder="Filter players..." type="text">
<div class="table-scroll">
<table id="players-table">
  <thead><tr><th>Riot ID</th><th>Region</th><th>Seeded</th></tr></thead>
  <tbody>
  {rows}
  </tbody>
</table>
</div>
{pagination}
{filter_script}
"""
    return HTMLResponse(_page("Players", body, path="/players"))


_STREAM_KEYS = [
    "stream:puuid",
    "stream:match_id",
    "stream:parse",
    "stream:analyze",
    "stream:dlq",
    "stream:dlq:archive",
]


async def _streams_fragment_html(r: Any) -> str:
    """Build the inner HTML for the streams table + status (no page wrapper)."""
    rows = ""
    for s in _STREAM_KEYS:
        length = await r.xlen(s)
        status_badge = _depth_badge(s, length)
        rows += f"<tr><td>{s}</td><td>{length}</td><td>{status_badge}</td></tr>"
    delayed = await r.zcard("delayed:messages")
    delayed_badge = _depth_badge("delayed:messages", delayed)
    rows += f"<tr><td>delayed:messages</td><td>{delayed}</td><td>{delayed_badge}</td></tr>"

    halted = await r.get("system:halted")
    status = (
        '<div class="banner banner--error">&#9888; System is HALTED (system:halted is set)</div>'
        if halted
        else '<div class="banner banner--success">&#10003; System running</div>'
    )

    priority_count_val = await r.get("system:priority_count")
    priority_display = priority_count_val or "0"

    return f"""{status}
<p>Priority players in-flight: <strong>{priority_display}</strong></p>
<div class="table-scroll">
<table class="streams">
  <tr><th>Key</th><th>Length</th><th>Status</th></tr>
  {rows}
</table>
</div>
"""


@app.get("/streams/fragment", response_class=HTMLResponse)
async def streams_fragment(request: Request) -> HTMLResponse:
    """Return just the streams table + status HTML for AJAX polling."""
    r = request.app.state.r
    return HTMLResponse(await _streams_fragment_html(r))


@app.get("/streams", response_class=HTMLResponse)
async def show_streams(request: Request) -> HTMLResponse:
    r = request.app.state.r
    fragment = await _streams_fragment_html(r)

    script = """
<script>
(function() {
  var paused = false;
  var btn = document.getElementById('streams-pause-btn');
  var container = document.getElementById('streams-container');

  btn.addEventListener('click', function() {
    paused = !paused;
    btn.textContent = paused ? 'Resume' : 'Pause';
    btn.className = paused ? 'paused' : '';
  });

  function refresh() {
    if (paused) return;
    fetch('/streams/fragment')
      .then(function(r) { return r.text(); })
      .then(function(html) { container.innerHTML = html; })
      .catch(function() {});
  }

  setInterval(refresh, 5000);
})();
</script>
"""

    body = f"""
<h2>Stream Depths</h2>
<div id="streams-container">
{fragment}
</div>
<div class="log-controls">
  <button id="streams-pause-btn">Pause</button>
  <span class="log-meta">Auto-refresh every 5s</span>
</div>
{script}
"""
    return HTMLResponse(_page("Streams", body, path="/streams"))


@app.get("/dlq", response_class=HTMLResponse)
async def show_dlq(request: Request) -> HTMLResponse:
    """Display dead-letter queue entries."""
    r = request.app.state.r
    entries: list[tuple[str, dict[str, str]]] = await r.xrange("stream:dlq", count=50)
    if not entries:
        body = "<h2>Dead Letter Queue</h2>" + _empty_state(
            "DLQ is empty",
            "No failed messages. The pipeline is healthy.",
        )
        return HTMLResponse(_page("Dead Letter Queue", body, path="/dlq"))

    rows = ""
    for entry_id, fields in entries:
        dlq = DLQEnvelope.from_redis_fields(fields)
        safe_id = html.escape(entry_id)
        fc_badge = _badge("error", html.escape(dlq.failure_code))
        service = html.escape(dlq.failed_by or "?")
        attempts = html.escape(str(dlq.dlq_attempts))
        raw_payload = json.dumps(dlq.payload)
        truncated = raw_payload[:80]
        payload_preview = html.escape(truncated)
        if len(raw_payload) > 80:
            payload_preview += "..."
        rows += (
            f"<tr><td>{safe_id}</td><td>{fc_badge}</td>"
            f"<td>{service}</td><td>{attempts}</td>"
            f"<td><code>{payload_preview}</code></td></tr>"
        )

    body = f"""<h2>Dead Letter Queue</h2>
<p>Showing up to 50 entries.
   Use <code>just admin dlq replay &lt;id&gt;</code> to replay a failed message.</p>
<div class="table-scroll">
<table>
  <tr><th>Entry ID</th><th>Failure Code</th>
      <th>Service</th><th>Attempts</th><th>Payload</th></tr>
  {rows}
</table>
</div>
"""
    return HTMLResponse(_page("Dead Letter Queue", body, path="/dlq"))


_MATCH_PAGE_SIZE = 20


def _match_history_html(
    matches: list[tuple[str, dict[str, str], dict[str, str]]],
    puuid: str,
    region: str,
    riot_id: str,
    page: int,
    has_more: bool,
) -> str:
    """Render match history rows + optional next-page link."""
    if not matches:
        return "<p>No match history found.</p>"
    rows = ""
    for _match_id, match, participant in matches:
        game_start = int(match.get("game_start", 0))
        dt = (
            datetime.fromtimestamp(game_start / 1000, tz=UTC).strftime("%Y-%m-%d")
            if game_start
            else "?"
        )
        win = participant.get("win") == "1"
        result = _badge("success", "Win") if win else _badge("error", "Loss")
        champ = html.escape(participant.get("champion_name", "?"))
        role = html.escape(participant.get("team_position", participant.get("role", "?")))
        k = participant.get("kills", "0")
        d = participant.get("deaths", "0")
        a = participant.get("assists", "0")
        mode = html.escape(match.get("game_mode", "?"))
        rows += (
            f"<tr><td>{dt}</td><td>{result}</td><td>{champ}</td><td>{role}</td>"
            f"<td>{k}/{d}/{a}</td><td>{mode}</td></tr>"
        )
    safe_puuid = html.escape(puuid, quote=True)
    safe_region = html.escape(region, quote=True)
    safe_id = html.escape(riot_id, quote=True)
    next_page = ""
    if has_more:
        next_p = page + 1
        next_page = (
            f'<p><a href="#" class="load-matches"'
            f' data-puuid="{safe_puuid}" data-region="{safe_region}"'
            f' data-riot-id="{safe_id}" data-page="{next_p}">'
            f"Load more (page {next_p + 1})</a></p>"
        )
    return (
        f'<div class="table-scroll">'
        f"<table><tr><th>Date</th><th>Result</th><th>Champion</th><th>Role</th>"
        f"<th>K/D/A</th><th>Mode</th></tr>{rows}</table>"
        f"</div>{next_page}"
    )


@app.get("/stats/matches", response_class=HTMLResponse)
async def stats_matches(request: Request) -> HTMLResponse:
    """Return a fragment of match history HTML for lazy loading."""
    puuid = request.query_params.get("puuid", "")
    region = request.query_params.get("region", "na1")
    riot_id = request.query_params.get("riot_id", "")
    try:
        page = int(request.query_params.get("page", "0"))
    except ValueError:
        page = 0

    if not puuid:
        return HTMLResponse("<p class='error'>Missing puuid</p>")

    if not _PUUID_RE.match(puuid):
        return HTMLResponse("<p class='error'>Invalid PUUID format</p>", status_code=400)

    r = request.app.state.r
    start = page * _MATCH_PAGE_SIZE
    stop = start + _MATCH_PAGE_SIZE  # fetch one extra to detect more pages
    # Sorted set score = game_start ms; ZREVRANGEBYSCORE to get newest first
    raw_pairs: list[tuple[str, float]] = await r.zrevrange(
        f"player:matches:{puuid}", start, stop, withscores=True
    )
    has_more = len(raw_pairs) > _MATCH_PAGE_SIZE
    raw_pairs = raw_pairs[:_MATCH_PAGE_SIZE]

    # Batch all HGETALL calls into a single pipeline round-trip
    results: list[tuple[str, dict[str, str], dict[str, str]]] = []
    if raw_pairs:
        async with r.pipeline(transaction=False) as pipe:
            for match_id, _ in raw_pairs:
                pipe.hgetall(f"match:{match_id}")
                pipe.hgetall(f"participant:{match_id}:{puuid}")
            pipe_results: list[dict[str, str]] = await pipe.execute()
        for i, (match_id, _) in enumerate(raw_pairs):
            match_data: dict[str, str] = pipe_results[i * 2]
            participant_data: dict[str, str] = pipe_results[i * 2 + 1]
            results.append((match_id, match_data, participant_data))

    return HTMLResponse(_match_history_html(results, puuid, region, riot_id, page, has_more))


@app.get("/lcu", response_class=HTMLResponse)
async def show_lcu(request: Request) -> HTMLResponse:
    lcu: dict[str, list[dict[str, Any]]] = request.app.state.lcu

    if not lcu:
        body = f"""
<h2>LCU Match History {_badge("warning", "&#9888; Unverified")}</h2>
<p>No LCU data collected yet.</p>
<p>Run <code>just lcu</code> with the League client open, then restart the UI to reload.</p>
"""
        return HTMLResponse(_page("LCU", body, path="/lcu"))

    rows = ""
    for puuid, matches in sorted(lcu.items()):
        if not matches:
            continue
        raw_riot_id = str(matches[0].get("riot_id", puuid[:12]))
        safe_riot_id = html.escape(raw_riot_id)
        if "#" in raw_riot_id:
            stats_href = f"/stats?riot_id={_url_quote(raw_riot_id)}"
            player_cell = f'<a href="{stats_href}">{safe_riot_id}</a>'
        else:
            player_cell = safe_riot_id
        total = len(matches)
        wins = sum(1 for m in matches if m.get("win"))
        by_mode = _aggregate_by_mode(matches)
        mode_str = html.escape(
            ", ".join(f"{mode} ({d['w']}/{d['t']})" for mode, d in sorted(by_mode.items()))
        )
        rows += (
            f"<tr>"
            f"<td>{player_cell}</td>"
            f"<td>{total}</td>"
            f"<td>{wins}</td>"
            f"<td>{total - wins}</td>"
            f"<td>{mode_str}</td>"
            f"</tr>"
        )

    body = f"""
<h2>LCU Match History {_badge("warning", "&#9888; Unverified")}</h2>
<p class="unverified">Data collected from the local League client. Not verified against the Riot
API. Includes game modes unavailable in Match-v5 (ARAM Mayhem, URF, One for All, etc.).</p>
<p>To collect more data: run <code>just lcu</code> with the League client open,
then restart the UI (<code>just restart ui</code>) to reload.</p>
<div class="table-scroll">
<table>
  <tr><th>Player</th><th>Games</th><th>Wins</th><th>Losses</th><th>Modes (W/T)</th></tr>
  {rows}
</table>
</div>
"""
    return HTMLResponse(_page("LCU", body, path="/lcu"))


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

_LOG_LINES = 50
_LOG_LEVEL_CSS = {
    "CRITICAL": "log-critical",
    "ERROR": "log-error",
    "WARNING": "log-warning",
    "DEBUG": "log-debug",
}


_EST_BYTES_PER_LOG_LINE = 600  # heuristic for JSON structured log lines


def _tail_file(path: Path, n: int) -> list[str]:
    """Read last n non-empty lines from a file efficiently (byte-seeks from end)."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            read_bytes = min(n * _EST_BYTES_PER_LOG_LINE, size)
            f.seek(size - read_bytes)
            raw = f.read()
        parts = raw.split(b"\n")
        if size > read_bytes:
            parts = parts[1:]
        lines = [p.decode("utf-8", errors="replace") for p in parts if p.strip()]
        return lines[-n:]
    except OSError:
        return []


def _parse_log_line(line: str) -> tuple[str, str, str, str, str]:
    """Return (timestamp, level, logger, message, extra_kv) from a JSON log line."""
    try:
        d: dict[str, Any] = json.loads(line)
        ts = str(d.pop("timestamp", ""))[:19].replace("T", " ")
        level = str(d.pop("level", "INFO"))
        logger = str(d.pop("logger", ""))
        msg = str(d.pop("message", line))
        extra = "  ".join(f"{k}={v}" for k, v in d.items() if not str(k).startswith("_"))
        return ts, level, logger, msg, extra
    except (json.JSONDecodeError, TypeError, AttributeError):
        return "", "INFO", "", line, ""


def _render_log_lines(raw_lines: list[str]) -> str:
    rows: list[str] = []
    for line in raw_lines:
        ts, level, logger, msg, extra = _parse_log_line(line)
        line_cls = _LOG_LEVEL_CSS.get(level, "")
        badge_cls = _LOG_LEVEL_CSS.get(level, "log-info")
        rows.append(
            f'<div class="log-line {line_cls}">'
            f'<span class="log-ts">{html.escape(ts)}</span>'
            f'<span class="log-badge {badge_cls}">{html.escape(level)}</span>'
            f'<span class="log-svc">{html.escape(logger)}</span>'
            f'<span class="log-msg">{html.escape(msg)}</span>'
            + (f'<span class="log-extra">{html.escape(extra)}</span>' if extra else "")
            + "</div>"
        )
    return "\n".join(rows) if rows else "<p>No log entries found.</p>"


def _merged_log_lines(log_dir: Path, n: int) -> list[str]:
    """Read last n lines from ALL log files, merge by timestamp, return newest n.

    Each per-file tail is already sorted (log files are append-only), so we
    use ``heapq.merge`` on the pre-sorted iterables instead of a full sort.
    We then take the last *n* items with ``collections.deque(maxlen=n)``
    to bound memory when the merged stream is large.
    """
    log_files = list(log_dir.glob("*.log"))
    per_file = max(n // len(log_files) + 1, 10) if log_files else 0

    def _keyed(f: Path) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for line in _tail_file(f, per_file):
            try:
                d = json.loads(line)
                ts = str(d.get("timestamp", ""))
            except (json.JSONDecodeError, TypeError):
                ts = ""
            result.append((ts, line))
        return result

    per_file_iters = [_keyed(f) for f in log_files]
    merged = heapq.merge(*per_file_iters, key=lambda x: x[0])
    tail: collections.deque[tuple[str, str]] = collections.deque(merged, maxlen=n)
    return [line for _, line in tail]


@app.get("/logs/fragment", response_class=HTMLResponse)
async def logs_fragment() -> HTMLResponse:
    """Return just the log lines HTML for AJAX polling."""
    log_dir_env = os.getenv("LOG_DIR", "")
    if not log_dir_env:
        return HTMLResponse("<p>LOG_DIR not configured.</p>")
    log_dir = Path(log_dir_env)
    lines = _merged_log_lines(log_dir, _LOG_LINES)
    return HTMLResponse(_render_log_lines(lines))


@app.get("/logs", response_class=HTMLResponse)
async def show_logs() -> HTMLResponse:
    log_dir_env = os.getenv("LOG_DIR", "")
    if not log_dir_env:
        return HTMLResponse(
            _page(
                "Logs",
                "<h2>Logs</h2><p>LOG_DIR not configured. Add it to docker-compose.yml.</p>",
                path="/logs",
            )
        )

    log_dir = Path(log_dir_env)
    log_files = sorted(log_dir.glob("*.log"))
    if not log_files:
        return HTMLResponse(
            _page(
                "Logs",
                f"<h2>Logs</h2><p>No log files found in <code>{html.escape(log_dir_env)}</code>."
                " Services may not have started yet.</p>",
                path="/logs",
            )
        )

    lines = _merged_log_lines(log_dir, _LOG_LINES)
    svc_list = ", ".join(f.stem for f in log_files)
    log_content = f'<div class="log-wrap" id="log-container">{_render_log_lines(lines)}</div>'

    script = """
<script>
(function() {
  var paused = false;
  var btn = document.getElementById('pause-btn');
  var container = document.getElementById('log-container');
  var timer;

  btn.addEventListener('click', function() {
    paused = !paused;
    btn.textContent = paused ? 'Resume' : 'Pause';
    btn.className = paused ? 'paused' : '';
  });

  function refresh() {
    if (paused) return;
    fetch('/logs/fragment')
      .then(function(r) { return r.text(); })
      .then(function(html) { container.innerHTML = html; })
      .catch(function() {});
  }

  timer = setInterval(refresh, 2000);
})();
</script>
"""

    body = (
        f"<h2>Logs</h2>"
        f'<div class="log-controls">'
        f'<button id="pause-btn">Pause</button>'
        f'<span class="log-meta">All services: {html.escape(svc_list)} &mdash; '
        f"last {_LOG_LINES} lines, auto-refresh 2s</span>"
        f"</div>"
        f"{log_content}{script}"
    )
    return HTMLResponse(_page("Logs", body, path="/logs"))
