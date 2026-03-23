"""Match detail rendering — player rows."""

from __future__ import annotations

import html
from urllib.parse import quote as _url_quote

from lol_ui._helpers import _parse_item_ids
from lol_ui.ddragon import localize_champion_name
from lol_ui.rendering import _champion_icon_html, _item_icon_html


def _render_detail_player(
    p_puuid: str,
    part: dict[str, str],
    player: dict[str, str],
    current_puuid: str,
    max_damage: int,
    version: str | None,
    name_map: dict[str, str] | None = None,
) -> str:
    """Render a single player row inside the match detail expansion.

    *name_map* localizes champion display names; English IDs kept for icons.
    """
    _name_map = name_map or {}
    is_me = p_puuid == current_puuid
    me_cls = " match-detail__player--me" if is_me else ""
    champ = part.get("champion_name", "?")
    display_champ = localize_champion_name(_name_map, champ)
    icon = _champion_icon_html(champ, version)
    name = player.get("game_name", "")
    tag = player.get("tag_line", "")
    display_name = f"{html.escape(name)}#{html.escape(tag)}" if name else html.escape(p_puuid[:8])
    if name and tag:
        name_link = (
            f'<a href="/stats?riot_id={_url_quote(name)}%23{_url_quote(tag)}'
            f'&region={html.escape(player.get("region", "na1"), quote=True)}">'
            f"{display_name}</a>"
        )
    else:
        name_link = display_name
    k = html.escape(part.get("kills", "0"))
    d = html.escape(part.get("deaths", "0"))
    a = html.escape(part.get("assists", "0"))
    cs = html.escape(part.get("total_minions_killed", "0"))
    gold = part.get("gold_earned", "0")
    try:
        gold_k = f"{int(gold) / 1000:.1f}k"
    except ValueError:
        gold_k = gold
    vision = html.escape(part.get("vision_score", "0"))
    try:
        dmg = int(part.get("total_damage_dealt_to_champions", "0"))
    except ValueError:
        dmg = 0
    dmg_pct = min(100, round(dmg / max(max_damage, 1) * 100))
    team_id = part.get("team_id", "")
    fill_cls = (
        "match-detail__dmg-fill match-detail__dmg-fill--blue"
        if team_id != "200"
        else "match-detail__dmg-fill match-detail__dmg-fill--red"
    )
    dmg_str = f"{dmg:,}"

    # Items
    item_ids = _parse_item_ids(part)
    items_html = "".join(_item_icon_html(iid, version) for iid in item_ids)

    safe_champ = html.escape(display_champ)
    return (
        f'<div class="match-detail__player{me_cls}" title="{safe_champ}">'
        f"{icon}"
        f'<div class="match-detail__name">{name_link}</div>'
        f'<div class="match-detail__kda">{k}/{d}/{a}</div>'
        f'<div class="match-detail__stat">{cs} CS</div>'
        f'<div class="match-detail__stat">{vision} V</div>'
        f'<div class="match-detail__stat">{html.escape(gold_k)}</div>'
        f'<div class="match-detail__dmg-bar" title="{dmg_str} dmg">'
        f'<div class="{fill_cls}" style="width:{dmg_pct}%"></div></div>'
        f'<div class="match-detail__items">{items_html}</div>'
        f"</div>"
    )
