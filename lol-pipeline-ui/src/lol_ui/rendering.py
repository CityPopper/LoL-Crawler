"""Rendering helpers — HTML page wrapper, badges, icons, formatting utilities."""

from __future__ import annotations

import html
import time

from lol_ui.constants import (
    _BADGE_VARIANTS,
    _DEPTH_BADGE_BACKLOG_THRESHOLD,
    _DEPTH_BADGE_BUSY_THRESHOLD,
    _KDA_RATIO_GOOD_THRESHOLD,
    _REGIONS,
    _TIME_AGO_DAY_S,
    _TIME_AGO_HOUR_S,
    _VALID_MSG_CLASSES,
)
from lol_ui.css import _CSS, _FAVICON, _NAV_ITEMS
from lol_ui.language import language_switcher_html
from lol_ui.strings import t as _t
from lol_ui.strings import t_raw as _t_raw
from lol_ui.themes import get_theme_css, theme_switcher_html


def _depth_badge(stream_name: str, depth: int) -> str:
    """Return a status badge based on stream depth thresholds."""
    if stream_name == "stream:dlq":
        if depth > 0:
            return _badge("error", f"{depth} {_t_raw('errors')}")
        return _badge("success", _t_raw("badge_ok"))
    if depth < _DEPTH_BADGE_BUSY_THRESHOLD:
        return _badge("success", _t_raw("badge_ok"))
    if depth < _DEPTH_BADGE_BACKLOG_THRESHOLD:
        return _badge("warning", _t_raw("badge_busy"))
    return _badge("error", _t_raw("badge_backlog"))


def _badge(variant: str, text: str) -> str:
    """Render a status badge with auto-escaped text (safe for user-supplied input).

    variant: success|error|warning|info|muted.
    """
    if variant not in _BADGE_VARIANTS:
        msg = f"Invalid badge variant: {variant}"
        raise ValueError(msg)
    return f'<span class="badge badge--{variant}">{html.escape(text)}</span>'


def _badge_html(variant: str, text: str) -> str:
    """Render a status badge preserving raw HTML in *text* (no escaping).

    Use only when *text* is trusted/pre-escaped HTML.  For user-supplied
    strings use :func:`_badge` which auto-escapes via ``html.escape``.
    """
    if variant not in _BADGE_VARIANTS:
        msg = f"Invalid badge variant: {variant}"
        raise ValueError(msg)
    return f'<span class="badge badge--{variant}">{text}</span>'


def _empty_state(title: str, body_html: str) -> str:
    """Render an empty-state message. Both params are raw HTML -- callers MUST
    pre-escape any dynamic content with html.escape().
    """
    return f'<div class="empty-state"><p><strong>{title}</strong></p><p>{body_html}</p></div>'


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


def _item_icon_html(item_id: str, version: str | None) -> str:
    """Return an <img> for a DDragon item ID, or an empty-slot span."""
    if not item_id or item_id.strip() in ("", "0") or not version:
        return '<span class="match-item match-item--empty"></span>'
    safe_v = html.escape(version)
    safe_id = html.escape(item_id.strip())
    url = f"https://ddragon.leagueoflegends.com/cdn/{safe_v}/img/item/{safe_id}.png"
    return (
        f'<img src="{url}" alt="item {safe_id}" class="match-item"'
        f' loading="lazy" onerror="this.style.display=\'none\'">'
    )


def _kda_ratio_html(kills: str, deaths: str, assists: str) -> str:
    """Format KDA ratio with color coding."""
    try:
        k, d, a = float(kills), float(deaths), float(assists)
        ratio = (k + a) / max(d, 1.0)
        cls = "match-kda__ratio--good" if ratio >= _KDA_RATIO_GOOD_THRESHOLD else "match-kda__ratio"
        return f'<span class="{cls}">{ratio:.2f} KDA</span>'
    except ValueError:
        return ""


def _time_ago(game_start_ms: int) -> str:
    """Return human-readable time-ago string from game start milliseconds."""
    if not game_start_ms:
        return ""
    diff_s = int(time.time()) - game_start_ms // 1000
    if diff_s < 0:
        return "just now"
    if diff_s < _TIME_AGO_HOUR_S:
        return f"{diff_s // 60}m ago"
    if diff_s < _TIME_AGO_DAY_S:
        return f"{diff_s // 3600}h ago"
    return f"{diff_s // 86400}d ago"


