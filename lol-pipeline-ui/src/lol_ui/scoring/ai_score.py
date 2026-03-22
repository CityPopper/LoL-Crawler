"""AI Score computation and tab HTML generation.

Provides:
- ``_normalize_stat(values)`` — min-max normalize to 0-100
- ``_compute_kill_participation(...)`` — KP calculation
- ``_compute_ai_score(participants, team_ids)`` — full scoring
- ``_ai_score_tab_html(scores, focused_puuid, version)`` — tab HTML
"""

from __future__ import annotations

import html

from lol_ui._helpers import _safe_int
from lol_ui.rendering import _champion_icon_html
from lol_ui.strings import t

# Component weights summing to 1.0
_WEIGHTS: list[tuple[str, float]] = [
    ("kda", 0.25),
    ("damage_share", 0.20),
    ("gold_share", 0.15),
    ("cs_per_min", 0.15),
    ("vision", 0.10),
    ("kill_participation", 0.10),
    ("objective_contribution", 0.05),
]

# Grade thresholds (score 0-10)
_GRADE_THRESHOLDS: list[tuple[float, str]] = [
    (8.0, "S"),
    (6.5, "A"),
    (5.0, "B"),
    (3.5, "C"),
]

_GRADE_TOOLTIPS: dict[str, str] = {
    "S": "grade_s",
    "A": "grade_a",
    "B": "grade_b",
    "C": "grade_c",
    "D": "grade_d",
}


def _normalize_stat(values: list[float]) -> list[float]:
    """Min-max normalize a list of values to 0-100 range.

    When max == min, returns 50 for all values (midpoint).
    Returns an empty list for empty input.
    """
    if not values:
        return []
    min_v = min(values)
    max_v = max(values)
    if max_v == min_v:
        return [50.0] * len(values)
    return [round((v - min_v) / (max_v - min_v) * 100, 2) for v in values]


def _compute_kill_participation(
    player_kills: int,
    player_assists: int,
    team_total_kills: int,
) -> float:
    """Compute kill participation as a 0.0-1.0 fraction.

    Returns 0.0 when the team has zero kills.
    """
    if team_total_kills <= 0:
        return 0.0
    return (player_kills + player_assists) / team_total_kills


def _score_to_grade(score: float) -> str:
    """Map a 0-10 score to a letter grade."""
    for threshold, grade in _GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "D"


def _team_totals(
    participants: list[dict[str, str]],
    key: str,
) -> dict[str, int]:
    """Sum a stat by team_id across all participants."""
    totals: dict[str, int] = {}
    for p in participants:
        tid = p.get("team_id", "100")
        totals[tid] = totals.get(tid, 0) + _safe_int(p.get(key, "0"))
    return totals


def _player_raw_stats(
    p: dict[str, str],
    duration_min: float,
    team_kills: dict[str, int],
    team_damage: dict[str, int],
    team_gold: dict[str, int],
) -> dict[str, float]:
    """Extract raw stat values for a single participant."""
    tid = p.get("team_id", "100")
    k = _safe_int(p.get("kills", "0"))
    d = _safe_int(p.get("deaths", "0"))
    a = _safe_int(p.get("assists", "0"))
    cs = _safe_int(p.get("total_minions_killed", "0"))
    neutral = _safe_int(p.get("neutral_minions_killed", "0"))
    dmg = _safe_int(p.get("total_damage_dealt_to_champions", "0"))
    gold = _safe_int(p.get("gold_earned", "0"))
    return {
        "kda": (k + a) / max(d, 1),
        "damage_share": dmg / max(team_damage.get(tid, 1), 1),
        "gold_share": gold / max(team_gold.get(tid, 1), 1),
        "cs_per_min": (cs + neutral) / duration_min,
        "vision": float(_safe_int(p.get("vision_score", "0"))),
        "kill_participation": _compute_kill_participation(k, a, team_kills.get(tid, 0)),
        "objective_contribution": float(_safe_int(p.get("damage_dealt_to_objectives", "0"))),
    }


