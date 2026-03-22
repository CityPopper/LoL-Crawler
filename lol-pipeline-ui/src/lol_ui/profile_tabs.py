"""Profile-level tab navigation for the player stats page.

Provides:
- ``_profile_tabs_html(active_tab, content_html)`` — wrap stats content with
  Summary / Mastery / ARAM tab navigation.
- ``_profile_tab_js()`` — vanilla JS for profile-level tab switching.

Reuses the ``_tab_strip_html`` / ``_tab_panel_html`` primitives from ``tabs.py``.
"""

from __future__ import annotations

from lol_ui.tabs import _tab_panel_html, _tab_strip_html

_PROFILE_TABS: list[tuple[str, str]] = [
    ("summary", "Summary"),
    ("mastery", "Mastery"),
    ("aram", "ARAM"),
]

_PLACEHOLDER_HTML = '<p class="text-muted">Coming soon.</p>'


def _profile_tabs_html(content_html: str) -> str:
    """Wrap *content_html* in profile-level tab navigation.

    The Summary tab shows existing stats content.
    Mastery and ARAM tabs show placeholder text.
    """
    strip = _tab_strip_html(_PROFILE_TABS, active="summary")

    panels = (
        _tab_panel_html("summary", content_html, active=True)
        + _tab_panel_html("mastery", _PLACEHOLDER_HTML, active=False)
        + _tab_panel_html("aram", _PLACEHOLDER_HTML, active=False)
    )

    return f'<div class="profile-tabs">{strip}{panels}</div>'


def _profile_tab_js() -> str:
    """Return a ``<script>`` block implementing profile-level tab switching.

    Scoped to the ``.profile-tabs`` container so it does not interfere
    with match-detail tabs.
    """
    return """<script>
(function() {
  var container = document.querySelector('.profile-tabs');
  if (!container) return;
  var strip = container.querySelector('.tab-strip');
  if (!strip) return;
  strip.addEventListener('click', function(e) {
    var btn = e.target.closest('.tab-btn');
    if (!btn) return;
    var tabId = btn.getAttribute('data-tab');
    if (!tabId) return;
    var buttons = strip.querySelectorAll('.tab-btn');
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].classList.remove('tab-btn--active');
      buttons[i].setAttribute('aria-selected', 'false');
      buttons[i].setAttribute('tabindex', '-1');
    }
    btn.classList.add('tab-btn--active');
    btn.setAttribute('aria-selected', 'true');
    btn.setAttribute('tabindex', '0');
    var panels = container.querySelectorAll('.tab-panel');
    for (var j = 0; j < panels.length; j++) {
      if (panels[j].getAttribute('data-tab-panel') === tabId) {
        panels[j].style.display = '';
      } else {
        panels[j].style.display = 'none';
      }
    }
  });
})();
</script>"""
