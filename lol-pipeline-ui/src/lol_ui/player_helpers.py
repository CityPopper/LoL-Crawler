"""Player listing helpers — sort and render player table rows."""

from __future__ import annotations

import html
from urllib.parse import quote as _url_quote

from lol_ui.constants import _PlayerRow


def _apply_player_sort(rows: list[_PlayerRow], sort: str) -> list[_PlayerRow]:
    """Return rows sorted by the given key; mutates and returns the list."""
    if sort == "name":
        rows.sort(key=lambda p: p[0].lower())
    elif sort == "region":
        rows.sort(key=lambda p: (p[2].lower(), p[0].lower()))
    return rows


def _render_player_rows(rows: list[_PlayerRow]) -> str:
    """Render player rows as HTML table rows."""
    html_rows = ""
    for game_name, tag_line, region, seeded_at in rows:
        href = (
            f"/stats?riot_id={_url_quote(game_name + '#' + tag_line)}"
            f"&amp;region={html.escape(region)}"
        )
        safe_name = html.escape(f"{game_name}#{tag_line}")
        seeded = html.escape(seeded_at[:10]) if seeded_at else "?"
        html_rows += (
            f'<tr><td><a href="{href}">{safe_name}</a></td>'
            f"<td>{html.escape(region)}</td><td>{seeded}</td></tr>"
        )
    return html_rows
