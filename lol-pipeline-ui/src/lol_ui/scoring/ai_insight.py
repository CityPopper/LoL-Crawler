"""Rule-based AI Insight — observational tone, no prescriptive advice.

Provides:
- ``_evaluate_insight_rules(stats, champs, roles)`` — rule evaluation
- ``_ai_insight_html(stats, champs, roles)`` — formatted HTML div
"""

from __future__ import annotations

import html as html_mod

from lol_ui._helpers import _safe_float
from lol_ui.strings import t

# Minimum games required before generating insights
_MIN_GAMES = 5

# Rule thresholds
_HIGH_KDA_THRESHOLD = 3.0
_LOW_VISION_THRESHOLD = 10.0
_HIGH_CS_THRESHOLD = 7.0
_DOMINANT_ROLE_PCT = 0.60


def _evaluate_insight_rules(
    stats: dict[str, str],
    champs: list[tuple[str, float]],
    roles: list[tuple[str, float]],
) -> list[str]:
    """Evaluate insight rules and return a list of localized insight strings.

    Rules are observational, not prescriptive:
    - High KDA (>3.0): "Maintains a high KDA ratio."
    - Low vision (<10 avg): "Vision score below average."
    - High CS (>7/min): "Consistently high CS per minute."
    - Dominant role (>60%): "Primarily plays {role}."

    Returns empty list when total games < 5.
    """
    total_games = _safe_float(stats.get("total_games", "0"))
    if total_games < _MIN_GAMES:
        return []

    insights: list[str] = []

    # High KDA check
    kda = _safe_float(stats.get("kda", "0"))
    if kda >= _HIGH_KDA_THRESHOLD:
        insights.append(t("insight_high_kda"))

    # Low vision check
    avg_vision = _safe_float(stats.get("avg_vision_score", "0"))
    if avg_vision > 0 and avg_vision < _LOW_VISION_THRESHOLD:
        insights.append(t("insight_low_vision"))

    # High CS check
    avg_cs_per_min = _safe_float(stats.get("avg_cs_per_min", "0"))
    if avg_cs_per_min >= _HIGH_CS_THRESHOLD:
        insights.append(t("insight_high_cs"))

    # Dominant role check
    if roles and total_games > 0:
        top_role_name, top_role_games = roles[0]
        if top_role_games / total_games >= _DOMINANT_ROLE_PCT:
            insights.append(
                t("insight_dominant_role_prefix") + " " + html_mod.escape(top_role_name) + "."
            )

    return insights


def _ai_insight_html(
    stats: dict[str, str],
    champs: list[tuple[str, float]],
    roles: list[tuple[str, float]],
) -> str:
    """Render the AI Insight panel as an HTML div.

    Shows observational insights based on player stats.
    Returns "Not enough games" message when < 5 games.
    Returns empty string when no insights are generated.
    """
    total_games = _safe_float(stats.get("total_games", "0"))
    if total_games < _MIN_GAMES:
        return '<div class="ai-insight"><p class="warning">' + t("not_enough_games") + "</p></div>"

    insights = _evaluate_insight_rules(stats, champs, roles)
    if not insights:
        return ""

    items: list[str] = []
    for insight in insights:
        items.append('<li class="ai-insight__item">' + insight + "</li>")

    body = "<ul>" + "".join(items) + "</ul>"
    return (
        '<div class="ai-insight">'
        '<div class="ai-insight__header">' + t("ai_insight_title") + "</div>" + body + "</div>"
    )
