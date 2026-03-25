"""Rank card, rank history, and profile header HTML rendering."""

from __future__ import annotations

import html
from datetime import UTC, datetime

from lol_pipeline.i18n import label as _ilabel

from lol_ui._helpers import _safe_int, _win_rate
from lol_ui.constants import _RANK_WR_THRESHOLD
from lol_ui.language import _current_lang
from lol_ui.summoner_icon import _summoner_icon_html


def _rank_card_html(rank: dict[str, str]) -> str:
    """Render a rank card from player:rank:{puuid} hash data."""
    if not rank:
        return ""
    lang = _current_lang.get()
    tier = rank.get("tier", "")
    division = rank.get("division", "")
    tier_label = _ilabel("tier", tier, lang)
    lp = html.escape(rank.get("lp", "0"))
    wins = _safe_int(rank.get("wins"))
    losses = _safe_int(rank.get("losses"))
    total = wins + losses
    wr = round(_win_rate(wins, total))
    wr_color = "var(--color-win)" if wr >= _RANK_WR_THRESHOLD else "var(--color-loss)"
    return (
        f'<div class="card" style="display:flex;align-items:center;gap:var(--space-md)">'
        f"<div>"
        f'<div style="font-family:var(--font-sans);font-size:var(--font-size-lg);font-weight:700">'
        f"{html.escape(tier_label)} {html.escape(division)}</div>"
        f'<div style="font-size:var(--font-size-sm);color:var(--color-muted)">'
        f"{lp} LP &mdash; {wins}W {losses}L</div>"
        f'<div style="background:var(--color-surface2);'
        f'border-radius:4px;height:6px;margin-top:6px">'
        f'<div style="background:{wr_color};width:{wr}%;'
        f'height:6px;border-radius:4px"></div></div>'
        f"</div>"
        f'<div style="margin-left:auto;text-align:right">'
        f'<span style="font-family:var(--font-sans);'
        f"font-size:var(--font-size-xl);"
        f'font-weight:700;color:{wr_color}">{wr}%</span>'
        f'<div style="font-size:10px;color:var(--color-muted)">Win Rate</div></div></div>'
    )


def _rank_history_html(entries: list[tuple[str, float]]) -> str:
    """Render rank history as a table from ZRANGE WITHSCORES data.

    Each entry is ``("TIER:DIVISION:LP", epoch_ms_score)``.
    """
    if not entries:
        return ""
    lang = _current_lang.get()
    rows = ""
    for value, score in entries:
        parts = value.split(":", 2)
        raw_tier = parts[0] if len(parts) > 0 else ""
        tier = html.escape(_ilabel("tier", raw_tier, lang))
        division = html.escape(parts[1]) if len(parts) > 1 else ""
        lp = html.escape(parts[2]) if len(parts) > 2 else "0"
        dt = datetime.fromtimestamp(score / 1000, tz=UTC)
        date_str = dt.strftime("%Y-%m-%d %H:%M")
        rows += (
            f"<tr><td>{html.escape(date_str)}</td><td>{tier} {division}</td><td>{lp} LP</td></tr>"
        )
    return (
        '<h3>Rank History</h3><div class="table-scroll">'
        "<table><thead><tr>"
        '<th scope="col">Date</th><th scope="col">Rank</th>'
        '<th scope="col">LP</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def _profile_header_html(
    game_name: str,
    tag_line: str,
    rank: dict[str, str],
    icon_id: str | None = None,
    level: str | None = None,
    version: str | None = None,
) -> str:
    """Render a profile header with summoner icon (or letter fallback), name, and rank."""
    safe_name = html.escape(game_name)
    safe_tag = html.escape(tag_line)
    tier = rank.get("tier", "UNRANKED") if rank else "UNRANKED"
    division = rank.get("division", "") if rank else ""
    lp = rank.get("lp", "0") if rank else "0"
    from lol_ui.strings import t

    lang = _current_lang.get()
    if tier != "UNRANKED":
        tier_label = _ilabel("tier", tier, lang)
        rank_text = f"{tier_label} {division}".strip()
    else:
        rank_text = t("unranked")

    if icon_id and version:
        avatar_html = _summoner_icon_html(icon_id, level, version)
    else:
        # Fallback: letter-circle with optional level badge
        avatar_html = (
            '<div style="position:relative;display:inline-block">'
            '<div style="width:64px;height:64px;border-radius:50%;'
            "background:var(--color-surface2);"
            "display:flex;align-items:center;justify-content:center;"
            "border:3px solid var(--color-win);flex-shrink:0;"
            "font-family:var(--font-sans);font-size:28px;"
            f'font-weight:700;color:var(--color-win)">'
            f"{html.escape(game_name[:1].upper())}</div></div>"
        )

    return (
        f'<div class="card" style="display:flex;'
        f"align-items:center;gap:var(--space-lg);"
        f'padding:var(--space-lg)">'
        f"{avatar_html}"
        f"<div>"
        f'<div style="font-family:var(--font-sans);'
        f'font-size:var(--font-size-xl);font-weight:700">'
        f"{safe_name}"
        f'<span style="color:var(--color-muted);'
        f'font-size:var(--font-size-base)">#{safe_tag}</span></div>'
        f'<div style="font-size:var(--font-size-sm);color:var(--color-muted);margin-top:2px">'
        f"{html.escape(rank_text)} &mdash; {html.escape(lp)} LP</div>"
        f"</div></div>"
    )
