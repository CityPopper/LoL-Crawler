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
from lol_pipeline.models import MessageEnvelope
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
    cfg = Config()
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


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — LoL Pipeline</title>
  <style>
    body {{ font-family: monospace; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
    h1 {{ border-bottom: 2px solid #ccc; padding-bottom: 0.5rem; }}
    nav a {{ margin-right: 1rem; }}
    form {{ margin: 1rem 0; }}
    input, select {{ padding: 0.4rem; margin: 0.2rem; }}
    button {{ padding: 0.4rem 1rem; cursor: pointer; }}
    .success {{ color: green; }}
    .error {{ color: red; }}
    .warning {{ color: orange; }}
    .unverified {{ color: #b8860b; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
    td, th {{ border: 1px solid #ccc; padding: 0.4rem 0.8rem; text-align: left; }}
    th {{ background: #f0f0f0; }}
    .streams td:last-child {{ text-align: right; }}
  </style>
</head>
<body>
<h1>LoL Pipeline</h1>
<nav>
  <a href="/stats">Stats</a>
  <a href="/players">Players</a>
  <a href="/streams">Streams</a>
  <a href="/lcu">LCU</a>
  <a href="/logs">Logs</a>
</nav>
<hr>
{body}
</body>
</html>"""


def _stats_form(msg: str = "", css_class: str = "", stats_html: str = "") -> str:
    msg_html = f'<p class="{css_class}">{msg}</p>' if msg else ""
    return _page(
        "Player Stats",
        f"""
<h2>Player Stats</h2>
{msg_html}
<form method="get" action="/stats">
  <label>Riot ID: <input name="riot_id" placeholder="GameName#TagLine" required size="30"></label>
  <label>Region:
    <select name="region">
      <option value="na1">na1</option>
      <option value="euw1">euw1</option>
      <option value="eun1">eun1</option>
      <option value="kr">kr</option>
      <option value="br1">br1</option>
      <option value="jp1">jp1</option>
      <option value="oc1">oc1</option>
    </select>
  </label>
  <button type="submit">Look Up</button>
</form>
{stats_html}
""",
    )


def _stats_table(
    stats: dict[str, str],
    champs: list[tuple[str, float]],
    roles: list[tuple[str, float]],
) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>"
        for k, v in sorted(stats.items())
    )
    champ_rows = "".join(f"<tr><td>{html.escape(c)}</td><td>{int(n)}</td></tr>" for c, n in champs)
    role_rows = "".join(f"<tr><td>{html.escape(r)}</td><td>{int(n)}</td></tr>" for r, n in roles)
    return f"""
<h3>Verified (Riot API) <span class="success">&#10003;</span></h3>
<table><tr><th>Stat</th><th>Value</th></tr>{rows}</table>
<h3>Top Champions</h3>
<table><tr><th>Champion</th><th>Games</th></tr>
{champ_rows or "<tr><td colspan='2'>No data</td></tr>"}</table>
<h3>Roles</h3>
<table><tr><th>Role</th><th>Games</th></tr>
{role_rows or "<tr><td colspan='2'>No data</td></tr>"}</table>
"""


def _match_history_section(puuid: str, region: str, riot_id: str) -> str:
    """Render a lazy-loading match history placeholder section."""
    safe_puuid = html.escape(puuid)
    safe_region = html.escape(region)
    safe_id = html.escape(riot_id)
    href = (
        f"/stats/matches?puuid={safe_puuid}"
        f"&amp;region={safe_region}&amp;riot_id={safe_id}&amp;page=0"
    )
    onclick = f"loadMatches(this, '{safe_puuid}', '{safe_region}', '{safe_id}', 0); return false;"
    err_msg = "container.innerHTML = '<p class=\"error\">Failed to load: ' + (e.message || e) + '</p>';"
    return f"""
<h3>Match History</h3>
<div id="match-history-container">
  <p><a href="{href}" onclick="{onclick}">Load match history</a></p>
</div>
<script>
function loadMatches(el, puuid, region, riotId, page) {{
  var container = document.getElementById('match-history-container');
  container.innerHTML = '<p>Loading...</p>';
  var url = '/stats/matches?puuid=' + encodeURIComponent(puuid)
    + '&region=' + encodeURIComponent(region)
    + '&riot_id=' + encodeURIComponent(riotId)
    + '&page=' + page;
  fetch(url, {{headers: {{'Accept': 'text/html'}}}})
    .then(function(r) {{ return r.text(); }})
    .then(function(html) {{ container.innerHTML = html; }})
    .catch(function(e) {{ {err_msg} }});
}}
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
<h3>Unverified (LCU) <span class="unverified">&#9888;</span></h3>
<p class="unverified">Collected from the local League client. May include game modes not
tracked by the Riot API (ARAM Mayhem, URF, etc.). May overlap with verified data above.</p>
<table>
  <tr><th>Total Games</th><th>Wins</th><th>Losses</th></tr>
  <tr><td>{total}</td><td>{wins}</td><td>{total - wins}</td></tr>
</table>
<h4>By Game Mode</h4>
<table>
  <tr><th>Mode</th><th>Games</th><th>Wins</th><th>Losses</th></tr>
  {mode_rows or "<tr><td colspan='4'>No data</td></tr>"}
</table>
"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

app = FastAPI(title="LoL Pipeline UI", lifespan=_lifespan)


@app.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse("/stats")


@app.get("/stats", response_class=HTMLResponse)
async def show_stats(request: Request) -> HTMLResponse:
    riot_id = request.query_params.get("riot_id", "")
    region = request.query_params.get("region", "na1")

    if not riot_id:
        return HTMLResponse(_stats_form())

    if "#" not in riot_id:
        return HTMLResponse(_stats_form("Invalid Riot ID — expected GameName#TagLine", "error"))

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
            return HTMLResponse(_stats_form(f"Player not found: {html.escape(riot_id)}", "error"))
        except (AuthError, RateLimitError, ServerError) as exc:
            return HTMLResponse(_stats_form(f"Riot API error: {html.escape(str(exc))}", "error"))
        puuid = account["puuid"]
        await r.set(cache_key, puuid)
    stats: dict[str, str] = await r.hgetall(f"player:stats:{puuid}")

    lcu_matches: list[dict[str, Any]] = request.app.state.lcu.get(puuid, [])
    lcu_html = _lcu_stats_section(lcu_matches) if lcu_matches else ""

    safe_id = html.escape(riot_id)

    if not stats and not lcu_matches:
        # Auto-seed: publish to stream:puuid if not halted and not already seeded
        if await r.get("system:halted"):
            return HTMLResponse(_stats_form(f"System halted. No stats yet for {safe_id}.", "error"))
        existing_seeded: str | None = await r.hget(f"player:{puuid}", "seeded_at")
        if existing_seeded:
            return HTMLResponse(
                _stats_form(
                    f"{safe_id} was seeded recently — pipeline processing. Check back soon.",
                    "warning",
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
        )
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
        _log.info("auto-seeded via stats lookup", extra={"puuid": puuid})
        return HTMLResponse(
            _stats_form(
                f"&#10003; Auto-seeded {safe_id} — pipeline processing. Refresh in a minute.",
                "warning",
            )
        )

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
    heading = f"Stats for {safe_name} (PUUID: {puuid[:12]}&#8230;)"
    return HTMLResponse(_stats_form(heading, "success", api_html + lcu_html + history_html))


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
        return HTMLResponse(_page("Players", "<h2>Players</h2><p>No players seeded yet.</p>"))

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
        seeded = p["seeded_at"][:10] if p["seeded_at"] else "?"
        rows += (
            f'<tr><td><a href="{href}">{safe_name}</a></td>'
            f"<td>{html.escape(p['region'])}</td><td>{seeded}</td></tr>"
        )

    has_prev = page > 0
    has_next = start + _PLAYERS_PAGE_SIZE < total
    prev_link = f'<a href="/players?page={page - 1}">&larr; Prev</a>' if has_prev else ""
    sep = "&nbsp;&nbsp;" if has_prev and has_next else ""
    next_link = f'<a href="/players?page={page + 1}">Next &rarr;</a>' if has_next else ""
    pagination = f"<p>{prev_link}{sep}{next_link}</p>" if (has_prev or has_next) else ""

    body = f"""<h2>Players ({total} total, page {page + 1})</h2>
<table>
  <tr><th>Riot ID</th><th>Region</th><th>Seeded</th></tr>
  {rows}
</table>
{pagination}
"""
    return HTMLResponse(_page("Players", body))


@app.get("/streams", response_class=HTMLResponse)
async def show_streams(request: Request) -> HTMLResponse:
    r = request.app.state.r
    streams = [
        "stream:puuid",
        "stream:match_id",
        "stream:parse",
        "stream:analyze",
        "stream:dlq",
        "stream:dlq:archive",
    ]
    rows = ""
    for s in streams:
        length = await r.xlen(s)
        rows += f"<tr><td>{s}</td><td>{length}</td></tr>"
    delayed = await r.zcard("delayed:messages")
    rows += f"<tr><td>delayed:messages</td><td>{delayed}</td></tr>"

    halted = await r.get("system:halted")
    status = (
        '<p class="error">&#9888; System is HALTED (system:halted is set)</p>'
        if halted
        else '<p class="success">&#10003; System running</p>'
    )

    body = f"""
<h2>Stream Depths</h2>
{status}
<table class="streams">
  <tr><th>Key</th><th>Length</th></tr>
  {rows}
</table>
"""
    return HTMLResponse(_page("Streams", body))


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
        result = '<span class="success">Win</span>' if win else '<span class="error">Loss</span>'
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
    safe_puuid = html.escape(puuid)
    safe_region = html.escape(region)
    safe_id = html.escape(riot_id)
    next_page = ""
    if has_more:
        next_p = page + 1
        next_page = (
            f"<p><a href=\"#\" onclick=\"loadMatches(null,'{safe_puuid}','{safe_region}',"
            f"'{safe_id}',{next_p}); return false;\">Load more (page {next_p + 1})</a></p>"
        )
    return (
        f"<table><tr><th>Date</th><th>Result</th><th>Champion</th><th>Role</th>"
        f"<th>K/D/A</th><th>Mode</th></tr>{rows}</table>{next_page}"
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

    results: list[tuple[str, dict[str, str], dict[str, str]]] = []
    for match_id, _ in raw_pairs:
        match: dict[str, str] = await r.hgetall(f"match:{match_id}")
        participant: dict[str, str] = await r.hgetall(f"participant:{match_id}:{puuid}")
        results.append((match_id, match, participant))

    return HTMLResponse(_match_history_html(results, puuid, region, riot_id, page, has_more))


@app.get("/lcu", response_class=HTMLResponse)
async def show_lcu(request: Request) -> HTMLResponse:
    lcu: dict[str, list[dict[str, Any]]] = request.app.state.lcu

    if not lcu:
        body = """
<h2>LCU Match History <span class="unverified">&#9888; Unverified</span></h2>
<p>No LCU data collected yet.</p>
<p>Run <code>just lcu</code> with the League client open, then restart the UI to reload.</p>
"""
        return HTMLResponse(_page("LCU", body))

    rows = ""
    for puuid, matches in sorted(lcu.items()):
        if not matches:
            continue
        riot_id = html.escape(str(matches[0].get("riot_id", puuid[:12])))
        total = len(matches)
        wins = sum(1 for m in matches if m.get("win"))
        by_mode = _aggregate_by_mode(matches)
        mode_str = html.escape(
            ", ".join(f"{mode} ({d['w']}/{d['t']})" for mode, d in sorted(by_mode.items()))
        )
        rows += (
            f"<tr>"
            f"<td>{riot_id}</td>"
            f"<td>{total}</td>"
            f"<td>{wins}</td>"
            f"<td>{total - wins}</td>"
            f"<td>{mode_str}</td>"
            f"</tr>"
        )

    body = f"""
<h2>LCU Match History <span class="unverified">&#9888; Unverified</span></h2>
<p class="unverified">Data collected from the local League client. Not verified against the Riot
API. Includes game modes unavailable in Match-v5 (ARAM Mayhem, URF, One for All, etc.).</p>
<p>To collect more data: run <code>just lcu</code> with the League client open,
then restart the UI (<code>just restart ui</code>) to reload.</p>
<table>
  <tr><th>Player</th><th>Games</th><th>Wins</th><th>Losses</th><th>Modes (W/T)</th></tr>
  {rows}
</table>
"""
    return HTMLResponse(_page("LCU", body))


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
_LOG_CSS = """
<style>
  .log-wrap { font-family: monospace; font-size: 0.82em; }
  .log-line { display: flex; gap: 0.5rem; padding: 2px 4px;
    border-bottom: 1px solid #f0f0f0; align-items: baseline; flex-wrap: wrap; }
  .log-critical { background: #ffe0e0; font-weight: bold; }
  .log-error { background: #fff0f0; }
  .log-warning { background: #fffbe6; }
  .log-debug { color: #999; }
  .log-ts { color: #aaa; white-space: nowrap; flex-shrink: 0; }
  .log-badge { padding: 0 4px; border-radius: 2px;
    font-size: 0.75em; white-space: nowrap; flex-shrink: 0; }
  .log-badge.log-critical { background: #c00; color: #fff; }
  .log-badge.log-error { background: #e33; color: #fff; }
  .log-badge.log-warning { background: #e80; color: #fff; }
  .log-badge.log-debug { background: #bbb; color: #fff; }
  .log-badge.log-info { background: #888; color: #fff; }
  .log-svc { color: #669; flex-shrink: 0; }
  .log-msg { flex: 1; }
  .log-extra { color: #666; font-size: 0.9em; }
  .log-controls { margin: 0.5rem 0; display: flex;
    gap: 0.5rem; align-items: center; flex-wrap: wrap; }
  .log-meta { color: #888; font-size: 0.85em; margin-bottom: 0.3rem; }
  #pause-btn { padding: 0.4rem 1rem; cursor: pointer; }
  #pause-btn.paused { background: #e33; color: #fff; }
</style>
"""


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
                "Logs", "<h2>Logs</h2><p>LOG_DIR not configured. Add it to docker-compose.yml.</p>"
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
        f"{_LOG_CSS}<h2>Logs</h2>"
        f'<div class="log-controls">'
        f'<button id="pause-btn">Pause</button>'
        f'<span class="log-meta">All services: {html.escape(svc_list)} &mdash; '
        f"last {_LOG_LINES} lines, auto-refresh 2s</span>"
        f"</div>"
        f"{log_content}{script}"
    )
    return HTMLResponse(_page("Logs", body))
