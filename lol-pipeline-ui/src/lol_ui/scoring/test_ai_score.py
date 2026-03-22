"""Tests for AI Score computation and tab HTML (T4-2)."""

from __future__ import annotations

from lol_ui.scoring.ai_score import (
    _ai_score_tab_html,
    _component_bar_html,
    _compute_ai_score,
    _compute_kill_participation,
    _grade_badge_html,
    _normalize_stat,
    _score_to_grade,
)

# ---------------------------------------------------------------------------
# _normalize_stat
# ---------------------------------------------------------------------------


class TestNormalizeStat:
    """_normalize_stat maps values to 0-100 range."""

    def test_empty_list__returns_empty(self):
        assert _normalize_stat([]) == []

    def test_all_same__returns_fifty(self):
        result = _normalize_stat([5.0, 5.0, 5.0])
        assert result == [50.0, 50.0, 50.0]

    def test_min_max__zero_and_hundred(self):
        result = _normalize_stat([0.0, 100.0])
        assert result[0] == 0.0
        assert result[1] == 100.0

    def test_intermediate_values(self):
        result = _normalize_stat([0.0, 50.0, 100.0])
        assert result[0] == 0.0
        assert result[1] == 50.0
        assert result[2] == 100.0

    def test_single_value__returns_fifty(self):
        result = _normalize_stat([42.0])
        assert result == [50.0]

    def test_negative_values(self):
        result = _normalize_stat([-10.0, 0.0, 10.0])
        assert result[0] == 0.0
        assert result[1] == 50.0
        assert result[2] == 100.0

    def test_close_values__all_midpoint(self):
        """When max == min, all should be 50."""
        result = _normalize_stat([7.0, 7.0])
        assert all(v == 50.0 for v in result)


# ---------------------------------------------------------------------------
# _compute_kill_participation
# ---------------------------------------------------------------------------


class TestComputeKillParticipation:
    """_compute_kill_participation returns KP as 0.0-1.0."""

    def test_zero_team_kills__returns_zero(self):
        assert _compute_kill_participation(5, 3, 0) == 0.0

    def test_full_participation(self):
        result = _compute_kill_participation(10, 5, 15)
        assert result == 1.0

    def test_half_participation(self):
        result = _compute_kill_participation(3, 2, 10)
        assert result == 0.5

    def test_zero_player_contribution(self):
        result = _compute_kill_participation(0, 0, 20)
        assert result == 0.0

    def test_negative_team_kills__returns_zero(self):
        assert _compute_kill_participation(5, 3, -1) == 0.0


# ---------------------------------------------------------------------------
# _score_to_grade
# ---------------------------------------------------------------------------


class TestScoreToGrade:
    """_score_to_grade maps 0-10 score to letter grade."""

    def test_ten__s_grade(self):
        assert _score_to_grade(10.0) == "S"

    def test_eight__s_grade(self):
        assert _score_to_grade(8.0) == "S"

    def test_seven_nine__a_grade(self):
        assert _score_to_grade(7.9) == "A"

    def test_six_five__a_grade(self):
        assert _score_to_grade(6.5) == "A"

    def test_six_four__b_grade(self):
        assert _score_to_grade(6.4) == "B"

    def test_five__b_grade(self):
        assert _score_to_grade(5.0) == "B"

    def test_four__c_grade(self):
        assert _score_to_grade(4.0) == "C"

    def test_three_five__c_grade(self):
        assert _score_to_grade(3.5) == "C"

    def test_three__d_grade(self):
        assert _score_to_grade(3.0) == "D"

    def test_zero__d_grade(self):
        assert _score_to_grade(0.0) == "D"


# ---------------------------------------------------------------------------
# _compute_ai_score
# ---------------------------------------------------------------------------


_PARTICIPANT_DEFAULTS = {
    "puuid": "p1",
    "kills": "5",
    "deaths": "3",
    "assists": "7",
    "champion_name": "Ahri",
    "team_id": "100",
    "total_damage_dealt_to_champions": "15000",
    "gold_earned": "12000",
    "total_minions_killed": "180",
    "neutral_minions_killed": "0",
    "vision_score": "25",
    "damage_dealt_to_objectives": "5000",
}


