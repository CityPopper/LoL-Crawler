"""Localization string table and lookup functions.

All user-facing strings for new Sprint 0-5 features go through ``t()`` (auto-escaped)
or ``t_raw()`` (unescaped, for intentional HTML).  Both languages must have identical
key sets; zh-CN values start as ``[CN] …`` placeholders until real translations land.
"""

from __future__ import annotations

import html as _html

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # -- Tab labels --
        "win": "Win",
        "loss": "Loss",
        "ai_score": "AI Score",
        "team_analysis": "Team Analysis",
        "build": "Build",
        "overview": "Overview",
        "timeline": "Timeline",
        # -- Empty states --
        "no_timeline_data": "Timeline data unavailable for this match.",
        "no_build_data": "Build data unavailable for this match.",
        "not_enough_games": "Not enough games for an insight yet.",
        "no_skill_data": "Skill data requires timeline.",
        "no_kill_data": "No kill events recorded for this match.",
        "no_match_history": "No matches found.",
        # -- Grade labels --
        "grade_s": "Exceptional",
        "grade_a": "Great",
        "grade_b": "Good",
        "grade_c": "Below Average",
        "grade_d": "Poor",
        # -- Team labels --
        "blue_team": "Blue Team",
        "red_team": "Red Team",
        # -- Common --
        "loading": "Loading\u2026",
        "load_more": "Load More",
        "player_stats": "Player Stats",
        "build_order": "Build Order",
        "match_details_unavailable": "Match details not available.",
        # -- Stat labels --
        "gold": "Gold",
        "damage": "Damage",
        "kills": "Kills",
        "cs": "CS",
        "vision": "Vision",
        "objectives": "Objectives",
        "kda": "KDA",
        "damage_share": "Damage Share",
        "gold_share": "Gold Share",
        "cs_per_min": "CS/min",
        "kill_participation": "Kill Participation",
        "objective_contribution": "Objective Contribution",
        # -- Build tab --
        "final_items": "Final Items",
        "skill_order": "Skill Order",
        "runes": "Runes",
        "summoner_spells": "Summoner Spells",
        # -- AI Insight --
        "ai_insight_title": "AI Insight",
        "insight_high_kda": "Maintains a high KDA ratio.",
        "insight_low_vision": "Vision score below average.",
        "insight_high_cs": "Consistently high CS per minute.",
        "insight_dominant_role_prefix": "Primarily plays",
        # -- Gold chart --
        "gold_over_time": "Gold Over Time",
        # -- Sprint 5 --
        "recently_played_with": "Recently Played With",
        "games_shared": "games",
        "minimap": "Kill Map",
        "sparkline_7d": "7-Day Win Rate",
    },
    "zh-CN": {
        # -- Tab labels --
        "win": "[CN] Win",
        "loss": "[CN] Loss",
        "ai_score": "[CN] AI Score",
        "team_analysis": "[CN] Team Analysis",
        "build": "[CN] Build",
        "overview": "[CN] Overview",
        "timeline": "[CN] Timeline",
        # -- Empty states --
        "no_timeline_data": "[CN] Timeline data unavailable for this match.",
        "no_build_data": "[CN] Build data unavailable for this match.",
        "not_enough_games": "[CN] Not enough games for an insight yet.",
        "no_skill_data": "[CN] Skill data requires timeline.",
        "no_kill_data": "[CN] No kill events recorded for this match.",
        "no_match_history": "[CN] No matches found.",
        # -- Grade labels --
        "grade_s": "[CN] Exceptional",
        "grade_a": "[CN] Great",
        "grade_b": "[CN] Good",
        "grade_c": "[CN] Below Average",
        "grade_d": "[CN] Poor",
        # -- Team labels --
        "blue_team": "[CN] Blue Team",
        "red_team": "[CN] Red Team",
        # -- Common --
        "loading": "[CN] Loading\u2026",
        "load_more": "[CN] Load More",
        "player_stats": "[CN] Player Stats",
        "build_order": "[CN] Build Order",
        "match_details_unavailable": "[CN] Match details not available.",
        # -- Stat labels --
        "gold": "[CN] Gold",
        "damage": "[CN] Damage",
        "kills": "[CN] Kills",
        "cs": "[CN] CS",
        "vision": "[CN] Vision",
        "objectives": "[CN] Objectives",
        "kda": "[CN] KDA",
        "damage_share": "[CN] Damage Share",
        "gold_share": "[CN] Gold Share",
        "cs_per_min": "[CN] CS/min",
        "kill_participation": "[CN] Kill Participation",
        "objective_contribution": "[CN] Objective Contribution",
        # -- Build tab --
        "final_items": "[CN] Final Items",
        "skill_order": "[CN] Skill Order",
        "runes": "[CN] Runes",
        "summoner_spells": "[CN] Summoner Spells",
        # -- AI Insight --
        "ai_insight_title": "[CN] AI Insight",
        "insight_high_kda": "[CN] Maintains a high KDA ratio.",
        "insight_low_vision": "[CN] Vision score below average.",
        "insight_high_cs": "[CN] Consistently high CS per minute.",
        "insight_dominant_role_prefix": "[CN] Primarily plays",
        # -- Gold chart --
        "gold_over_time": "[CN] Gold Over Time",
        # -- Sprint 5 --
        "recently_played_with": "[CN] Recently Played With",
        "games_shared": "[CN] games",
        "minimap": "[CN] Kill Map",
        "sparkline_7d": "[CN] 7-Day Win Rate",
    },
}

_LANG: str = "en"


def t(key: str) -> str:
    """Return localized string, HTML-escaped by default.

    Falls back to the key itself when the key is not found.
    """
    raw = _STRINGS.get(_LANG, _STRINGS["en"]).get(key, key)
    return _html.escape(raw)


def t_raw(key: str) -> str:
    """Return localized string without escaping (for intentional HTML)."""
    return _STRINGS.get(_LANG, _STRINGS["en"]).get(key, key)