def _compute_ai_score(
    participants: list[dict[str, str]],
    match_data: dict[str, str],
) -> list[dict[str, object]]:
    """Compute AI Score for all participants.

    Returns a list of dicts sorted by score descending, each containing:
        - puuid: str
        - champion_name: str
        - score: float (0-10)
        - grade: str (S/A/B/C/D)
        - components: dict[str, float] (normalized 0-100 values)

    Returns an empty list when participants is empty.
    """
    if not participants:
        return []

    duration_s = _safe_int(match_data.get("game_duration", "0"))
    duration_min = max(duration_s / 60, 1.0)
    team_kills = _team_totals(participants, "kills")
    team_damage = _team_totals(participants, "total_damage_dealt_to_champions")
    team_gold = _team_totals(participants, "gold_earned")

    # Collect raw stats per player
    all_raw: list[dict[str, float]] = [
        _player_raw_stats(p, duration_min, team_kills, team_damage, team_gold) for p in participants
    ]

    # Normalize each stat across all participants
    stat_keys = [k for k, _ in _WEIGHTS]
    normalized: dict[str, list[float]] = {}
    for sk in stat_keys:
        normalized[sk] = _normalize_stat([r[sk] for r in all_raw])

    # Compute weighted scores
    results: list[dict[str, object]] = []
    for i, p in enumerate(participants):
        weighted_sum = 0.0
        components: dict[str, float] = {}
        for stat_name, weight in _WEIGHTS:
            norm_val = normalized[stat_name][i]
            components[stat_name] = round(norm_val, 1)
            weighted_sum += norm_val * weight
        score = min(10.0, max(0.0, round(weighted_sum / 10, 1)))
        results.append(
            {
                "puuid": p.get("puuid", ""),
                "champion_name": p.get("champion_name", "?"),
                "score": score,
                "grade": _score_to_grade(score),
                "components": components,
            }
        )

    results.sort(key=lambda x: float(str(x.get("score", 0))), reverse=True)
    return results


def _grade_badge_html(grade: str) -> str:
    """Render a grade badge with tooltip."""
    tooltip_key = _GRADE_TOOLTIPS.get(grade, "grade_d")
    tooltip = t(tooltip_key)
    return (
        '<span class="badge grade--'
        + html.escape(grade)
        + '" title="'
        + tooltip
        + '">'
        + html.escape(grade)
        + "</span>"
    )


def _component_bar_html(label_key: str, value: float) -> str:
    """Render a single component sub-bar for the focused player."""
    label = t(label_key)
    pct = str(min(100, max(0, round(value))))
    return (
        '<div class="ai-score__component">'
        '<span class="ai-score__component-label">' + label + "</span>"
        '<div class="ai-score__component-track">'
        '<div class="ai-score__component-fill" style="width:' + pct + '%"></div>'
        "</div>"
        '<span class="ai-score__component-val">' + pct + "</span>"
        "</div>"
    )


def _ai_score_tab_html(
    scores: list[dict[str, object]],
    focused_puuid: str,
    version: str | None,
) -> str:
    """Render the AI Score tab content.

    Shows all 10 players ranked by score with grade badges.
    The focused player also gets a component breakdown with sub-bars.
    Returns a warning message when scores is empty.
    """
    if not scores:
        return '<p class="warning">' + t("ai_score") + " unavailable.</p>"

    rows: list[str] = []
    for entry in scores:
        puuid = str(entry.get("puuid", ""))
        champ = str(entry.get("champion_name", "?"))
        score = entry.get("score", 0)
        grade = str(entry.get("grade", "D"))
        is_focused = puuid == focused_puuid

        icon = _champion_icon_html(champ, version)
        grade_html = _grade_badge_html(grade)
        score_str = str(score)

        me_cls = " ai-score__row--me" if is_focused else ""

        row = (
            '<div class="ai-score__row'
            + me_cls
            + '">'
            + icon
            + '<span class="ai-score__champ">'
            + html.escape(champ)
            + "</span>"
            + '<span class="ai-score__score stat-num">'
            + score_str
            + "</span>"
            + grade_html
            + "</div>"
        )
        rows.append(row)

        # Show component breakdown for focused player only
        if is_focused:
            components = entry.get("components", {})
            if isinstance(components, dict):
                bars: list[str] = []
                for stat_name, _weight in _WEIGHTS:
                    val = components.get(stat_name, 50.0)
                    if isinstance(val, (int, float)):
                        bars.append(_component_bar_html(stat_name, val))
                rows.append('<div class="ai-score__breakdown">' + "".join(bars) + "</div>")

    body = "".join(rows)
    return '<div class="ai-score-tab">' + body + "</div>"
