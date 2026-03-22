"""Match detail rendering — player rows and build order sections."""

from __future__ import annotations

import html
from urllib.parse import quote as _url_quote

from lol_ui._helpers import _parse_item_ids
from lol_ui.rendering import _champion_icon_html, _item_icon_html


def _render_detail_player(
    p_puuid: str,
    part: dict[str, str],
    player: dict[str, str],
    current_puuid: str,
    max_damage: int,
    version: str | None,
) -> str:
    """Render a single player row inside the match detail expansion."""
    is_me = p_puuid == current_puuid
    me_cls = " match-detail__player--me" if is_me else ""
    champ = part.get("champion_name", "?")
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

    return (
        f'<div class="match-detail__player{me_cls}">'
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


def _render_build_section(
    blue_team: list[tuple[str, dict[str, str], dict[str, str], list[str]]],
    red_team: list[tuple[str, dict[str, str], dict[str, str], list[str]]],
    version: str | None,
) -> str:
    """Render build order section showing item purchase path per player."""
    all_players = blue_team + red_team
    # Only show if at least one player has build data
    if not any(build for _, _, _, build in all_players):
        return ""
    rows = ""
    for _puuid, part, _player, build in all_players:
        if not build:
            continue
        champ = part.get("champion_name", "?")
        icon = _champion_icon_html(champ, version)
        items_html = ""
        for i, item_id in enumerate(build):
            items_html += _item_icon_html(item_id, version)
            if i < len(build) - 1:
                items_html += '<span class="match-detail__build-arrow">\u2192</span>'
        rows += (
            f'<div class="match-detail__build-row">'
            f"{icon}"
            f'<div class="match-detail__build-name">{html.escape(champ)}</div>'
            f'<div class="match-detail__build-items">{items_html}</div>'
            f"</div>"
        )
    if not rows:
        return ""
    return (
        f'<div class="match-detail__build">'
        f'<div class="match-detail__build-label">Build Order</div>'
        f"{rows}</div>"
    )
