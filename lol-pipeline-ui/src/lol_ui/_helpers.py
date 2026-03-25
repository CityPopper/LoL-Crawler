"""Shared UI helpers — small utilities used across multiple modules."""

from __future__ import annotations

import html as _html_mod
import json
import re
from collections import Counter


def _safe_int(value: str | None, default: int = 0) -> int:
    """Parse an integer from a string, returning *default* on failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _safe_float(value: str | None, default: float = 0.0) -> float:
    """Parse a float from a string, returning *default* on failure."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _win_rate(wins: int, games: int) -> float:
    """Compute win rate as a 0-100 percentage. Returns 0.0 when games is 0."""
    return (wins / games * 100) if games > 0 else 0.0


def _kda(kills: int, deaths: int, assists: int) -> float:
    """Compute KDA ratio: ``(kills + assists) / max(deaths, 1)``."""
    return (kills + assists) / max(deaths, 1)


def _parse_item_ids(participant: dict[str, str], *, slots: int = 7) -> list[str]:
    """Parse item IDs from a participant hash, padded to *slots* entries.

    Handles both JSON arrays (``"[3006,3047,0,...]"``) and comma-separated
    strings (``"3006,3047,0,..."``) stored in the ``items`` field.

    Returns a list of string item IDs, always exactly *slots* long,
    padded with ``"0"`` for empty slots.
    """
    raw_items = participant.get("items", "")
    try:
        item_list = json.loads(raw_items) if raw_items.startswith("[") else raw_items.split(",")
    except (json.JSONDecodeError, AttributeError):
        item_list = []
    return (list(map(str, item_list)) + ["0"] * slots)[:slots]


def _champion_datalist(name_map: dict[str, str]) -> str:
    """Render a ``<datalist>`` element with champion names for autocomplete."""
    if not name_map:
        return ""
    options = "\n".join(
        f'<option value="{_html_mod.escape(display_name)}">'
        for display_name in sorted(name_map.values())
    )
    return f'<datalist id="champion-list">\n{options}\n</datalist>'


def _role_options(lang: str) -> str:
    """Render localized ``<option>`` elements for the role dropdown."""
    from lol_pipeline.i18n import label

    roles = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
    return "\n    ".join(
        f'<option value="{key}">{_html_mod.escape(label("role", key, lang))}</option>'
        for key in roles
    )


# ---------------------------------------------------------------------------
# Language request utilities (REFACTOR-10)
# ---------------------------------------------------------------------------

_LANG_DEFAULT = "en"
_LANG_COOKIE_NAME = "lang"
_LANG_COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 year


def get_lang(request: object) -> str:
    """Resolve the active language from request cookie or Accept-Language header.

    Priority: ``lang`` cookie > ``Accept-Language`` header > ``"en"`` default.
    """
    from lol_ui.strings import SUPPORTED_LANGUAGES

    cookie_val: str = getattr(request, "cookies", {}).get(_LANG_COOKIE_NAME, "")
    if cookie_val in SUPPORTED_LANGUAGES:
        return cookie_val

    accept: str = getattr(request, "headers", {}).get("accept-language", "")
    for token in accept.split(","):
        tag = token.split(";")[0].strip()
        if tag in SUPPORTED_LANGUAGES:
            return tag
        base = tag.split("-")[0]
        for supported in SUPPORTED_LANGUAGES:
            if supported.split("-")[0] == base and supported != _LANG_DEFAULT:
                return supported

    return _LANG_DEFAULT


def set_lang_cookie(response: object, lang: str) -> None:
    """Set the ``lang`` cookie on *response*."""
    from lol_ui.strings import SUPPORTED_LANGUAGES

    safe_lang = lang if lang in SUPPORTED_LANGUAGES else _LANG_DEFAULT
    set_cookie = getattr(response, "set_cookie", None)
    if callable(set_cookie):
        set_cookie(
            key=_LANG_COOKIE_NAME,
            value=safe_lang,
            max_age=_LANG_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )


# ---------------------------------------------------------------------------
# Theme request utilities (REFACTOR-10)
# ---------------------------------------------------------------------------

_THEME_DEFAULT = "default"
_THEME_COOKIE_NAME = "theme"
_THEME_COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 year


def get_theme(request: object) -> str:
    """Resolve the active theme from request cookie."""
    from lol_ui.themes import SUPPORTED_THEMES

    cookie_val: str = getattr(request, "cookies", {}).get(_THEME_COOKIE_NAME, "")
    if cookie_val in SUPPORTED_THEMES:
        return cookie_val
    return _THEME_DEFAULT


def set_theme_cookie(response: object, theme: str) -> None:
    """Set the ``theme`` cookie on *response*."""
    from lol_ui.themes import SUPPORTED_THEMES

    safe_theme = theme if theme in SUPPORTED_THEMES else _THEME_DEFAULT
    set_cookie = getattr(response, "set_cookie", None)
    if callable(set_cookie):
        set_cookie(
            key=_THEME_COOKIE_NAME,
            value=safe_theme,
            max_age=_THEME_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )


# ---------------------------------------------------------------------------
# Rendering helpers extracted from modules (REFACTOR-11)
# ---------------------------------------------------------------------------


