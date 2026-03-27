"""System routes -- combined streams, rate-limiter status, and request metrics."""

from __future__ import annotations

import html
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from lol_ui._render_helpers import _auto_refresh_script
from lol_ui.rendering import _page
from lol_ui.streams_helpers import _streams_fragment_html
from lol_ui.strings import t

router = APIRouter()

_RPM_WINDOW = 60  # look-back minutes for request metrics
_RPM_COUNTER_TTL = 7200  # 2 hours in seconds

_DEFAULT_KNOWN_SOURCES = (
    "riot,riot:americas,riot:europe,riot:asia,riot:sea,"
    "fetcher,crawler,discovery,opgg,opgg:ui"
)


def _known_sources() -> list[str]:
    """Return the list of known rate-limiter sources from env or default."""
    raw = os.environ.get("RATELIMIT_KNOWN_SOURCES", _DEFAULT_KNOWN_SOURCES)
    return [s.strip() for s in raw.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Fragment: streams (reuses existing helper)
# ---------------------------------------------------------------------------


@router.get("/system/fragment/streams", response_class=HTMLResponse)
async def system_fragment_streams(request: Request) -> HTMLResponse:
    """Return streams table HTML fragment for auto-refresh."""
    r = request.app.state.r
    return HTMLResponse(await _streams_fragment_html(r))


# ---------------------------------------------------------------------------
# Fragment: rate-limiter status
# ---------------------------------------------------------------------------


async def _ratelimiter_fragment_html(r: object) -> str:
    """Build rate-limiter status table HTML."""
    sources = _known_sources()
    # Pipeline: for each source query ZCARD short, ZCARD long, EXISTS cooling_off
    async with r.pipeline(transaction=False) as pipe:  # type: ignore[union-attr]
        for src in sources:
            prefix = f"ratelimit:{src}"
            pipe.zcard(f"{prefix}:short")
            pipe.zcard(f"{prefix}:long")
            pipe.exists(f"{prefix}:cooling_off")
        results = await pipe.execute()

    rows = ""
    for i, src in enumerate(sources):
        base = i * 3
        short_count = results[base]
        long_count = results[base + 1]
        cooling = bool(results[base + 2])
        cooling_label = t("system_cooling_yes") if cooling else t("system_cooling_no")
        cooling_cls = ' class="text-warning"' if cooling else ""
        rows += (
            f"<tr><td>{html.escape(src)}</td>"
            f'<td class="text-right">{short_count}</td>'
            f'<td class="text-right">{long_count}</td>'
            f"<td{cooling_cls}>{cooling_label}</td></tr>"
        )

    return f"""<div class="table-scroll">
<table class="streams streams--full">
  <thead><tr>
    <th scope="col">{t("system_col_source")}</th>
    <th scope="col" class="text-right">{t("system_col_short")}</th>
    <th scope="col" class="text-right">{t("system_col_long")}</th>
    <th scope="col">{t("system_col_cooling")}</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
</div>"""


@router.get("/system/fragment/ratelimiter", response_class=HTMLResponse)
async def system_fragment_ratelimiter(request: Request) -> HTMLResponse:
    """Return rate-limiter status table HTML fragment."""
    r = request.app.state.r
    return HTMLResponse(await _ratelimiter_fragment_html(r))


# ---------------------------------------------------------------------------
# Fragment: request metrics
# ---------------------------------------------------------------------------


async def _metrics_fragment_html(r: object) -> str:
    """Build per-source request metrics table HTML."""
    sources = _known_sources()
    now_ms = int(time.time() * 1000)
    current_minute = now_ms // 60_000

    # Build all keys for all sources in one pipeline
    async with r.pipeline(transaction=False) as pipe:  # type: ignore[union-attr]
        for src in sources:
            keys = [f"ratelimit:{src}:rpm:{current_minute - i}" for i in range(_RPM_WINDOW)]
            pipe.mget(keys)
        results = await pipe.execute()

    rows = ""
    for idx, src in enumerate(sources):
        counts_raw = results[idx]
        counts = [int(c or 0) for c in counts_raw]
        last_1 = counts[0]
        last_10 = sum(counts[:10])
        last_30 = sum(counts[:30])
        last_60 = sum(counts[:_RPM_WINDOW])
        avg_60 = f"{last_60 / _RPM_WINDOW:.1f}"
        rows += (
            f"<tr><td>{html.escape(src)}</td>"
            f'<td class="text-right">{last_1}</td>'
            f'<td class="text-right">{last_10}</td>'
            f'<td class="text-right">{last_30}</td>'
            f'<td class="text-right">{avg_60}</td></tr>'
        )

    return f"""<div class="table-scroll">
<table class="streams streams--full">
  <thead><tr>
    <th scope="col">{t("system_col_source")}</th>
    <th scope="col" class="text-right">{t("system_col_last1m")}</th>
    <th scope="col" class="text-right">{t("system_col_last10m")}</th>
    <th scope="col" class="text-right">{t("system_col_last30m")}</th>
    <th scope="col" class="text-right">{t("system_col_avg_rpm")}</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
</div>"""


@router.get("/system/fragment/metrics", response_class=HTMLResponse)
async def system_fragment_metrics(request: Request) -> HTMLResponse:
    """Return request metrics table HTML fragment."""
    r = request.app.state.r
    return HTMLResponse(await _metrics_fragment_html(r))


# ---------------------------------------------------------------------------
# Full page
# ---------------------------------------------------------------------------


@router.get("/system", response_class=HTMLResponse)
async def show_system(request: Request) -> HTMLResponse:
    """System page -- streams, rate limiter, and request metrics."""
    r = request.app.state.r

    streams_html = await _streams_fragment_html(r)
    ratelimiter_html = await _ratelimiter_fragment_html(r)
    metrics_html = await _metrics_fragment_html(r)

    streams_script = _auto_refresh_script(
        container_id="sys-streams",
        pause_btn_id="sys-streams-pause",
        fragment_url="/system/fragment/streams",
        interval_ms=5000,
        spinner_id="sys-streams-spinner",
        pause_label=t("streams_pause"),
        resume_label=t("streams_resume"),
    )

    rl_script = _auto_refresh_script(
        container_id="sys-ratelimiter",
        pause_btn_id="sys-rl-pause",
        fragment_url="/system/fragment/ratelimiter",
        interval_ms=5000,
        spinner_id="sys-rl-spinner",
        pause_label=t("streams_pause"),
        resume_label=t("streams_resume"),
    )

    metrics_script = _auto_refresh_script(
        container_id="sys-metrics",
        pause_btn_id="sys-metrics-pause",
        fragment_url="/system/fragment/metrics",
        interval_ms=10000,
        spinner_id="sys-metrics-spinner",
        pause_label=t("streams_pause"),
        resume_label=t("streams_resume"),
    )

    body = f"""
<h2>{t("page_system")}</h2>

<h3>{t("system_section_streams")}</h3>
<div id="sys-streams">{streams_html}</div>
<div class="log-controls">
  <button id="sys-streams-pause" aria-label="Pause">{t("streams_pause")}</button>
  <div class="spinner hidden" id="sys-streams-spinner"></div>
  <span class="log-meta">{t("streams_auto_refresh")}</span>
</div>
{streams_script}

<h3>{t("system_section_ratelimiter")}</h3>
<div id="sys-ratelimiter">{ratelimiter_html}</div>
<div class="log-controls">
  <button id="sys-rl-pause" aria-label="Pause">{t("streams_pause")}</button>
  <div class="spinner hidden" id="sys-rl-spinner"></div>
  <span class="log-meta">{t("streams_auto_refresh")}</span>
</div>
{rl_script}

<h3>{t("system_section_metrics")}</h3>
<div id="sys-metrics">{metrics_html}</div>
<div class="log-controls">
  <button id="sys-metrics-pause" aria-label="Pause">{t("streams_pause")}</button>
  <div class="spinner hidden" id="sys-metrics-spinner"></div>
  <span class="log-meta">{t("streams_auto_refresh")}</span>
</div>
{metrics_script}
"""
    return HTMLResponse(_page(t("page_system"), body, path="/system"))


# ---------------------------------------------------------------------------
# Backward-compat redirect
# ---------------------------------------------------------------------------


@router.get("/streams", response_class=HTMLResponse)
async def streams_redirect() -> RedirectResponse:  # type: ignore[return]
    """Redirect old /streams URL to /system."""
    return RedirectResponse("/system", status_code=301)
