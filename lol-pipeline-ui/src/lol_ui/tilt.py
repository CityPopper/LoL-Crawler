"""Tilt / streak indicator computation and HTML rendering."""

from __future__ import annotations

import html

from lol_ui._helpers import _safe_int
from lol_ui.constants import (
    _TILT_KDA_THRESHOLD,
    _TILT_MIN_STREAK_DISPLAY,
    _TILT_RECENT_COUNT,
    _TILT_RECENT_KDA_COUNT,
)
from lol_ui.rendering import _badge


def _streak_indicator(matches: list[dict[str, str]]) -> dict[str, object]:
    """Compute tilt/streak data from recent match participant data.

    Each dict in *matches* must have ``win``, ``kills``, ``deaths``, ``assists``
    keys (string values from Redis HGETALL).  Matches are ordered newest-first.

    Returns a dict with:
      streak_type  - "win" | "loss" | "none"
      streak_count - int (consecutive from most recent)
      recent_wr    - float 0-100 (win rate over all supplied matches)
      kda_trend    - "rising" | "falling" | "neutral"
    """
    if not matches:
        return {
            "streak_type": "none",
            "streak_count": 0,
            "recent_wr": 0.0,
            "kda_trend": "neutral",
        }

    # --- streak ---
    first_win = str(matches[0].get("win", "0")) == "1"
    streak_type = "win" if first_win else "loss"
    streak_count = 0
    for m in matches:
        is_win = str(m.get("win", "0")) == "1"
        if is_win == first_win:
            streak_count += 1
        else:
            break

    # --- recent win rate ---
    wins = sum(1 for m in matches if str(m.get("win", "0")) == "1")
    recent_wr = round(wins / len(matches) * 100, 1) if matches else 0.0

    # --- KDA trend (last 5 vs 6-20) ---
    def _avg_kda(group: list[dict[str, str]]) -> float:
        if not group:
            return 0.0
        total = 0.0
        for m in group:
            k = _safe_int(m.get("kills", "0"))
            d = _safe_int(m.get("deaths", "0"))
            a = _safe_int(m.get("assists", "0"))
            total += (k + a) / max(d, 1)
        return total / len(group)

    recent_kda = _avg_kda(matches[:_TILT_RECENT_KDA_COUNT])
    older_kda = _avg_kda(matches[_TILT_RECENT_KDA_COUNT:])

    kda_trend = "neutral"
    if older_kda > 0:
        ratio = (recent_kda - older_kda) / older_kda
        if ratio >= _TILT_KDA_THRESHOLD:
            kda_trend = "rising"
        elif ratio <= -_TILT_KDA_THRESHOLD:
            kda_trend = "falling"

    return {
        "streak_type": streak_type,
        "streak_count": streak_count,
        "recent_wr": recent_wr,
        "kda_trend": kda_trend,
    }


def _tilt_banner_html(indicator: dict[str, object]) -> str:
    """Render a tilt/streak banner from ``_streak_indicator`` output.

    Returns empty string when there is nothing notable to show.
    """
    parts: list[str] = []

    streak_type = indicator.get("streak_type", "none")
    streak_count = int(str(indicator.get("streak_count", 0)))
    kda_trend = str(indicator.get("kda_trend", "neutral"))

    # Streak badge (only shown at or above minimum streak threshold)
    if streak_count >= _TILT_MIN_STREAK_DISPLAY:
        if streak_type == "win":
            label = f"W{streak_count}"
            parts.append(_badge("success", label))
        elif streak_type == "loss":
            label = f"L{streak_count}"
            parts.append(_badge("error", label))

    # KDA trend arrow
    if kda_trend == "rising":
        parts.append(
            '<span class="badge badge--success" title="KDA trending up">&uarr; Rising</span>'
        )
    elif kda_trend == "falling":
        parts.append(
            '<span class="badge badge--error" title="KDA trending down">&darr; Falling</span>'
        )

    if not parts:
        return ""

    recent_wr = indicator.get("recent_wr", 0.0)
    wr_str = f"{recent_wr:.0f}%" if isinstance(recent_wr, float) else f"{recent_wr}%"
    wr_html = (
        f'<span style="font-size:var(--font-size-sm);color:var(--color-muted)">'
        f"Last {_TILT_RECENT_COUNT} WR: {html.escape(wr_str)}</span>"
    )
    return (
        f'<div class="tilt-indicator" '
        f'style="display:flex;align-items:center;gap:var(--space-sm);'
        f'margin:var(--space-sm) 0">'
        f"{' '.join(parts)} {wr_html}</div>"
    )