def _match_history_section(puuid: str, region: str, riot_id: str) -> str:
    """Render a lazy-loading match history placeholder section."""
    safe_puuid = _html_mod.escape(puuid, quote=True)
    safe_region = _html_mod.escape(region, quote=True)
    safe_id = _html_mod.escape(riot_id, quote=True)
    return f"""
<h3>Match History</h3>
<div id="match-history-container" data-puuid="{safe_puuid}"
  data-region="{safe_region}" data-riot-id="{safe_id}">
<div class="loading-state"><span class="spinner"></span> Loading\u2026</div></div>
<script>
var _loadingMatches = false;
var _matchObserver = null;
function loadMatches(puuid, region, riotId, page) {{
  if (_loadingMatches) return;
  _loadingMatches = true;
  var container = document.getElementById('match-history-container');
  var isFirst = page === 0;
  var btn = null;
  if (!isFirst) {{
    btn = container.querySelector('.match-load-more');
    if (btn) {{ btn.textContent = 'Loading\u2026'; btn.style.pointerEvents = 'none'; }}
  }}
  var url = '/stats/matches?puuid=' + encodeURIComponent(puuid)
    + '&region=' + encodeURIComponent(region)
    + '&riot_id=' + encodeURIComponent(riotId)
    + '&page=' + page;
  fetch(url, {{headers: {{'Accept': 'text/html'}}}})
    .then(function(r) {{ if (!r.ok) {{ throw new Error('HTTP ' + r.status); }} return r.text(); }})
    .then(function(h) {{
      if (isFirst) {{
        container.innerHTML = h;
      }} else {{
        var tmp = document.createElement('div');
        tmp.innerHTML = h;
        var existingList = container.querySelector('.match-list');
        var newList = tmp.querySelector('.match-list');
        if (existingList && newList) {{
          Array.from(newList.children).forEach(function(row) {{
            existingList.appendChild(row.cloneNode(true));
          }});
        }}
        var oldBtn = container.querySelector('.match-load-more');
        if (oldBtn) {{ oldBtn.remove(); }}
        var newBtn = tmp.querySelector('.match-load-more');
        if (newBtn) {{ container.appendChild(newBtn.cloneNode(true)); }}
      }}
      _observeLoadMore();
    }})
    .catch(function(e) {{
      if (btn) {{
        btn.textContent = 'Load more';
        btn.style.pointerEvents = '';
      }}
      var existing = container.querySelector('.error');
      if (existing) existing.remove();
      var p = document.createElement('p');
      p.className = 'error';
      p.textContent = 'Failed to load: ' + (e.message || e);
      container.appendChild(p);
    }})
    .finally(function() {{ _loadingMatches = false; }});
}}
function _observeLoadMore() {{
  if (_matchObserver) {{ _matchObserver.disconnect(); }}
  var btn = document.querySelector('.match-load-more');
  if (!btn) return;
  _matchObserver = new IntersectionObserver(function(entries) {{
    if (entries[0].isIntersecting) {{
      btn.click();
    }}
  }}, {{rootMargin: '200px'}});
  _matchObserver.observe(btn);
  btn.addEventListener('click', function(e) {{
    e.preventDefault();
    loadMatches(btn.dataset.puuid, btn.dataset.region, btn.dataset.riotId, +btn.dataset.page);
  }});
}}
function toggleMatchDetail(row) {{
  var detail = row.nextElementSibling;
  if (detail && detail.classList.contains('match-detail')) {{
    detail.classList.toggle('open');
    return;
  }}
  var matchId = row.dataset.matchId;
  if (!matchId) return;
  var c = document.getElementById('match-history-container');
  var puuid = c ? c.dataset.puuid : '';
  var winCls = row.classList.contains('match-row--win')
    ? ' match-detail--win' : ' match-detail--loss';
  detail = document.createElement('div');
  detail.className = 'match-detail open' + winCls;
  detail.innerHTML = '<div class="loading-state">'
    + '<span class="spinner"></span> Loading\u2026</div>';
  row.after(detail);
  fetch('/stats/match-detail?match_id=' + encodeURIComponent(matchId)
    + '&puuid=' + encodeURIComponent(puuid),
    {{headers: {{'Accept': 'text/html'}}}})
    .then(function(r) {{
      if (!r.ok) throw new Error('HTTP ' + r.status); return r.text();
    }})
    .then(function(h) {{
      detail.innerHTML = h;
      if (typeof initMatchTabs==='function') initMatchTabs(detail);
    }})
    .catch(function() {{
      detail.innerHTML = '<p class="error">Failed to load details</p>';
    }});
}}
(function() {{
  var c = document.getElementById('match-history-container');
  if (c) {{
    loadMatches(c.dataset.puuid, c.dataset.region, c.dataset.riotId, 0);
  }}
}})();
document.addEventListener('click', function(e) {{
  var row = e.target.closest('.match-row');
  if (row) toggleMatchDetail(row);
}});
</script>
"""


def _count_co_players(
    participant_sets: list[set[str]],
    current_puuid: str,
) -> Counter[str]:
    """Count how many matches each co-player shares with *current_puuid*.

    Returns a Counter mapping co-player PUUIDs to shared game counts.
    The current player is excluded from the count.
    """
    counter: Counter[str] = Counter()
    for pset in participant_sets:
        for p in pset:
            if p != current_puuid:
                counter[p] += 1
    return counter


_DDRAGON_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _validate_ddragon_version(version: str) -> bool:
    """Return True if *version* matches DDragon semver format ``X.Y.Z``."""
    return bool(_DDRAGON_VERSION_RE.match(version))
