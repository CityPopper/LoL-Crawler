"""Tab strip and panel HTML generation for match detail view.

Provides:
- ``_tab_strip_html(tabs, active)`` — scrollable tab button strip
- ``_tab_panel_html(id, content, active)`` — individual tab panel wrapper
- ``_tabbed_match_detail(...)`` — assembles the full tabbed match detail
- ``_tab_js()`` — vanilla JS for tab switching via event delegation
"""

from __future__ import annotations

from lol_ui.strings import t


def _tab_strip_html(tabs: list[tuple[str, str]], active: str) -> str:
    """Render a horizontal scrollable tab strip.

    *tabs* is a list of ``(id, label)`` pairs.
    *active* is the ``id`` of the currently selected tab.

    Returns empty string when *tabs* is empty.
    """
    if not tabs:
        return ""
    buttons: list[str] = []
    for tab_id, label in tabs:
        is_active = tab_id == active
        cls = "tab-btn tab-btn--active" if is_active else "tab-btn"
        selected = "true" if is_active else "false"
        tabindex = "0" if is_active else "-1"
        buttons.append(
            f'<button class="{cls}" data-tab="{tab_id}"'
            f' role="tab" aria-selected="{selected}"'
            f' tabindex="{tabindex}">{label}</button>'
        )
    inner = "".join(buttons)
    return f'<div class="tab-strip" role="tablist">{inner}</div>'


def _tab_panel_html(panel_id: str, content: str, *, active: bool) -> str:
    """Wrap *content* in a tab panel div.

    Hidden panels get ``display:none`` so only the active panel is visible
    on initial render (before JS kicks in).
    """
    hidden = "" if active else ' style="display:none"'
    return (
        f'<div class="tab-panel" data-tab-panel="{panel_id}"'
        f' role="tabpanel"{hidden}>{content}</div>'
    )


def _tabbed_match_detail(
    overview_html: str,
    build_html: str,
    team_html: str,
    ai_html: str,
    timeline_html: str,
    has_timeline: bool,
) -> str:
    """Assemble a tabbed match detail view.

    Tab order: Overview | Build | Team Analysis | AI Score | Timeline.
    The Timeline tab is suppressed server-side when *has_timeline* is False.
    """
    tabs: list[tuple[str, str]] = [
        ("overview", t("overview")),
        ("build", t("build")),
        ("team_analysis", t("team_analysis")),
        ("ai_score", t("ai_score")),
    ]
    if has_timeline:
        tabs.append(("timeline", t("timeline")))

    strip = _tab_strip_html(tabs, active="overview")

    panels = [
        _tab_panel_html("overview", overview_html, active=True),
        _tab_panel_html("build", build_html, active=False),
        _tab_panel_html("team_analysis", team_html, active=False),
        _tab_panel_html("ai_score", ai_html, active=False),
    ]
    if has_timeline:
        panels.append(_tab_panel_html("timeline", timeline_html, active=False))

    panels_html = "".join(panels)
    return f'<div class="match-tabs">{strip}{panels_html}</div>'


def _tab_js() -> str:
    """Return a ``<script>`` block implementing tab switching.

    Uses ``classList`` toggling with event delegation, matching the
    ``toggleMatchDetail`` pattern in match_history.py.

    Call ``initMatchTabs(container)`` after inserting detail HTML via
    ``detail.innerHTML = h`` to wire up tab buttons within that container.
    """
    return """<script>
function initMatchTabs(container) {
  if (!container) return;
  var strip = container.querySelector('.tab-strip');
  if (!strip) return;
  strip.addEventListener('click', function(e) {
    var btn = e.target.closest('.tab-btn');
    if (!btn) return;
    var tabId = btn.getAttribute('data-tab');
    if (!tabId) return;
    // Deactivate all tabs
    var buttons = strip.querySelectorAll('.tab-btn');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.remove('tab-btn--active');
      buttons[i].setAttribute('aria-selected', 'false');
      buttons[i].setAttribute('tabindex', '-1');
    }
    // Activate clicked tab
    btn.classList.add('tab-btn--active');
    btn.setAttribute('aria-selected', 'true');
    btn.setAttribute('tabindex', '0');
    // Hide all panels, show selected
    var panels = container.querySelectorAll('.tab-panel');
    for (var j = 0; j < panels.length; j++) {
      if (panels[j].getAttribute('data-tab-panel') === tabId) {
        panels[j].style.display = '';
      } else {
        panels[j].style.display = 'none';
      }
    }
  });
}
</script>"""
