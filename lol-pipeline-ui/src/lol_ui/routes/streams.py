"""Streams routes — GET /streams, GET /streams/fragment."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from lol_ui._render_helpers import _auto_refresh_script
from lol_ui.rendering import _page
from lol_ui.streams_helpers import _streams_fragment_html
from lol_ui.strings import t

router = APIRouter()


@router.get("/streams/fragment", response_class=HTMLResponse)
async def streams_fragment(request: Request) -> HTMLResponse:
    """Return just the streams table + status HTML for AJAX polling."""
    r = request.app.state.r
    return HTMLResponse(await _streams_fragment_html(r))


@router.get("/streams", response_class=HTMLResponse)
async def show_streams(request: Request) -> HTMLResponse:
    r = request.app.state.r
    fragment = await _streams_fragment_html(r)

    script = _auto_refresh_script(
        container_id="streams-container",
        pause_btn_id="streams-pause-btn",
        fragment_url="/streams/fragment",
        interval_ms=5000,
        spinner_id="streams-spinner",
        pause_label=t("streams_pause"),
        resume_label=t("streams_resume"),
    )

    body = f"""
<h2>{t("page_streams")}</h2>
<div id="streams-container">
{fragment}
</div>
<div class="log-controls">
  <button id="streams-pause-btn" aria-label="Pause auto-refresh">{t("streams_pause")}</button>
  <div class="spinner hidden" id="streams-spinner"></div>
  <span class="log-meta">{t("streams_auto_refresh")}</span>
</div>
{script}
"""
    return HTMLResponse(_page(t("page_streams"), body, path="/streams"))
