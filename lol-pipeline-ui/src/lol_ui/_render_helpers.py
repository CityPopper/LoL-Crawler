"""Extracted rendering helpers — JS snippets, sort links, region selects, pagination."""

from __future__ import annotations

import html as _html

from lol_ui.constants import _REGIONS
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


def _player_filter_script() -> str:
    """Return a <script> that filters player table rows by name input."""
    return """
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


def _auto_refresh_script(  # noqa: PLR0913
    container_id: str,
    pause_btn_id: str,
    fragment_url: str,
    interval_ms: int,
    *,
    spinner_id: str = "",
    clear_btn_id: str = "",
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
        clear_js = (
            f"  var clearBtn = document.getElementById('{clear_btn_id}');\n"
            f"  clearBtn.addEventListener('click', function() {{\n"
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


def _sort_link(
    key: str,
    label_key: str,
    current_sort: str,
    page: int,
    region_filter: str,
) -> str:
    """Render a single sort link for the players page."""
    cls = ' class="active"' if current_sort == key else ""
    label = t(label_key)
    return (
        f'<a href="/players?sort={key}&amp;page={page}'
        f'&amp;region={region_filter}"{cls}'
        f' aria-label="Sort by {label}">{label}</a>'
    )


def _region_select(
    current_sort: str,
    region_filter: str,
    regions: list[str] | None = None,
) -> str:
    """Render the region filter dropdown for the players page."""
    regs = regions if regions is not None else _REGIONS
    region_options = f'<option value="">{t("players_all_regions")}</option>\n'
    region_options += "\n".join(
        f'<option value="{reg}"{" selected" if reg == region_filter else ""}>{reg}</option>'
        for reg in regs
    )
    return (
        '<div class="filter-controls mt-sm">'
        f'<label for="region-filter">{t("players_col_region")}:</label>'
        f'<select id="region-filter"'
        f" onchange=\"window.location.href='/players?sort={current_sort}"
        f"&region='+this.value\">"
        f"{region_options}</select></div>"
    )


def _pagination_html(
    prev_url: str | None,
    next_url: str | None,
    page_indicator: str,
) -> str:
    """Render pagination controls with prev/next links."""
    prev_link = (
        f'<a class="page-link" href="{_html.escape(prev_url)}">&larr; {t("players_prev")}</a>'
        if prev_url
        else ""
    )
    next_link = (
        f'<a class="page-link" href="{_html.escape(next_url)}">{t("players_next")} &rarr;</a>'
        if next_url
        else ""
    )
    return f'<p class="pagination">{prev_link}{page_indicator}{next_link}</p>'