def _make_participant(**overrides):
    return {**_PARTICIPANT_DEFAULTS, **overrides}


class TestComputeAiScore:
    """_compute_ai_score computes scores for all participants."""

    def test_empty_participants__returns_empty(self):
        assert _compute_ai_score([], {}) == []

    def test_single_participant__returns_one_result(self):
        participants = [_make_participant()]
        match_data = {"game_duration": "1800"}
        result = _compute_ai_score(participants, match_data)
        assert len(result) == 1

    def test_result_has_required_keys(self):
        participants = [_make_participant()]
        match_data = {"game_duration": "1800"}
        result = _compute_ai_score(participants, match_data)
        entry = result[0]
        assert "puuid" in entry
        assert "champion_name" in entry
        assert "score" in entry
        assert "grade" in entry
        assert "components" in entry

    def test_score_is_between_zero_and_ten(self):
        participants = [
            _make_participant(puuid="p1"),
            _make_participant(
                puuid="p2",
                kills="0",
                deaths="10",
                assists="0",
                total_damage_dealt_to_champions="1000",
                gold_earned="5000",
                vision_score="2",
            ),
        ]
        match_data = {"game_duration": "1800"}
        result = _compute_ai_score(participants, match_data)
        for entry in result:
            score = entry["score"]
            assert isinstance(score, float)
            assert 0.0 <= score <= 10.0

    def test_sorted_by_score_descending(self):
        participants = [
            _make_participant(
                puuid="p1",
                kills="10",
                assists="15",
                deaths="1",
                total_damage_dealt_to_champions="30000",
                gold_earned="18000",
                vision_score="50",
            ),
            _make_participant(
                puuid="p2",
                kills="0",
                deaths="12",
                assists="0",
                total_damage_dealt_to_champions="3000",
                gold_earned="5000",
                vision_score="2",
            ),
        ]
        match_data = {"game_duration": "1800"}
        result = _compute_ai_score(participants, match_data)
        assert float(str(result[0]["score"])) >= float(str(result[1]["score"]))

    def test_components_has_seven_keys(self):
        participants = [_make_participant()]
        match_data = {"game_duration": "1800"}
        result = _compute_ai_score(participants, match_data)
        components = result[0]["components"]
        assert isinstance(components, dict)
        assert len(components) == 7

    def test_single_participant_all_same__midpoint(self):
        """Single player: all stats normalized to 50, score should be 5.0."""
        participants = [_make_participant()]
        match_data = {"game_duration": "1800"}
        result = _compute_ai_score(participants, match_data)
        assert result[0]["score"] == 5.0

    def test_grade_assigned_correctly(self):
        participants = [_make_participant()]
        match_data = {"game_duration": "1800"}
        result = _compute_ai_score(participants, match_data)
        # Score 5.0 -> B grade
        assert result[0]["grade"] == "B"

    def test_zero_duration__no_crash(self):
        participants = [_make_participant()]
        match_data = {"game_duration": "0"}
        result = _compute_ai_score(participants, match_data)
        assert len(result) == 1

    def test_missing_stats__defaults_to_zero(self):
        participants = [{"puuid": "p1", "team_id": "100"}]
        match_data = {"game_duration": "1800"}
        result = _compute_ai_score(participants, match_data)
        assert len(result) == 1

    def test_ten_participants__all_scored(self):
        participants = [
            _make_participant(puuid="p" + str(i), team_id="100" if i < 5 else "200")
            for i in range(10)
        ]
        match_data = {"game_duration": "1800"}
        result = _compute_ai_score(participants, match_data)
        assert len(result) == 10


# ---------------------------------------------------------------------------
# _grade_badge_html
# ---------------------------------------------------------------------------


class TestGradeBadgeHtml:
    """_grade_badge_html renders badge with tooltip."""

    def test_s_grade__has_grade_class(self):
        result = _grade_badge_html("S")
        assert "grade--S" in result

    def test_d_grade__has_grade_class(self):
        result = _grade_badge_html("D")
        assert "grade--D" in result

    def test_has_tooltip(self):
        result = _grade_badge_html("S")
        assert 'title="' in result
        assert "Exceptional" in result

    def test_a_grade_tooltip(self):
        result = _grade_badge_html("A")
        assert "Great" in result


