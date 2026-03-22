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

# Re-export constants so callers that previously imported from main.py can use rendering
# as a single entry point if needed.
__all__ = [
    "_badge",
    "_badge_html",
    "_champion_icon_html",
    "_depth_badge",
    "_duration_fmt",
    "_empty_state",
    "_item_icon_html",
    "_kda_ratio_html",
    "_page",
    "_stats_form",
    "_time_ago",
]


def _depth_badge(stream_name: str, depth: int) -> str:
    """Return a status badge based on stream depth thresholds."""
    if stream_name == "stream:dlq":
        if depth > 0:
            return _badge("error", f"{depth} errors")
        return _badge("success", "OK")
    if depth < _DEPTH_BADGE_BUSY_THRESHOLD:
        return _badge("success", "OK")
    if depth < _DEPTH_BADGE_BACKLOG_THRESHOLD:
        return _badge("warning", "Busy")
    return _badge("error", "Backlog")


def _badge(variant: str, text: str) -> str:
    """Render a status badge with auto-escaped text (safe for user-supplied input).

    variant: success|error|warning|info|muted.
    """
    if variant not in _BADGE_VARIANTS:
        msg = f"Invalid badge variant: {variant}"
        raise ValueError(msg)
    return f'<span class="badge badge--{variant}">{html.escape(text)}</span>'


def _badge_html(variant: str, raw_html: str) -> str:
    """Render a status badge with raw HTML content (for trusted HTML entities).

    Use this ONLY for trusted content like ``&#10003;``. For user data, use ``_badge()``.
    variant: success|error|warning|info|muted.
    """
    if variant not in _BADGE_VARIANTS:
        msg = f"Invalid badge variant: {variant}"
        raise ValueError(msg)
    return f'<span class="badge badge--{variant}">{raw_html}</span>'


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


def _page(title: str, body: str, path: str = "", lang: str | None = None) -> str:
    """Render a full HTML page with nav, body, and footer.

    When *lang* is ``None`` (the default), reads the active language from the
    ``_current_lang`` context variable set by middleware.  This avoids threading
    ``lang`` through every call site.
    """
    if lang is None:
        from lol_ui.language import _current_lang

        lang = _current_lang.get()
    nav_links = []
    for href, label in _NAV_ITEMS:
        active = (href != "/" and path.startswith(href)) or href == path
        cls = ' class="active" aria-current="page"' if active else ""
        nav_links.append(f'<a href="{href}"{cls}>{label}</a>')
    nav_html = "\n  ".join(nav_links)
    switcher = language_switcher_html(lang)
    html_lang = "zh-Hans" if lang == "zh-CN" else lang
    return f"""<!doctype html>
<html lang="{html_lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>{title} — LoL Pipeline</title>
  <link rel="icon" href="{_FAVICON}">
  <style>{_CSS}</style>
</head>
<body>
<a class="skip-link" href="#main-content">Skip to content</a>
{switcher}
<h1>LoL Pipeline</h1>
<nav aria-label="Main navigation">
  {nav_html}
</nav>
<hr>
<main id="main-content">
{body}
</main>
<footer class="site-footer">
  LoL Pipeline isn&rsquo;t endorsed by Riot Games and doesn&rsquo;t
  reflect the views or opinions of Riot Games or anyone officially
  involved in producing or managing Riot Games properties.
  League of Legends and Riot Games are trademarks or registered
  trademarks of Riot Games, Inc.
</footer>
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
    return _page(
        "Player Stats",
        f"""
<h2>Player Stats</h2>
{msg_html}
<form class="form-inline" method="get" action="/stats">
  <label for="stats-riot-id">Riot ID:</label>
  <input id="stats-riot-id" name="riot_id"
    placeholder="GameName#TagLine" required value="{escaped_value}">
  <label for="stats-region">Region:</label>
  <select id="stats-region" name="region">
      {options}
  </select>
  <button type="submit">Look Up</button>
</form>
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
