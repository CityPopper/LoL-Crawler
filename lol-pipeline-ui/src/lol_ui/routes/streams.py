"""Streams routes — GET /streams, GET /streams/fragment."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from lol_ui.rendering import _page
from lol_ui.streams_helpers import _streams_fragment_html

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
