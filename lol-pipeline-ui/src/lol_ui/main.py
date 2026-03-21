"""Web UI — view player stats."""

from __future__ import annotations

import asyncio
import collections
import heapq
import html
import json
import math
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote as _url_quote

import httpx
import redis.asyncio as aioredis
import redis.exceptions
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from lol_pipeline.config import Config
from lol_pipeline.helpers import name_cache_key
from lol_pipeline.log import get_logger
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from lol_pipeline.priority import has_priority_players, set_priority
from lol_pipeline.rate_limiter import wait_for_token
from lol_pipeline.redis_client import get_redis
from lol_pipeline.resolve import CACHE_TTL_S
from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    RiotClient,
    ServerError,
)
from lol_pipeline.streams import publish
from starlette.responses import Response

_STREAM_PUUID = "stream:puuid"
_PUUID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_STREAM_ENTRY_ID_RE = re.compile(r"^\d+-\d+$")
_NAME_CACHE_INDEX = "name_cache:index"
_NAME_CACHE_MAX = 10_000
_AUTOSEED_COOLDOWN_S = 300  # 5 minutes
_log = get_logger("ui")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg = Config()
    app.state.cfg = cfg
    app.state.r = get_redis(cfg.redis_url)
    app.state.riot = RiotClient(cfg.riot_api_key, r=app.state.r)

    yield

    await app.state.r.aclose()
    await app.state.riot.close()


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

