"""Player listing helpers — sort and render player table rows."""

from __future__ import annotations

import html
from urllib.parse import quote as _url_quote

from lol_ui._helpers import _safe_int
from lol_ui.constants import _DIVISION_ORDER, _TIER_ORDER, _PlayerRow

_UNRANKED_TIER = 99
_UNRANKED_DIVISION = 99
_UNRANKED_LP = -1


def _rank_sort_key(rank: dict[str, str]) -> tuple[int, int, int]:
    """Return a sort key for rank ordering (lower = better).

    Sorts by tier (Challenger=0 .. Iron=9), then division (I=0 .. IV=3),
    then LP descending (negated so higher LP sorts first).
    Unranked players sort last.
    """
    if not rank:
        return (_UNRANKED_TIER, _UNRANKED_DIVISION, _UNRANKED_LP)
    tier = _TIER_ORDER.get(rank.get("tier", ""), _UNRANKED_TIER)
    division = _DIVISION_ORDER.get(rank.get("division", ""), _UNRANKED_DIVISION)
    lp = -_safe_int(rank.get("lp"))  # negate so higher LP sorts first
    return (tier, division, lp)


def _apply_player_sort(
    rows: list[_PlayerRow],
    sort: str,
) -> list[_PlayerRow]:
    """Return rows sorted by the given key; mutates and returns the list."""
    if sort == "name":
        rows.sort(key=lambda p: p[0].lower())
    elif sort == "region":
        rows.sort(key=lambda p: (p[2].lower(), p[0].lower()))
    elif sort == "rank":
        rows.sort(key=lambda p: _rank_sort_key(p[4]))
    return rows


def _format_rank_display(rank: dict[str, str]) -> str:
    """Format rank data as a short display string."""
    if not rank:
        return '<span class="badge badge--muted">Unranked</span>'
    tier = html.escape(rank.get("tier", ""))
    division = html.escape(rank.get("division", ""))
    lp = html.escape(rank.get("lp", "0"))
    return f"{tier} {division} {lp} LP"


def _render_player_rows(rows: list[_PlayerRow]) -> str:
    """Render player rows as HTML table rows."""
    html_rows = ""
    for game_name, tag_line, region, seeded_at, rank in rows:
        href = (
            f"/stats?riot_id={_url_quote(game_name + '#' + tag_line)}"
            f"&amp;region={html.escape(region)}"
        )
        safe_name = html.escape(f"{game_name}#{tag_line}")
        seeded = html.escape(seeded_at[:10]) if seeded_at else "?"
        rank_display = _format_rank_display(rank)
        html_rows += (
            f'<tr><td><a href="{href}">{safe_name}</a></td>'
            f"<td>{html.escape(region)}</td>"
            f"<td>{rank_display}</td>"
            f"<td>{seeded}</td></tr>"
        )
    return html_rows
