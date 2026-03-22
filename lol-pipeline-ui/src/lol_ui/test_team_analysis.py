"""Tests for team analysis tab (T2-3)."""

from __future__ import annotations

from lol_ui.team_analysis import _team_analysis_html, _team_stat_bar


class TestTeamStatBar:
    """_team_stat_bar renders a single comparison row."""

    def test_typical_values__shows_both_values(self):
        result = _team_stat_bar(15000, 12000, "Gold")
        assert "15,000" in result
        assert "12,000" in result
        assert "Gold" in result

    def test_typical_values__blue_percentage_correct(self):
        result = _team_stat_bar(6000, 4000, "Damage")
        # blue_pct = 6000 / (6000+4000) * 100 = 60%
        assert "60.0%" in result

    def test_zero_sum__shows_fifty_fifty(self):
        result = _team_stat_bar(0, 0, "Gold")
        assert "50.0%" in result

    def test_linear_gradient__present(self):
        result = _team_stat_bar(100, 200, "Kills")
        assert "linear-gradient" in result
        assert "var(--color-win)" in result
        assert "var(--color-loss)" in result

    def test_label_present(self):
        result = _team_stat_bar(10, 20, "Vision")
        assert "Vision" in result

    def test_single_side_dominant__100_percent(self):
        result = _team_stat_bar(5000, 0, "CS")
        assert "100.0%" in result


class TestTeamAnalysisHtml:
    """_team_analysis_html renders all stat comparison rows."""

    def _make_team(
        self,
        *,
        kills: int = 10,
        gold: int = 50000,
        damage: int = 80000,
        cs: int = 600,
        vision: int = 40,
    ) -> list[dict[str, str]]:
        """Create a team of 5 players with distributed stats."""
        per_player = {
            "kills": str(kills // 5),
            "gold_earned": str(gold // 5),
            "total_damage_dealt_to_champions": str(damage // 5),
            "total_minions_killed": str(cs // 5),
            "vision_score": str(vision // 5),
        }
        return [per_player.copy() for _ in range(5)]

    def test_renders_five_stat_rows_without_objectives(self):
        blue = self._make_team()
        red = self._make_team()
        match_data: dict[str, str] = {}
        result = _team_analysis_html(blue, red, match_data)
        assert "Gold" in result
        assert "Damage" in result
        assert "Kills" in result
        assert "CS" in result
        assert "Vision" in result

    def test_objectives_row_hidden_when_data_absent(self):
        blue = self._make_team()
        red = self._make_team()
        match_data: dict[str, str] = {}
        result = _team_analysis_html(blue, red, match_data)
        assert "Objectives" not in result

    def test_objectives_row_shown_when_data_present(self):
        blue = self._make_team()
        red = self._make_team()
        match_data = {
            "team_blue_dragons": "3",
            "team_blue_barons": "1",
            "team_blue_towers": "6",
            "team_blue_heralds": "1",
            "team_red_dragons": "1",
            "team_red_barons": "0",
            "team_red_towers": "3",
            "team_red_heralds": "1",
        }
        result = _team_analysis_html(blue, red, match_data)
        assert "Objectives" in result

    def test_empty_teams__no_crash(self):
        result = _team_analysis_html([], [], {})
        assert "Gold" in result

    def test_result_contains_team_analysis_class(self):
        blue = self._make_team()
        red = self._make_team()
        result = _team_analysis_html(blue, red, {})
        assert "team-analysis" in result

    def test_zero_stats__fifty_fifty_bars(self):
        blue = self._make_team(kills=0, gold=0, damage=0, cs=0, vision=0)
        red = self._make_team(kills=0, gold=0, damage=0, cs=0, vision=0)
        result = _team_analysis_html(blue, red, {})
        assert "50.0%" in result
