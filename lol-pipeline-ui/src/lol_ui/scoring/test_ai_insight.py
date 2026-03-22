"""Tests for rule-based AI Insight (T4-4)."""

from __future__ import annotations

from lol_ui.scoring.ai_insight import (
    _ai_insight_html,
    _evaluate_insight_rules,
)


def _make_stats(
    total_games="20",
    kda="2.5",
    avg_vision_score="15",
    avg_cs_per_min="5.0",
    avg_kills="5",
    avg_deaths="4",
    avg_assists="7",
):
    return {
        "total_games": total_games,
        "kda": kda,
        "avg_vision_score": avg_vision_score,
        "avg_cs_per_min": avg_cs_per_min,
        "avg_kills": avg_kills,
        "avg_deaths": avg_deaths,
        "avg_assists": avg_assists,
    }


# ---------------------------------------------------------------------------
# _evaluate_insight_rules
# ---------------------------------------------------------------------------


class TestEvaluateInsightRules:
    """_evaluate_insight_rules returns observational insight strings."""

    def test_too_few_games__returns_empty(self):
        stats = _make_stats(total_games="3")
        result = _evaluate_insight_rules(stats, [], [])
        assert result == []

    def test_exactly_five_games__evaluates_rules(self):
        stats = _make_stats(total_games="5", kda="4.0")
        result = _evaluate_insight_rules(stats, [], [])
        assert len(result) >= 1

    def test_high_kda__triggers_insight(self):
        stats = _make_stats(kda="4.5")
        result = _evaluate_insight_rules(stats, [], [])
        found = any("KDA" in s for s in result)
        assert found

    def test_low_kda__no_kda_insight(self):
        stats = _make_stats(kda="2.0")
        result = _evaluate_insight_rules(stats, [], [])
        found = any("KDA" in s for s in result)
        assert not found

    def test_low_vision__triggers_insight(self):
        stats = _make_stats(avg_vision_score="5")
        result = _evaluate_insight_rules(stats, [], [])
        found = any("ision" in s.lower() for s in result)
        assert found

    def test_normal_vision__no_vision_insight(self):
        stats = _make_stats(avg_vision_score="20")
        result = _evaluate_insight_rules(stats, [], [])
        found = any("ision" in s.lower() for s in result)
        assert not found

    def test_zero_vision__no_insight(self):
        """avg_vision_score=0 should not trigger (could mean data unavailable)."""
        stats = _make_stats(avg_vision_score="0")
        result = _evaluate_insight_rules(stats, [], [])
        found = any("ision" in s.lower() for s in result)
        assert not found

    def test_high_cs__triggers_insight(self):
        stats = _make_stats(avg_cs_per_min="8.5")
        result = _evaluate_insight_rules(stats, [], [])
        found = any("CS" in s for s in result)
        assert found

    def test_low_cs__no_cs_insight(self):
        stats = _make_stats(avg_cs_per_min="4.0")
        result = _evaluate_insight_rules(stats, [], [])
        found = any("CS" in s for s in result)
        assert not found

    def test_dominant_role__triggers_insight(self):
        stats = _make_stats(total_games="20")
        roles = [("BOTTOM", 15.0), ("JUNGLE", 5.0)]
        result = _evaluate_insight_rules(stats, [], roles)
        found = any("BOTTOM" in s for s in result)
        assert found

    def test_no_dominant_role__no_role_insight(self):
        stats = _make_stats(total_games="20")
        roles = [("TOP", 6.0), ("JUNGLE", 5.0), ("MID", 5.0), ("BOT", 4.0)]
        result = _evaluate_insight_rules(stats, [], roles)
        found = any("TOP" in s for s in result)
        assert not found

    def test_empty_roles__no_role_insight(self):
        stats = _make_stats()
        result = _evaluate_insight_rules(stats, [], [])
        # No crash, no role insight
        assert isinstance(result, list)

    def test_multiple_insights__all_returned(self):
        stats = _make_stats(kda="5.0", avg_cs_per_min="8.0")
        result = _evaluate_insight_rules(stats, [], [])
        assert len(result) >= 2


# ---------------------------------------------------------------------------
# _ai_insight_html
# ---------------------------------------------------------------------------


class TestAiInsightHtml:
    """_ai_insight_html renders the insight panel."""

    def test_too_few_games__shows_not_enough(self):
        stats = _make_stats(total_games="3")
        result = _ai_insight_html(stats, [], [])
        assert "Not enough games" in result

    def test_no_insights__returns_empty(self):
        # Stats that trigger no rules
        stats = _make_stats(kda="2.0", avg_vision_score="20", avg_cs_per_min="4.0")
        result = _ai_insight_html(stats, [], [])
        assert result == ""

    def test_with_insights__has_wrapper(self):
        stats = _make_stats(kda="5.0")
        result = _ai_insight_html(stats, [], [])
        assert "ai-insight" in result

    def test_with_insights__has_list_items(self):
        stats = _make_stats(kda="5.0")
        result = _ai_insight_html(stats, [], [])
        assert "<li" in result
        assert "ai-insight__item" in result

    def test_with_insights__has_header(self):
        stats = _make_stats(kda="5.0")
        result = _ai_insight_html(stats, [], [])
        assert "ai-insight__header" in result

    def test_dominant_role__shows_role_name(self):
        stats = _make_stats(total_games="20")
        roles = [("JUNGLE", 15.0)]
        result = _ai_insight_html(stats, [], roles)
        assert "JUNGLE" in result

    def test_dominant_role__xss_in_role_name__escaped(self):
        """SEC: role name from Redis must be HTML-escaped to prevent XSS."""
        stats = _make_stats(total_games="10")
        malicious = '<script>alert("xss")</script>'
        roles = [(malicious, 8.0)]
        result = _ai_insight_html(stats, [], roles)
        # Raw script tag must NOT appear in output
        assert "<script>" not in result
        # The escaped form must appear
        assert "&lt;script&gt;" in result


class TestEvaluateInsightRulesXss:
    """XSS regression: role names must be escaped in returned insight strings."""

    def test_script_in_role_name__escaped(self):
        stats = _make_stats(total_games="10")
        malicious = "<script>alert(1)</script>"
        roles = [(malicious, 8.0)]
        result = _evaluate_insight_rules(stats, [], roles)
        combined = " ".join(result)
        # Raw tags must not appear — only escaped entities
        assert "<script>" not in combined
        assert "&lt;script&gt;" in combined