# ---------------------------------------------------------------------------
# _component_bar_html
# ---------------------------------------------------------------------------


class TestComponentBarHtml:
    """_component_bar_html renders a single stat sub-bar."""

    def test_contains_label(self):
        result = _component_bar_html("kda", 75.0)
        assert "KDA" in result

    def test_contains_percentage_width(self):
        result = _component_bar_html("kda", 80.0)
        assert "width:80%" in result

    def test_clamps_over_hundred(self):
        result = _component_bar_html("kda", 150.0)
        assert "width:100%" in result

    def test_clamps_negative(self):
        result = _component_bar_html("kda", -10.0)
        assert "width:0%" in result

    def test_has_component_class(self):
        result = _component_bar_html("vision", 50.0)
        assert "ai-score__component" in result


# ---------------------------------------------------------------------------
# _ai_score_tab_html
# ---------------------------------------------------------------------------


class TestAiScoreTabHtml:
    """_ai_score_tab_html renders the full AI Score tab."""

    def test_empty_scores__shows_warning(self):
        result = _ai_score_tab_html([], "p1", None)
        assert "unavailable" in result

    def test_renders_all_players(self):
        scores = [
            {"puuid": "p1", "champion_name": "Ahri", "score": 8.0, "grade": "S", "components": {}},
            {"puuid": "p2", "champion_name": "Zed", "score": 5.0, "grade": "B", "components": {}},
        ]
        result = _ai_score_tab_html(scores, "p1", None)
        assert "Ahri" in result
        assert "Zed" in result

    def test_focused_player__has_me_class(self):
        scores = [
            {
                "puuid": "p1",
                "champion_name": "Ahri",
                "score": 8.0,
                "grade": "S",
                "components": {"kda": 90.0},
            },
        ]
        result = _ai_score_tab_html(scores, "p1", None)
        assert "ai-score__row--me" in result

    def test_non_focused__no_me_class(self):
        scores = [
            {"puuid": "p2", "champion_name": "Zed", "score": 5.0, "grade": "B", "components": {}},
        ]
        result = _ai_score_tab_html(scores, "p1", None)
        assert "ai-score__row--me" not in result

    def test_focused_player__has_breakdown(self):
        scores = [
            {
                "puuid": "p1",
                "champion_name": "Ahri",
                "score": 8.0,
                "grade": "S",
                "components": {
                    "kda": 90.0,
                    "vision": 50.0,
                    "damage_share": 60.0,
                    "gold_share": 55.0,
                    "cs_per_min": 70.0,
                    "kill_participation": 80.0,
                    "objective_contribution": 40.0,
                },
            },
        ]
        result = _ai_score_tab_html(scores, "p1", None)
        assert "ai-score__breakdown" in result

    def test_non_focused__no_breakdown(self):
        scores = [
            {
                "puuid": "p2",
                "champion_name": "Zed",
                "score": 5.0,
                "grade": "B",
                "components": {"kda": 50.0},
            },
        ]
        result = _ai_score_tab_html(scores, "p1", None)
        assert "ai-score__breakdown" not in result

    def test_has_grade_badges(self):
        scores = [
            {"puuid": "p1", "champion_name": "Ahri", "score": 8.0, "grade": "S", "components": {}},
        ]
        result = _ai_score_tab_html(scores, "p1", None)
        assert "grade--S" in result

    def test_has_score_value(self):
        scores = [
            {"puuid": "p1", "champion_name": "Ahri", "score": 7.5, "grade": "A", "components": {}},
        ]
        result = _ai_score_tab_html(scores, "p1", None)
        assert "7.5" in result

    def test_stat_num_class(self):
        scores = [
            {"puuid": "p1", "champion_name": "Ahri", "score": 8.0, "grade": "S", "components": {}},
        ]
        result = _ai_score_tab_html(scores, "p1", None)
        assert "stat-num" in result