def _duration_fmt(seconds: int) -> str:
    """Format game duration as mm:ss."""
    if not seconds:
        return ""
    return f"{seconds // 60}:{seconds % 60:02d}"


def _page(
    title: str,
    body: str,
    path: str = "",
    lang: str | None = None,
    theme: str | None = None,
) -> str:
    """Render a full HTML page with nav, body, and footer.

    When *lang* is ``None`` (the default), reads the active language from the
    ``_current_lang`` context variable set by middleware.  When *theme* is
    ``None``, reads the active theme from ``_current_theme``.
    """
    if lang is None:
        from lol_ui.language import _current_lang

        lang = _current_lang.get()
    if theme is None:
        from lol_ui.themes import _current_theme

        theme = _current_theme.get()
    nav_links = []
    for href, label_key in _NAV_ITEMS:
        active = (href != "/" and path.startswith(href)) or href == path
        cls = ' class="active" aria-current="page"' if active else ""
        nav_links.append(f'<a href="{href}"{cls}>{_t(label_key)}</a>')
    nav_html = "\n  ".join(nav_links)
    lang_switcher = language_switcher_html(lang)
    theme_css = get_theme_css(theme)
    theme_style = f"\n  <style>{theme_css}</style>" if theme_css else ""
    body_class = f' class="theme-{theme}"' if theme != "default" else ""
    theme_switcher = theme_switcher_html(theme)
    html_lang = "zh-Hans" if lang == "zh-CN" else lang
    return f"""<!doctype html>
<html lang="{html_lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>{html.escape(title)} — LoL Pipeline</title>
  <link rel="icon" href="{_FAVICON}">
  <style>{_CSS}</style>{theme_style}
</head>
<body{body_class}>
<a class="skip-link" href="#main-content">Skip to content</a>
{lang_switcher}
<h1>LoL Pipeline</h1>
<nav aria-label="Main navigation">
  {nav_html}
</nav>
<hr>
<main id="main-content">
{body}
</main>
<footer class="site-footer">
{_t_raw("footer_disclaimer")}
</footer>
{theme_switcher}
</body>
</html>"""


def _stats_form(
    msg: str = "",
    css_class: str = "",
    stats_html: str = "",
    selected_region: str = "na1",
    value: str = "",
) -> str:
    if css_class not in _VALID_MSG_CLASSES:
        css_class = "error"
    msg_html = f'<p class="{css_class}">{msg}</p>' if msg else ""
    options = "\n      ".join(
        f'<option value="{r}"{" selected" if r == selected_region else ""}>{r}</option>'
        for r in _REGIONS
    )
    escaped_value = html.escape(value, quote=True)
    hash_encode_js = """<script>
(function() {
  var form = document.querySelector('.form-inline');
  if (!form) return;
  form.addEventListener('submit', function(e) {
    var input = document.getElementById('stats-riot-id');
    if (input && input.value.indexOf('#') !== -1) {
      e.preventDefault();
      var region = document.getElementById('stats-region');
      var url = '/stats?riot_id=' + encodeURIComponent(input.value)
        + '&region=' + (region ? region.value : 'na1');
      window.location.href = url;
    }
  });
})();
</script>"""
    onboarding_html = (
        '<p style="color:var(--color-muted);margin-bottom:var(--space-sm)">'
        "Enter a tracked player&#x2019;s Riot ID and region to view their match statistics."
        "</p>"
        if not msg and not stats_html
        else ""
    )
    return _page(
        _t("stats_form_title"),
        f"""
<h2>{_t("stats_form_title")}</h2>
{onboarding_html}{msg_html}
<form class="form-inline" method="get" action="/stats">
  <label for="stats-riot-id">{_t("stats_form_riot_id_label")}
    <input id="stats-riot-id" name="riot_id"
      placeholder="GameName#TagLine" required value="{escaped_value}">
  </label>
  <label for="stats-region">{_t("stats_form_region_label")}
    <select id="stats-region" name="region">
      {options}
    </select>
  </label>
  <button type="submit">{_t("stats_form_submit")}</button>
</form>
{hash_encode_js}
{
            '<button class="btn btn--refresh"'
            ' onclick="document.querySelector(&apos;.form-inline&apos;).submit()"'
            ">&#8635; Refresh</button>"
            if stats_html
            else ""
        }
{stats_html}
""",
        path="/stats",
    )
