"""Localization string table and lookup functions.

All user-facing strings for new Sprint 0-5 features go through ``t()`` (auto-escaped)
or ``t_raw()`` (unescaped, for intentional HTML).  Both languages must have identical
key sets.

``t()`` and ``t_raw()`` accept an optional ``lang`` parameter (default ``"en"``).
Callers pass the language resolved from the request cookie / Accept-Language header.
"""

from __future__ import annotations

import html as _html

SUPPORTED_LANGUAGES: list[str] = ["en", "zh-CN"]

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
        "win": "\u80dc\u5229",
        "loss": "\u5931\u8d25",
        "ai_score": "AI\u8bc4\u5206",
        "team_analysis": "\u56e2\u961f\u5206\u6790",
        "build": "\u51fa\u88c5",
        "overview": "\u6982\u89c8",
        "timeline": "\u65f6\u95f4\u7ebf",
        # -- Empty states --
        "no_timeline_data": "\u672c\u573a\u6bd4\u8d5b\u65e0\u65f6\u95f4\u7ebf\u6570\u636e\u3002",
        "no_build_data": "\u672c\u573a\u6bd4\u8d5b\u65e0\u51fa\u88c5\u6570\u636e\u3002",
        "not_enough_games": "游戏局数不足，无法生成分析。",
        "no_skill_data": "\u6280\u80fd\u6570\u636e\u9700\u8981\u65f6\u95f4\u7ebf\u3002",
        "no_kill_data": "\u672c\u573a\u6bd4\u8d5b\u65e0\u51fb\u6740\u4e8b\u4ef6\u8bb0\u5f55\u3002",
        "no_match_history": "\u672a\u627e\u5230\u6bd4\u8d5b\u8bb0\u5f55\u3002",
        # -- Grade labels --
        "grade_s": "\u5353\u8d8a",
        "grade_a": "\u4f18\u79c0",
        "grade_b": "\u826f\u597d",
        "grade_c": "\u4e00\u822c",
        "grade_d": "\u8f83\u5dee",
        # -- Team labels --
        "blue_team": "\u84dd\u8272\u65b9",
        "red_team": "\u7ea2\u8272\u65b9",
        # -- Common --
        "loading": "\u52a0\u8f7d\u4e2d\u2026",
        "load_more": "\u52a0\u8f7d\u66f4\u591a",
        "player_stats": "\u73a9\u5bb6\u6570\u636e",
        "build_order": "\u51fa\u88c5\u987a\u5e8f",
        "match_details_unavailable": "\u6bd4\u8d5b\u8be6\u60c5\u4e0d\u53ef\u7528\u3002",
        # -- Stat labels --
        "gold": "\u91d1\u5e01",
        "damage": "\u4f24\u5bb3",
        "kills": "\u51fb\u6740",
        "cs": "\u8865\u5200",
        "vision": "\u89c6\u91ce",
        "objectives": "\u76ee\u6807",
        "kda": "KDA",
        "damage_share": "\u4f24\u5bb3\u5360\u6bd4",
        "gold_share": "\u91d1\u5e01\u5360\u6bd4",
        "cs_per_min": "\u6bcf\u5206\u8865\u5200",
        "kill_participation": "\u53c2\u56e2\u7387",
        "objective_contribution": "\u76ee\u6807\u8d21\u732e",
        # -- Build tab --
        "final_items": "\u6700\u7ec8\u88c5\u5907",
        "skill_order": "\u6280\u80fd\u52a0\u70b9",
        "runes": "\u7b26\u6587",
        "summoner_spells": "\u53ec\u5524\u5e08\u6280\u80fd",
        # -- AI Insight --
        "ai_insight_title": "AI\u5206\u6790",
        "insight_high_kda": "\u4fdd\u6301\u8f83\u9ad8\u7684KDA\u6bd4\u7387\u3002",
        "insight_low_vision": "\u89c6\u91ce\u5f97\u5206\u4f4e\u4e8e\u5e73\u5747\u6c34\u5e73\u3002",
        "insight_high_cs": "持续保持较高的每分补刀。",
        "insight_dominant_role_prefix": "\u4e3b\u8981\u626e\u6f14",
        # -- Gold chart --
        "gold_over_time": "\u91d1\u5e01\u8d70\u52bf",
        # -- Sprint 5 --
        "recently_played_with": "\u6700\u8fd1\u4e00\u8d77\u73a9\u7684",
        "games_shared": "\u573a",
        "minimap": "\u51fb\u6740\u5730\u56fe",
        "sparkline_7d": "7\u5929\u80dc\u7387",
    },
}


def t(key: str, lang: str = "en") -> str:
    """Return localized string, HTML-escaped by default.

    Falls back to the key itself when the key is not found.
    """
    raw = _STRINGS.get(lang, _STRINGS["en"]).get(key, key)
    return _html.escape(raw)


def t_raw(key: str, lang: str = "en") -> str:
    """Return localized string without escaping (for intentional HTML)."""
    return _STRINGS.get(lang, _STRINGS["en"]).get(key, key)
