"""Match history rendering — list view and lazy-loading section."""

from __future__ import annotations

import html

from lol_ui._helpers import _parse_item_ids
from lol_ui.ddragon import localize_champion_name
from lol_ui.match_badges import _match_badges, _match_badges_html
from lol_ui.rendering import (
    _champion_icon_html,
    _duration_fmt,
    _empty_state,
    _item_icon_html,
    _kda_ratio_html,
    _time_ago,
)

# Re-export page size so callers can import from this module if convenient.
__all__ = [
    "_match_history_html",
    "_match_history_section",
]


def _match_history_section(puuid: str, region: str, riot_id: str) -> str:
    """Render a lazy-loading match history placeholder section."""
    safe_puuid = html.escape(puuid, quote=True)
    safe_region = html.escape(region, quote=True)
    safe_id = html.escape(riot_id, quote=True)
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


def _match_history_html(  # noqa: PLR0913
    matches: list[tuple[str, dict[str, str], dict[str, str]]],
    puuid: str,
    region: str,
    riot_id: str,
    page: int,
    has_more: bool,
    version: str | None = None,
    name_map: dict[str, str] | None = None,
) -> str:
    """Render match history rows + optional next-page button.

    *name_map* localizes champion display names; English IDs are kept for icons.
    """
    if not matches:
        return _empty_state("No match history", "This player has no parsed matches yet.")
    _name_map = name_map or {}
    cards = ""
    for match_id, match_meta, participant in matches:
        win = participant.get("win") == "1"
        row_cls = "match-row--win" if win else "match-row--loss"
        result_cls = "match-result--win" if win else "match-result--loss"
        result_text = "WIN" if win else "LOSS"

        champ_name = participant.get("champion_name", "?")
        display_champ = localize_champion_name(_name_map, champ_name)
        icon = _champion_icon_html(champ_name, version)
        k = html.escape(participant.get("kills", "0"))
        d = html.escape(participant.get("deaths", "0"))
        a = html.escape(participant.get("assists", "0"))
        cs = html.escape(participant.get("total_minions_killed", "0"))
        try:
            duration = _duration_fmt(int(match_meta.get("game_duration", "0")))
        except ValueError:
            duration = ""
        role = html.escape(participant.get("team_position", ""))
        mode = html.escape(match_meta.get("game_mode", ""))
        try:
            game_start = int(match_meta.get("game_start", "0"))
        except ValueError:
            game_start = 0
        ago = _time_ago(game_start)
        kda = _kda_ratio_html(k, d, a)

        # Items: parse the JSON items field
        item_ids = _parse_item_ids(participant)
        items_html = "".join(_item_icon_html(iid, version) for iid in item_ids)
        badges_html = _match_badges_html(_match_badges(participant))

        safe_match_id = html.escape(match_id, quote=True)
        cards += (
            f'<div class="match-row {row_cls}" data-match-id="{safe_match_id}">'
            f'<div class="match-result {result_cls}">{result_text}</div>'
            f'<div class="match-champ">{icon}'
            f'<span class="match-champ__name">{html.escape(display_champ)}</span></div>'
            f'<div class="match-kda">'
            f'<div class="match-kda__score"><span>{k}</span>'
            f'<span class="match-kda__sep">/</span>'
            f'<span class="match-kda__deaths">{d}</span>'
            f'<span class="match-kda__sep">/</span>'
            f"<span>{a}</span></div>"
            f"{kda}</div>"
            f'<div class="match-meta-col">'
            f'<span class="match-meta-col__value">{cs} CS</span>'
            f'<span class="match-meta-col__label">{duration}</span></div>'
            f'<div class="match-items">{items_html}</div>'
            f"{badges_html}"
            f'<div class="match-info-col">'
            f"<span>{mode}</span><span>{role}</span><span>{ago}</span></div>"
            f"</div>"
        )
    safe_puuid = html.escape(puuid, quote=True)
    safe_region = html.escape(region, quote=True)
    safe_id = html.escape(riot_id, quote=True)
    next_btn = ""
    if has_more:
        next_p = page + 1
        next_btn = (
            f'<button class="match-load-more"'
            f' data-puuid="{safe_puuid}" data-region="{safe_region}"'
            f' data-riot-id="{safe_id}" data-page="{next_p}">'
            f"Load more matches</button>"
        )
    return f'<div class="match-list">{cards}</div>{next_btn}'
