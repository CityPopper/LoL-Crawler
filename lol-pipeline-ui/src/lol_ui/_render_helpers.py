"""Extracted rendering helpers — JS snippets and auto-refresh."""

from __future__ import annotations

from lol_ui.strings import t


def _riot_id_form_script(form_id: str, input_id: str, region_id: str) -> str:
    """Return a <script> that intercepts a Riot ID form submission.

    Encodes the ``#`` in ``GameName#TagLine`` for safe URL transport.
    """
    return f"""<script>
(function() {{
  var form = document.getElementById('{form_id}');
  if (!form) return;
  form.addEventListener('submit', function(e) {{
    var input = document.getElementById('{input_id}');
    if (input && input.value.indexOf('#') !== -1) {{
      e.preventDefault();
      var region = document.getElementById('{region_id}');
      var url = '/stats?riot_id=' + encodeURIComponent(input.value)
        + '&region=' + (region ? region.value : 'na1');
      window.location.href = url;
    }}
  }});
}})();
</script>"""


def _auto_refresh_script(  # noqa: PLR0913
    container_id: str,
    pause_btn_id: str,
    fragment_url: str,
    interval_ms: int,
    *,
    spinner_id: str = "",
    clear_btn_id: str = "",
    clear_url: str = "",
    svc_select_id: str = "",
    pause_label: str = "",
    resume_label: str = "",
) -> str:
    """Return a <script> for auto-refreshing an HTML fragment via fetch.

    Supports optional pause/resume button, spinner, clear button, and
    service-filter dropdown (for the logs page).
    """
    p_label = pause_label or t("streams_pause")
    r_label = resume_label or t("streams_resume")

    spinner_js = ""
    if spinner_id:
        spinner_js = f"  var spinner = document.getElementById('{spinner_id}');\n"

    clear_js = ""
    if clear_btn_id:
        fetch_line = ""
        if clear_url:
            fetch_line = (
                f"    fetch('{clear_url}', {{method: 'POST'}})"
                f".catch(function() {{}});\n"
            )
        clear_js = (
            f"  var clearBtn = document.getElementById('{clear_btn_id}');\n"
            f"  clearBtn.addEventListener('click', function() {{\n"
            f"{fetch_line}"
            f"    container.innerHTML = '';\n"
            f"  }});\n"
        )

    svc_js = ""
    url_expr = f"'{fragment_url}'"
    if svc_select_id:
        svc_js = (
            f"  var svcSelect = document.getElementById('{svc_select_id}');\n"
            f"  svcSelect.addEventListener('change', function() {{\n"
            f"    refresh();\n"
            f"  }});\n"
        )
        url_expr = (
            f"'{fragment_url}'"
            " + (svcSelect.value"
            " ? '?service=' + encodeURIComponent(svcSelect.value) : '')"
        )

    spinner_show = "    spinner.style.display = 'inline-block';\n" if spinner_id else ""
    spinner_hide = "spinner.style.display = 'none'; " if spinner_id else ""

    return f"""
<script>
(function() {{
  var paused = false;
  var btn = document.getElementById('{pause_btn_id}');
  var container = document.getElementById('{container_id}');
{spinner_js}{clear_js}{svc_js}\
  var pauseLabel = '{p_label}';
  var resumeLabel = '{r_label}';

  btn.addEventListener('click', function() {{
    paused = !paused;
    btn.textContent = paused ? resumeLabel : pauseLabel;
    btn.classList.toggle('paused', paused);
  }});

  function refresh() {{
    if (paused) return;
{spinner_show}\
    var url = {url_expr};
    fetch(url)
      .then(function(r) {{ if(!r.ok) throw new Error('HTTP '+r.status); return r.text(); }})
      .then(function(html) {{ container.innerHTML = html; {spinner_hide}}})
      .catch(function(e) {{
        {spinner_hide}var existing = container.querySelector('.error-msg');
        if (existing) existing.remove();
        var msg = document.createElement('p');
        msg.className = 'error-msg';
        msg.textContent = e.message || 'error';
        container.prepend(msg);
      }});
  }}

  setInterval(refresh, {interval_ms});
}})();
</script>
"""