_NAV_ITEMS = [
    ("/", "Dashboard"),
    ("/stats", "Stats"),
    ("/players", "Players"),
    ("/streams", "Streams"),
    ("/dlq", "DLQ"),
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
  --color-critical: #c00;
  --color-error-bg: #cc3333;
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
  max-width: min(900px, calc(100% - 2rem));
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
  box-sizing: border-box;
  max-width: 100%;
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
.error-msg { color: var(--color-error); padding: var(--space-sm) 0; }
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
.badge--error { background: var(--color-error-bg); color: #fff; }
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
.table-scroll td, .table-scroll th { white-space: nowrap; }

/* Small button */
.btn-sm { padding: var(--space-xs) var(--space-sm); font-size: var(--font-size-sm);
          min-height: 44px; }

/* Pagination links — accessible touch targets */
.page-link { display: inline-flex; align-items: center; min-height: 44px;
             padding: 0 var(--space-sm); }

/* Utility */
.text-right { text-align: right; }

/* Banners */
.banner { padding: var(--space-md); border-radius: var(--radius); margin: var(--space-md) 0;
          border-left: 4px solid; }
.banner--error { background: color-mix(in srgb, var(--color-error) 10%, transparent);
                border-color: var(--color-error); }
.banner--success { background: color-mix(in srgb, var(--color-success) 10%, transparent);
                  border-color: var(--color-success); }
.banner--warning { background: color-mix(in srgb, var(--color-warning) 10%, transparent);
                  border-color: var(--color-warning); }

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
.log-line { display: flex; flex-direction: column; gap: 2px; padding: 2px 4px;
  border-bottom: 1px solid var(--color-border); flex-wrap: nowrap; }
.log-critical { background: color-mix(in srgb, var(--color-error) 15%, transparent);
               font-weight: bold; }
.log-error { background: color-mix(in srgb, var(--color-error) 8%, transparent); }
.log-warning { background: color-mix(in srgb, var(--color-warning) 8%, transparent); }
.log-debug { color: var(--color-muted); }
.log-ts { color: var(--color-muted); white-space: nowrap; flex-shrink: 0; }
.log-badge { padding: 0 4px; border-radius: 2px;
  font-size: 0.75em; white-space: nowrap; flex-shrink: 0; }
.log-badge.log-critical { background: var(--color-critical); color: #fff; }
.log-badge.log-error { background: var(--color-error); color: #fff; }
.log-badge.log-warning { background: var(--color-warning); color: #111; }
.log-badge.log-debug { background: var(--color-border); color: var(--color-text); }
.log-badge.log-info { background: var(--color-info); color: #fff; }
.log-svc { color: var(--color-info); flex-shrink: 0; }
.log-msg { flex: 1; }
.log-extra { color: var(--color-muted); font-size: 0.9em; }
.log-controls { margin: 0.5rem 0; display: flex;
  gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.log-meta { color: var(--color-muted); font-size: 0.85em; margin-bottom: 0.3rem; }
#pause-btn { padding: var(--space-sm) var(--space-lg); min-height: 44px; cursor: pointer; }
#pause-btn.paused, #streams-pause-btn.paused { background: var(--color-error); color: #fff; }

.log-ts, .log-badge, .log-svc { font-size: 0.75em; }

#player-search { width: 100%; }

/* Mobile overrides */
@media (max-width: 767px) {
  body { margin: 1rem auto; }
  .site-footer { padding: var(--space-md) var(--space-sm); }
}

/* Tablet (768px+) */
@media (min-width: 768px) {
  .form-inline { flex-direction: row; flex-wrap: wrap; align-items: flex-end; }
  .form-inline label { flex: 1; min-width: 0; }
  .form-inline input, .form-inline select { width: 100%; }
  .form-inline button { width: auto; }
  body { padding: 0 1rem; }
  .log-line { flex-direction: row; gap: 0.5rem; align-items: baseline; }
  .log-ts, .log-badge, .log-svc { font-size: inherit; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
  #player-search { width: auto; }
}

/* Wide desktop (1440px+) */
@media (min-width: 1440px) {
  body { max-width: 1200px; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
}

/* Spinner */
@keyframes _spin { to { transform: rotate(360deg); } }
.spinner {
  display: inline-block;
  width: 18px; height: 18px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-info);
  border-radius: 50%;
  animation: _spin 0.7s linear infinite;
  vertical-align: middle;
  margin-left: var(--space-sm);
}

/* Dashboard grid */
.dashboard-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-md);
  margin: var(--space-md) 0;
}
@media (min-width: 768px) { .dashboard-grid { grid-template-columns: repeat(2, 1fr); } }
@media (min-width: 1440px) { .dashboard-grid { grid-template-columns: repeat(3, 1fr); } }

/* Sort controls */
.sort-controls { display: flex; gap: var(--space-sm); align-items: center;
                 margin-bottom: var(--space-sm); flex-wrap: wrap; }
.sort-controls a { padding: var(--space-xs) var(--space-sm); border-radius: var(--radius);
                   text-decoration: none; color: var(--color-muted);
                   border: 1px solid var(--color-border); font-size: var(--font-size-sm);
                   min-height: 44px; display: inline-flex; align-items: center; }
.sort-controls a.active { color: var(--color-text); border-color: var(--color-info);
                           background: color-mix(in srgb, var(--color-info) 10%, transparent); }
.sort-controls span { font-size: var(--font-size-sm); color: var(--color-muted); }

/* Footer */
.site-footer { text-align: center; padding: var(--space-lg); color: var(--color-muted);
  font-size: var(--font-size-sm); border-top: 1px solid var(--color-border);
  margin-top: var(--space-xl); }

/* Loading state */
.loading-state { display: flex; align-items: center; gap: var(--space-sm);
  color: var(--color-muted); padding: var(--space-lg); }

/* Champion icons */
.champion-icon { width: 32px; height: 32px; border-radius: 4px;
  vertical-align: middle; margin-right: var(--space-xs); }

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


def _format_stat_value(key: str, value: str) -> str:  # noqa: PLR0911
    """Format a stat value for display.

    win_rate is multiplied by 100 and shown as %. Averages and kda rounded to 2dp.
    """
    if key == "win_rate":
        try:
            fval = float(value)
            if not math.isfinite(fval):
                return "N/A"
            return f"{fval * 100:.1f}%"
        except ValueError:
            return value
    if key.startswith("avg_") or key == "kda":
        try:
            fval = float(value)
            if not math.isfinite(fval):
                return "N/A"
            return f"{fval:.2f}"
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
    """Render a status badge with auto-escaped text (safe for user-supplied input).

    variant: success|error|warning|info|muted.
    """
    if variant not in _BADGE_VARIANTS:
        msg = f"Invalid badge variant: {variant}"
        raise ValueError(msg)
    return f'<span class="badge badge--{variant}">{html.escape(text)}</span>'


def _badge_html(variant: str, raw_html: str) -> str:
    """Render a status badge with raw HTML content (for trusted HTML entities).

    Use this ONLY for trusted content like ``&#10003;``. For user data, use ``_badge()``.
    variant: success|error|warning|info|muted.
    """
    if variant not in _BADGE_VARIANTS:
        msg = f"Invalid badge variant: {variant}"
        raise ValueError(msg)
    return f'<span class="badge badge--{variant}">{raw_html}</span>'


def _empty_state(title: str, body_html: str) -> str:
    """Render an empty-state message. Both params are raw HTML -- callers MUST
    pre-escape any dynamic content with html.escape().
    """
    return f'<div class="empty-state"><p><strong>{title}</strong></p><p>{body_html}</p></div>'


# ---------------------------------------------------------------------------
# Data Dragon (champion icons)
# ---------------------------------------------------------------------------

_DDRAGON_VERSION_KEY = "ddragon:version"
_DDRAGON_TTL_S = 86400  # 24 hours


async def _get_ddragon_version(r: aioredis.Redis) -> str | None:
    """Return the current Data Dragon version, cached in Redis for 24h."""
    cached = await r.get(_DDRAGON_VERSION_KEY)
    if cached:
        return str(cached)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://ddragon.leagueoflegends.com/api/versions.json")
            resp.raise_for_status()
            versions: list[str] = resp.json()
            version = versions[0]
            await r.set(_DDRAGON_VERSION_KEY, version, ex=_DDRAGON_TTL_S)
            return version
    except Exception:
        return None


def _champion_icon_html(champion_name: str, version: str | None) -> str:
    """Return an <img> tag for the champion icon, or empty string on failure.

    champion_name is the in-game name (e.g. "MonkeyKing" for Wukong).
    """
    if not version or not champion_name:
        return ""
    safe_name = html.escape(champion_name)
    safe_version = html.escape(version)
    url = f"https://ddragon.leagueoflegends.com/cdn/{safe_version}/img/champion/{safe_name}.png"
    return (
        f'<img src="{url}" alt="{safe_name}" class="champion-icon"'
        f' loading="lazy" onerror="this.style.display=\'none\'">'
    )


def _page(title: str, body: str, path: str = "") -> str:
    nav_links = []
    for href, label in _NAV_ITEMS:
        active = (href != "/" and path.startswith(href)) or href == path
        cls = ' class="active" aria-current="page"' if active else ""
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
<a class="skip-link" href="#main-content">Skip to content</a>
<h1>LoL Pipeline</h1>
<nav aria-label="Main navigation">
  {nav_html}
</nav>
<hr>
<main id="main-content">
{body}
</main>
<footer class="site-footer">
  LoL Pipeline isn&rsquo;t endorsed by Riot Games and doesn&rsquo;t
  reflect the views or opinions of Riot Games or anyone officially
  involved in producing or managing Riot Games properties.
  League of Legends and Riot Games are trademarks or registered
  trademarks of Riot Games, Inc.
</footer>
</body>
</html>"""


_HALT_BANNER = (
    '<div class="banner banner--error">&#9888; System is HALTED — all workers have stopped</div>'
)


_DLQ_DEFAULT_PER_PAGE = 25
_DLQ_MAX_PER_PAGE = 50


def _make_replay_envelope(dlq: DLQEnvelope, max_attempts: int) -> MessageEnvelope:
    """Reconstruct a MessageEnvelope from a DLQEnvelope for replay."""
    original_type = dlq.original_stream.removeprefix("stream:")
    return MessageEnvelope(
        source_stream=dlq.original_stream,
        type=original_type,
        payload=dlq.payload,
        max_attempts=max_attempts,
        enqueued_at=dlq.enqueued_at,
        dlq_attempts=dlq.dlq_attempts,
        priority=dlq.priority,
    )


_REGIONS = [
    "na1",
    "br1",
    "la1",
    "la2",
    "euw1",
    "eun1",
    "tr1",
    "ru",
    "kr",
    "jp1",
    "oc1",
    "ph2",
    "sg2",
    "th2",
    "tw2",
    "vn2",
]

_REGIONS_SET = frozenset(_REGIONS)


def _stats_form(
    msg: str = "",
    css_class: str = "",
    stats_html: str = "",
    selected_region: str = "na1",
    value: str = "",
) -> str:
    msg_html = f'<p class="{css_class}">{msg}</p>' if msg else ""
    options = "\n      ".join(
        f'<option value="{r}"{" selected" if r == selected_region else ""}>{r}</option>'
        for r in _REGIONS
    )
    escaped_value = html.escape(value, quote=True)
    return _page(
        "Player Stats",
        f"""
<h2>Player Stats</h2>
{msg_html}
<form class="form-inline" method="get" action="/stats">
  <label>Riot ID:
    <input name="riot_id" placeholder="GameName#TagLine" required value="{escaped_value}">
  </label>
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
<h3>Verified (Riot API) {_badge_html("success", "&#10003; Verified")}</h3>
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
  var isFirst = page === 0;
  if (isFirst) {{
    container.innerHTML = '<div class="loading-state">'
      + '<span class="spinner"></span> Loading match history\u2026</div>';
  }} else {{
    var btn = container.querySelector('.load-matches');
    if (btn) {{ btn.textContent = 'Loading\u2026'; btn.style.pointerEvents = 'none'; }}
  }}
  var url = '/stats/matches?puuid=' + encodeURIComponent(puuid)
    + '&region=' + encodeURIComponent(region)
    + '&riot_id=' + encodeURIComponent(riotId)
    + '&page=' + page;
  fetch(url, {{headers: {{'Accept': 'text/html'}}}})
    .then(function(r) {{ if (!r.ok) {{ throw new Error('HTTP ' + r.status); }} return r.text(); }})
    .then(function(html) {{
      if (isFirst) {{
        container.innerHTML = html;
      }} else {{
        var tmp = document.createElement('div');
        tmp.innerHTML = html;
        var existingTbl = container.querySelector('table');
        var newTbl = tmp.querySelector('table');
        if (existingTbl && newTbl) {{
          Array.from(newTbl.rows).slice(1).forEach(function(row) {{
            existingTbl.appendChild(row.cloneNode(true));
          }});
        }}
        var oldLink = container.querySelector('.load-matches');
        if (oldLink) {{ oldLink.closest('p').remove(); }}
        var newLink = tmp.querySelector('.load-matches');
        if (newLink) {{ container.appendChild(newLink.closest('p')); }}
      }}
    }})
    .catch(function(e) {{
      container.textContent = '';
      var p = document.createElement('p');
      p.className = 'error';
      p.textContent = 'Failed to load: ' + (e.message || e);
      container.appendChild(p);
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app = FastAPI(title="LoL Pipeline UI", lifespan=_lifespan)


@app.middleware("http")
async def add_security_headers(request: Request, call_next: Any) -> Response:
    """Add security headers to every response."""
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "img-src 'self' ddragon.leagueoflegends.com data:; "
        "connect-src 'self'"
    )
    return response


@app.exception_handler(redis.exceptions.RedisError)
async def redis_error_handler(request: Request, exc: redis.exceptions.RedisError) -> HTMLResponse:
    """Return a user-friendly 503 page when Redis is unreachable."""
    body = _page(
        "Error",
        "<p>Cannot connect to Redis. Is the stack running? Try: <code>just up</code></p>",
    )
    return HTMLResponse(content=body, status_code=503)


@app.exception_handler(ConnectionError)
async def connection_error_handler(request: Request, exc: ConnectionError) -> HTMLResponse:
    """Return a user-friendly 503 page on connection errors."""
    body = _page(
        "Error",
        "<p>Cannot connect to Redis. Is the stack running? Try: <code>just up</code></p>",
    )
    return HTMLResponse(content=body, status_code=503)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
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
    <tr><th>Key</th><th class="text-right">Length</th><th>Status</th></tr>
    {stream_rows}
  </table>
  </div>
</div>

<div class="card">
  <h3 class="card__title">Look Up a Player</h3>
  <p style="color:var(--color-muted);font-size:var(--font-size-sm)">
    Enter a Riot ID to view stats or auto-seed the player into the pipeline.
  </p>
  <form class="form-inline" method="get" action="/stats">
    <label>Riot ID:
      <input name="riot_id" placeholder="GameName#TagLine" required>
    </label>
    <label>Region:
      <select name="region">
        {region_options}
      </select>
    </label>
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


async def _resolve_and_cache_puuid(
    r: Any,
    riot: RiotClient,
    riot_id: str,
    game_name: str,
    tag_line: str,
    region: str,
    cfg: Config,
) -> str | HTMLResponse:
    """Look up PUUID from cache or Riot API.

    Returns the PUUID string on success, or an HTMLResponse on error.
    """
    cache_key = name_cache_key(game_name, tag_line)
    cached_puuid: str | None = await r.get(cache_key)
    if cached_puuid:
        return cached_puuid
    try:
        await wait_for_token(r, limit_per_second=cfg.api_rate_limit_per_second)
        account = await riot.get_account_by_riot_id(game_name, tag_line, region)
    except NotFoundError:
        return HTMLResponse(
            _stats_form(
                "Player not found. Check the spelling of the Riot ID.",
                "error",
                selected_region=region,
                value=riot_id,
            )
        )
    except RateLimitError:
        return HTMLResponse(
            _stats_form(
                "Rate limited. Try again in a few seconds.",
                "warning",
                selected_region=region,
                value=riot_id,
            )
        )
    except AuthError:
        return HTMLResponse(
            _stats_form(
                "API key issue. An admin must run <code>just admin system-resume</code>.",
                "error",
                selected_region=region,
                value=riot_id,
            )
        )
    except ServerError:
        return HTMLResponse(
            _stats_form(
                "Riot servers temporarily unavailable. Try again later.",
                "warning",
                selected_region=region,
                value=riot_id,
            )
        )
    puuid: str = account["puuid"]
    await r.set(cache_key, puuid, ex=CACHE_TTL_S)
    now_ts = datetime.now(tz=UTC).timestamp()
    cache_size = int(await r.zcard(_NAME_CACHE_INDEX))
    if cache_size >= _NAME_CACHE_MAX:
        await r.zremrangebyrank(_NAME_CACHE_INDEX, 0, 0)
    await r.zadd(_NAME_CACHE_INDEX, {cache_key: now_ts})
    return puuid


async def _auto_seed_player(
    r: Any,
    puuid: str,
    game_name: str,
    tag_line: str,
    region: str,
    cfg: Config,
) -> HTMLResponse:
    """Auto-seed a player if not yet seeded, returning an appropriate status page."""
    riot_id = f"{game_name}#{tag_line}"
    safe_id = html.escape(riot_id)
    if await r.get("system:halted"):
        return HTMLResponse(
            _stats_form(
                f"System halted. No stats yet for {safe_id}.",
                "error",
                selected_region=region,
                value=riot_id,
            )
        )
    cooldown_key = f"autoseed:cooldown:{puuid}"
    if await r.get(cooldown_key):
        return HTMLResponse(
            _stats_form(
                f"{safe_id} was seeded recently — pipeline processing. Check back soon.",
                "warning",
                selected_region=region,
                value=riot_id,
            )
        )
    existing_seeded: str | None = await r.hget(f"player:{puuid}", "seeded_at")
    if existing_seeded:
        return HTMLResponse(
            _stats_form(
                f"{safe_id} was seeded recently — pipeline processing. Check back soon.",
                "warning",
                selected_region=region,
                value=riot_id,
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
    # Set priority before publishing so clear_priority() by downstream
    # services cannot race against a not-yet-set priority key.
    await set_priority(r, puuid)
    now_ts = time.time()
    await r.zadd("players:all", {puuid: now_ts})
    await r.zremrangebyrank("players:all", 0, -50001)
    await publish(r, _STREAM_PUUID, envelope)
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
    await r.set(cooldown_key, "1", ex=_AUTOSEED_COOLDOWN_S)
    _log.info("auto-seeded via stats lookup", extra={"puuid": puuid})
    return HTMLResponse(
        _stats_form(
            f"&#10003; Auto-seeded {safe_id} — pipeline processing. Refresh in a minute.",
            "success",
            selected_region=region,
            value=riot_id,
        )
    )


async def _build_stats_response(
    r: Any,
    puuid: str,
    game_name: str,
    tag_line: str,
    region: str,
    riot_id: str,
    stats: dict[str, str],
) -> HTMLResponse:
    """Read Redis hashes and build the stats HTML response."""
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
        else "<p class='warning'>No verified API stats yet (pipeline still processing).</p>"
    )
    history_html = _match_history_section(puuid, region, riot_id)
    safe_name = html.escape(f"{game_name}#{tag_line}")
    heading = f"Stats for {safe_name}{priority_html}"
    return HTMLResponse(
        _stats_form(
            heading, "success", api_html + history_html, selected_region=region, value=riot_id
        )
    )


@app.get("/stats", response_class=HTMLResponse)
async def show_stats(request: Request) -> HTMLResponse:
    riot_id = request.query_params.get("riot_id", "")
    region = request.query_params.get("region", "na1")
    if region not in _REGIONS_SET:
        safe_region = html.escape(region)
        return HTMLResponse(
            _stats_form(f"Invalid region: {safe_region}", "error", value=riot_id),
            status_code=400,
        )

    if not riot_id:
        return HTMLResponse(_stats_form(selected_region=region))

    if "#" not in riot_id:
        return HTMLResponse(
            _stats_form(
                "Invalid Riot ID — expected GameName#TagLine",
                "error",
                selected_region=region,
                value=riot_id,
            )
        )

    game_name, tag_line = riot_id.split("#", 1)
    r = request.app.state.r
    cfg: Config = request.app.state.cfg
    riot: RiotClient = request.app.state.riot

    result = await _resolve_and_cache_puuid(r, riot, riot_id, game_name, tag_line, region, cfg)
    if isinstance(result, HTMLResponse):
        return result
    puuid = result

    stats: dict[str, str] = await r.hgetall(f"player:stats:{puuid}")

    if not stats:
        return await _auto_seed_player(r, puuid, game_name, tag_line, region, cfg)

    return await _build_stats_response(r, puuid, game_name, tag_line, region, riot_id, stats)


_PLAYERS_PAGE_SIZE = 25


_PLAYERS_SORT_OPTIONS = frozenset({"date", "name", "region"})

_PlayerRow = tuple[str, str, str, str]  # (game_name, tag_line, region, seeded_at)


def _apply_player_sort(rows: list[_PlayerRow], sort: str) -> list[_PlayerRow]:
    """Return rows sorted by the given key; mutates and returns the list."""
    if sort == "name":
        rows.sort(key=lambda p: p[0].lower())
    elif sort == "region":
        rows.sort(key=lambda p: (p[2].lower(), p[0].lower()))
    return rows


def _render_player_rows(rows: list[_PlayerRow]) -> str:
    """Render player rows as HTML table rows."""
    html_rows = ""
    for game_name, tag_line, region, seeded_at in rows:
        href = (
            f"/stats?riot_id={_url_quote(game_name + '#' + tag_line)}"
            f"&amp;region={html.escape(region)}"
        )
        safe_name = html.escape(f"{game_name}#{tag_line}")
        seeded = html.escape(seeded_at[:10]) if seeded_at else "?"
        html_rows += (
            f'<tr><td><a href="{href}">{safe_name}</a></td>'
            f"<td>{html.escape(region)}</td><td>{seeded}</td></tr>"
        )
    return html_rows


@app.get("/players", response_class=HTMLResponse)
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
    sep = "&nbsp;&nbsp;" if has_prev else ""
    next_link = (
        f'<a class="page-link" href="/players?sort={sort}&amp;page={page + 1}">Next &rarr;</a>'
        if has_next
        else ""
    )
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

    body = f"""{halt_html}<h2>Players ({total} total, page {page + 1} of {total_pages})</h2>
{sort_controls}
<input id="player-search" placeholder="Filter players..." type="text"
  aria-label="Filter players by name">
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
    """Build the inner HTML for the streams table + status (no page wrapper).

    Uses a single Redis pipeline round-trip for all 9 calls (6 XLEN + 1 ZCARD + 2 GET).
    """
    async with r.pipeline(transaction=False) as pipe:
        for s in _STREAM_KEYS:
            pipe.xlen(s)
        pipe.zcard("delayed:messages")
        pipe.get("system:halted")
        results = await pipe.execute()

    # Unpack: 6 XLEN results, 1 ZCARD, 1 GET
    stream_lengths: list[int] = results[: len(_STREAM_KEYS)]
    delayed: int = results[len(_STREAM_KEYS)]
    halted = results[len(_STREAM_KEYS) + 1]

    has_priority = await has_priority_players(r)

    rows = ""
    for s, length in zip(_STREAM_KEYS, stream_lengths, strict=True):
        status_badge = _depth_badge(s, length)
        rows += f'<tr><td>{s}</td><td class="text-right">{length}</td><td>{status_badge}</td></tr>'
    delayed_badge = _depth_badge("delayed:messages", delayed)
    rows += (
        f"<tr><td>delayed:messages</td>"
        f'<td class="text-right">{delayed}</td><td>{delayed_badge}</td></tr>'
    )

    status = (
        '<div class="banner banner--error">'
        "&#9888; System is HALTED &mdash; all workers have stopped</div>"
        if halted
        else '<div class="banner banner--success">&#10003; System running</div>'
    )

    priority_display = "Yes" if has_priority else "No"

    return f"""{status}
<p>Priority players in-flight: <strong>{priority_display}</strong></p>
<div class="table-scroll">
<table class="streams">
  <tr><th>Key</th><th class="text-right">Length</th><th>Status</th></tr>
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
  var spinner = document.getElementById('streams-spinner');

  btn.addEventListener('click', function() {
    paused = !paused;
    btn.textContent = paused ? 'Resume' : 'Pause';
    btn.classList.toggle('paused', paused);
    btn.setAttribute('aria-label', paused ? 'Resume auto-refresh' : 'Pause auto-refresh');
  });

  function refresh() {
    if (paused) return;
    spinner.style.display = 'inline-block';
    fetch('/streams/fragment')
      .then(function(r) { if (!r.ok) { throw new Error('HTTP ' + r.status); } return r.text(); })
      .then(function(html) { container.innerHTML = html; spinner.style.display = 'none'; })
      .catch(function(e) {
        spinner.style.display = 'none';
        var existing = container.querySelector('.error-msg');
        if (existing) existing.remove();
        var msg = document.createElement('p');
        msg.className = 'error-msg';
        msg.textContent = 'Failed to refresh streams: ' + (e.message || 'network error');
        container.prepend(msg);
      });
  }

  setInterval(refresh, 5000);
})();
</script>
"""

    body = f"""
<h2>Streams</h2>
<div id="streams-container">
{fragment}
</div>
<div class="log-controls">
  <button id="streams-pause-btn" aria-label="Pause auto-refresh">Pause</button>
  <div class="spinner" id="streams-spinner" style="display:none"></div>
  <span class="log-meta">Auto-refresh every 5s</span>
</div>
{script}
"""
    return HTMLResponse(_page("Streams", body, path="/streams"))


@app.get("/dlq", response_class=HTMLResponse)
async def show_dlq(request: Request) -> HTMLResponse:
    """Display dead-letter queue entries with pagination."""
    r = request.app.state.r
    halted = await r.get("system:halted")
    halt_html = _HALT_BANNER if halted else ""
    try:
        page = int(request.query_params.get("page", "0"))
    except ValueError:
        page = 0
    try:
        per_page = min(
            int(request.query_params.get("per_page", str(_DLQ_DEFAULT_PER_PAGE))), _DLQ_MAX_PER_PAGE
        )
    except ValueError:
        per_page = _DLQ_DEFAULT_PER_PAGE
    per_page = max(per_page, 1)
    page = max(page, 0)

    # Fetch one extra to detect next page
    fetch_count = page * per_page + per_page + 1
    all_entries: list[tuple[str, dict[str, str]]] = await r.xrange("stream:dlq", count=fetch_count)
    if not all_entries:
        body = (
            halt_html
            + "<h2>Dead Letter Queue</h2>"
            + _empty_state(
                "DLQ is empty",
                "No failed messages. The pipeline is healthy.",
            )
        )
        return HTMLResponse(_page("Dead Letter Queue", body, path="/dlq"))

    start = page * per_page
    page_entries = all_entries[start : start + per_page]
    has_next = len(all_entries) > start + per_page
    has_prev = page > 0

    rows = ""
    for entry_id, fields in page_entries:
        try:
            dlq = DLQEnvelope.from_redis_fields(fields)
        except Exception:
            _log.warning("skipping corrupt DLQ entry", extra={"entry_id": entry_id})
            continue
        safe_id = html.escape(entry_id)
        fc_badge = _badge("error", dlq.failure_code)
        service = html.escape(dlq.failed_by or "?")
        attempts = html.escape(str(dlq.dlq_attempts))
        raw_payload = json.dumps(dlq.payload)
        truncated = raw_payload[:80]
        payload_preview = html.escape(truncated)
        if len(raw_payload) > 80:
            payload_preview += "..."
        orig_stream = html.escape(dlq.original_stream or "?")
        replay_form = (
            f'<form method="post" action="/dlq/replay/{_url_quote(entry_id)}"'
            f' style="display:inline">'
            f'<button type="submit" class="btn-sm"'
            f' aria-label="Replay DLQ entry {safe_id}">'
            f"Replay</button></form>"
        )
        rows += (
            f"<tr><td>{safe_id}</td><td>{fc_badge}</td>"
            f"<td>{orig_stream}</td><td>{service}</td><td>{attempts}</td>"
            f"<td><code>{payload_preview}</code></td><td>{replay_form}</td></tr>"
        )

    prev_link = (
        f'<a class="page-link" href="/dlq?page={page - 1}&amp;per_page={per_page}">&larr; Prev</a>'
        if has_prev
        else ""
    )
    next_link = (
        f'<a class="page-link" href="/dlq?page={page + 1}&amp;per_page={per_page}">Next &rarr;</a>'
        if has_next
        else ""
    )
    sep = "&nbsp;&nbsp;" if has_prev and has_next else ""
    page_label = f"page {page + 1}"
    pagination = (
        f"<p>{prev_link}{sep}{page_label}{sep}{next_link}</p>" if prev_link or next_link else ""
    )

    body = f"""{halt_html}<h2>Dead Letter Queue</h2>
<p>Showing {per_page} entries per page.</p>
<div class="table-scroll">
<table>
  <tr><th>Entry ID</th><th>Failure Code</th>
      <th>Original Stream</th><th>Service</th><th>Attempts</th>
      <th>Payload</th><th>Action</th></tr>
  {rows}
</table>
</div>
{pagination}
"""
    return HTMLResponse(_page("Dead Letter Queue", body, path="/dlq"))


@app.post("/dlq/replay/{entry_id:path}")
async def dlq_replay(request: Request, entry_id: str) -> Response:
    """Replay a single DLQ entry back to its original stream."""
    if not _STREAM_ENTRY_ID_RE.match(entry_id):
        raise HTTPException(status_code=400, detail="Invalid entry ID format")
    r = request.app.state.r
    cfg: Config = request.app.state.cfg
    entries: list[tuple[str, dict[str, str]]] = await r.xrange(
        "stream:dlq", min=entry_id, max=entry_id, count=1
    )
    if not entries:
        safe_id = html.escape(entry_id)
        body = (
            f"<h2>DLQ Replay Failed</h2>"
            f'<div class="banner banner--error">Entry {safe_id} not found.</div>'
            f'<p><a href="/dlq">&larr; Back to DLQ</a></p>'
        )
        return HTMLResponse(_page("DLQ Replay Failed", body, path="/dlq"), status_code=404)
    _eid, fields = entries[0]
    try:
        dlq = DLQEnvelope.from_redis_fields(fields)
    except Exception:
        _log.warning("corrupt DLQ entry during replay", extra={"entry_id": entry_id})
        safe_id = html.escape(entry_id)
        body = (
            f"<h2>DLQ Replay Failed</h2>"
            f'<div class="banner banner--error">Entry {safe_id} is corrupt '
            f"and cannot be replayed.</div>"
            f'<p><a href="/dlq">&larr; Back to DLQ</a></p>'
        )
        return HTMLResponse(_page("DLQ Replay Failed", body, path="/dlq"), status_code=422)
    envelope = _make_replay_envelope(dlq, cfg.max_attempts)
    await publish(r, dlq.original_stream, envelope)
    await r.xdel("stream:dlq", entry_id)
    return RedirectResponse("/dlq", status_code=303)


_MATCH_PAGE_SIZE = 20


def _match_history_html(
    matches: list[tuple[str, dict[str, str], dict[str, str]]],
    puuid: str,
    region: str,
    riot_id: str,
    page: int,
    has_more: bool,
    version: str | None = None,
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
        raw_champ = participant.get("champion_name", "?")
        champ = html.escape(raw_champ)
        icon = _champion_icon_html(raw_champ, version)
        role = html.escape(participant.get("team_position", participant.get("role", "?")))
        k = participant.get("kills", "0")
        d = participant.get("deaths", "0")
        a = participant.get("assists", "0")
        mode = html.escape(match.get("game_mode", "?"))
        rows += (
            f"<tr><td>{dt}</td><td>{result}</td><td>{icon}{champ}</td><td>{role}</td>"
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
    halted = await r.get("system:halted")
    halt_html = _HALT_BANNER if halted else ""
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

    version = await _get_ddragon_version(r)
    return HTMLResponse(
        halt_html + _match_history_html(results, puuid, region, riot_id, page, has_more, version)
    )


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
    except json.JSONDecodeError, TypeError, AttributeError:
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
            except json.JSONDecodeError, TypeError:
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
    lines = await asyncio.to_thread(_merged_log_lines, log_dir, _LOG_LINES)
    return HTMLResponse(_render_log_lines(lines))


@app.get("/logs", response_class=HTMLResponse)
async def show_logs(request: Request) -> HTMLResponse:
    r = request.app.state.r
    halted = await r.get("system:halted")
    halt_html = _HALT_BANNER if halted else ""

    log_dir_env = os.getenv("LOG_DIR", "")
    if not log_dir_env:
        return HTMLResponse(
            _page(
                "Logs",
                halt_html
                + "<h2>Logs</h2><p>LOG_DIR not configured. Add it to docker-compose.yml.</p>",
                path="/logs",
            )
        )

    log_dir = Path(log_dir_env)
    log_files = sorted(log_dir.glob("*.log"))
    if not log_files:
        return HTMLResponse(
            _page(
                "Logs",
                halt_html
                + f"<h2>Logs</h2><p>No log files found in <code>{html.escape(log_dir_env)}</code>."
                " Services may not have started yet.</p>",
                path="/logs",
            )
        )

    lines = await asyncio.to_thread(_merged_log_lines, log_dir, _LOG_LINES)
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
    btn.classList.toggle('paused', paused);
    btn.setAttribute('aria-label', paused ? 'Resume auto-refresh' : 'Pause auto-refresh');
  });

  function refresh() {
    if (paused) return;
    fetch('/logs/fragment')
      .then(function(r) { if (!r.ok) { throw new Error('HTTP ' + r.status); } return r.text(); })
      .then(function(html) { container.innerHTML = html; })
      .catch(function(e) {
        var existing = container.querySelector('.error-msg');
        if (existing) existing.remove();
        var msg = document.createElement('p');
        msg.className = 'error-msg';
        msg.textContent = 'Failed to refresh logs: ' + (e.message || 'network error');
        container.prepend(msg);
      });
  }

  timer = setInterval(refresh, 2000);
})();
</script>
"""

    body = (
        f"{halt_html}<h2>Logs</h2>"
        f'<div class="log-controls">'
        f'<button id="pause-btn" aria-label="Pause auto-refresh">Pause</button>'
        f'<span class="log-meta">All services: {html.escape(svc_list)} &mdash; '
        f"last {_LOG_LINES} lines, auto-refresh 2s</span>"
        f"</div>"
        f"{log_content}{script}"
    )
    return HTMLResponse(_page("Logs", body, path="/logs"))
