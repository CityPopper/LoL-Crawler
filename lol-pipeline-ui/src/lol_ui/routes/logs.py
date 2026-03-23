"""Logs routes — GET /logs, GET /logs/fragment."""

from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from lol_pipeline.config import Config

from lol_ui.constants import _HALT_BANNER, _LOG_LINES
from lol_ui.log_helpers import _merged_log_lines, _render_log_lines
from lol_ui.rendering import _empty_state, _page

router = APIRouter()

_SERVICE_NAMES = [
    "crawler",
    "fetcher",
    "parser",
    "analyzer",
    "recovery",
    "delay-scheduler",
    "discovery",
    "ui",
]

# Only allow safe service name chars to prevent path traversal
_SAFE_SERVICE_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


def _service_filter_html(selected: str) -> str:
    """Render a <select> dropdown for service filtering."""
    options = '<option value="">All services</option>'
    for svc in _SERVICE_NAMES:
        sel = " selected" if svc == selected else ""
        options += f'<option value="{html.escape(svc)}"{sel}>{html.escape(svc)}</option>'
    return f'<label for="svc-filter">Service:</label><select id="svc-filter">{options}</select>'


@router.get("/logs/fragment", response_class=HTMLResponse)
async def logs_fragment(request: Request) -> HTMLResponse:
    """Return just the log lines HTML for AJAX polling."""
    cfg: Config = request.app.state.cfg
    if not cfg.log_dir:
        return HTMLResponse(_empty_state("LOG_DIR not configured", "Add it to docker-compose.yml."))
    service = request.query_params.get("service", "")
    if service and not _SAFE_SERVICE_RE.match(service):
        service = ""
    log_dir = Path(cfg.log_dir)
    lines = await asyncio.to_thread(_merged_log_lines, log_dir, _LOG_LINES, service)
    return HTMLResponse(_render_log_lines(lines))


@router.get("/logs", response_class=HTMLResponse)
async def show_logs(request: Request) -> HTMLResponse:
    r = request.app.state.r
    halted = await r.get("system:halted")
    halt_html = _HALT_BANNER if halted else ""

    cfg: Config = request.app.state.cfg
    if not cfg.log_dir:
        return HTMLResponse(
            _page(
                "Logs",
                halt_html
                + "<h2>Logs</h2>"
                + _empty_state("LOG_DIR not configured", "Add it to docker-compose.yml."),
                path="/logs",
            )
        )

    log_dir = Path(cfg.log_dir)
    log_files = sorted(log_dir.glob("*.log"))
    if not log_files:
        return HTMLResponse(
            _page(
                "Logs",
                halt_html
                + "<h2>Logs</h2>"
                + _empty_state(
                    "No log files found",
                    f"No <code>.log</code> files in <code>{html.escape(cfg.log_dir)}</code>."
                    " Services may not have started yet.",
                ),
                path="/logs",
            )
        )

    service = request.query_params.get("service", "")
    if service and not _SAFE_SERVICE_RE.match(service):
        service = ""
    lines = await asyncio.to_thread(_merged_log_lines, log_dir, _LOG_LINES, service)
    svc_list = ", ".join(f.stem for f in log_files)
    log_content = f'<div class="log-wrap" id="log-container">{_render_log_lines(lines)}</div>'

    svc_filter = _service_filter_html(service)

    script = """
<script>
(function() {
  var paused = false;
  var btn = document.getElementById('pause-btn');
  var clearBtn = document.getElementById('clear-btn');
  var container = document.getElementById('log-container');
  var svcSelect = document.getElementById('svc-filter');
  var timer;

  btn.addEventListener('click', function() {
    paused = !paused;
    btn.textContent = paused ? 'Resume' : 'Pause';
    btn.classList.toggle('paused', paused);
    btn.setAttribute('aria-label', paused ? 'Resume auto-refresh' : 'Pause auto-refresh');
  });

  clearBtn.addEventListener('click', function() {
    container.innerHTML = '';
  });

  svcSelect.addEventListener('change', function() {
    refresh();
  });

  function refresh() {
    if (paused) return;
    var svc = svcSelect.value;
    var url = '/logs/fragment' + (svc ? '?service=' + encodeURIComponent(svc) : '');
    fetch(url)
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
        f'<button id="clear-btn" aria-label="Clear displayed logs">Clear</button>'
        f"{svc_filter}"
        f'<span class="log-meta">Services: {html.escape(svc_list)} &mdash; '
        f"last {_LOG_LINES} lines, auto-refresh 2s</span>"
        f"</div>"
        f"{log_content}{script}"
    )
    return HTMLResponse(_page("Logs", body, path="/logs"))
