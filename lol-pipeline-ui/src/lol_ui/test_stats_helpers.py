"""Tests for stats_helpers._group_participants and related helpers."""

from __future__ import annotations

import json

from lol_ui.stats_helpers import _group_participants


def _make_pipe_results(
    participants: list[tuple[str, dict[str, str], dict[str, str], str | None, str | None]],
) -> tuple[list[str], list[object]]:
    """Build (sorted_puuids, pipe_results) from a list of participant tuples.

    Each tuple: (puuid, participant_data, player_data, build_raw, skills_raw).
    pipe_results is flattened: [part, player, build, skills, part, player, ...].
    """
    puuids: list[str] = []
    results: list[object] = []
    for puuid, part, player, build, skills in participants:
        puuids.append(puuid)
        results.append(part)
        results.append(player)
        results.append(build)
        results.append(skills)
    return puuids, results


class TestGroupParticipants:
    """_group_participants groups pipeline results into blue/red teams."""

    def test_blue_and_red_teams(self) -> None:
        """Participants are grouped by team_id: 100=blue, 200=red."""
        puuids, pipe = _make_pipe_results([
            (
                "blue1",
                {"team_id": "100", "champion_name": "Jinx", "kills": "3", "deaths": "1",
                 "assists": "5", "total_damage_dealt_to_champions": "20000"},
                {"game_name": "BluePlayer", "tag_line": "NA1"},
                None,
                None,
            ),
            (
                "red1",
                {"team_id": "200", "champion_name": "Darius", "kills": "7", "deaths": "2",
                 "assists": "3", "total_damage_dealt_to_champions": "25000"},
                {"game_name": "RedPlayer", "tag_line": "NA1"},
                None,
                None,
            ),
        ])

        blue, red, skill_orders, max_dmg = _group_participants(puuids, pipe)

        assert len(blue) == 1
        assert len(red) == 1
        assert blue[0][0] == "blue1"
        assert red[0][0] == "red1"
        assert max_dmg == 25000

    def test_empty_participants(self) -> None:
        """Empty puuids list returns empty teams and max_damage=1."""
        blue, red, skill_orders, max_dmg = _group_participants([], [])

        assert blue == []
        assert red == []
        assert skill_orders == {}
        assert max_dmg == 1

    def test_mixed_teams(self) -> None:
        """Multiple participants on both teams are correctly separated."""
        puuids, pipe = _make_pipe_results([
            (
                "b1",
                {"team_id": "100", "total_damage_dealt_to_champions": "10000"},
                {"game_name": "B1"},
                None,
                None,
            ),
            (
                "r1",
                {"team_id": "200", "total_damage_dealt_to_champions": "15000"},
                {"game_name": "R1"},
                None,
                None,
            ),
            (
                "b2",
                {"team_id": "100", "total_damage_dealt_to_champions": "30000"},
                {"game_name": "B2"},
                None,
                None,
            ),
            (
                "r2",
                {"team_id": "200", "total_damage_dealt_to_champions": "5000"},
                {"game_name": "R2"},
                None,
                None,
            ),
        ])

        blue, red, _, max_dmg = _group_participants(puuids, pipe)

        assert len(blue) == 2
        assert len(red) == 2
        blue_puuids = {e[0] for e in blue}
        red_puuids = {e[0] for e in red}
        assert blue_puuids == {"b1", "b2"}
        assert red_puuids == {"r1", "r2"}
        assert max_dmg == 30000

    def test_missing_participant_data_skipped(self) -> None:
        """Participants with empty/falsy participant_data are skipped."""
        puuids = ["p1", "p2"]
        pipe = [
            {},  # p1 participant_data: empty dict (falsy)
            {"game_name": "P1"},
            None,
            None,
            {"team_id": "100", "total_damage_dealt_to_champions": "8000"},  # p2
            {"game_name": "P2"},
            None,
            None,
        ]

        blue, red, _, max_dmg = _group_participants(puuids, pipe)

        assert len(blue) == 1
        assert blue[0][0] == "p2"
        assert red == []
        assert max_dmg == 8000

    def test_default_team_is_blue(self) -> None:
        """Participant without team_id defaults to blue team."""
        puuids, pipe = _make_pipe_results([
            (
                "no_team",
                {"champion_name": "Ahri", "total_damage_dealt_to_champions": "5000"},
                {"game_name": "NoTeam"},
                None,
                None,
            ),
        ])

        blue, red, _, _ = _group_participants(puuids, pipe)

        assert len(blue) == 1
        assert len(red) == 0
        assert blue[0][0] == "no_team"

    def test_build_order_parsed(self) -> None:
        """Valid JSON build_raw is parsed into build_order list."""
        puuids, pipe = _make_pipe_results([
            (
                "p1",
                {"team_id": "100", "total_damage_dealt_to_champions": "10000"},
                {"game_name": "P1"},
                json.dumps([3006, 3047, 3089]),
                None,
            ),
        ])

        blue, _, _, _ = _group_participants(puuids, pipe)

        assert len(blue) == 1
        build_order = blue[0][3]
        assert build_order == ["3006", "3047", "3089"]

    def test_build_order_invalid_json(self) -> None:
        """Invalid JSON in build_raw results in empty build_order."""
        puuids, pipe = _make_pipe_results([
            (
                "p1",
                {"team_id": "100", "total_damage_dealt_to_champions": "10000"},
                {"game_name": "P1"},
                "not-valid-json{{{",
                None,
            ),
        ])

        blue, _, _, _ = _group_participants(puuids, pipe)

        assert len(blue) == 1
        assert blue[0][3] == []

    def test_skill_orders_parsed(self) -> None:
        """Valid JSON skills_raw is parsed into skill_orders dict."""
        puuids, pipe = _make_pipe_results([
            (
                "p1",
                {"team_id": "100", "total_damage_dealt_to_champions": "10000"},
                {"game_name": "P1"},
                None,
                json.dumps(["Q", "W", "E", "Q", "Q"]),
            ),
        ])

        _, _, skill_orders, _ = _group_participants(puuids, pipe)

        assert "p1" in skill_orders
        assert skill_orders["p1"] == ["Q", "W", "E", "Q", "Q"]

    def test_skill_orders_invalid_json(self) -> None:
        """Invalid JSON in skills_raw is silently ignored."""
        puuids, pipe = _make_pipe_results([
            (
                "p1",
                {"team_id": "100", "total_damage_dealt_to_champions": "10000"},
                {"game_name": "P1"},
                None,
                "not-json",
            ),
        ])

        _, _, skill_orders, _ = _group_participants(puuids, pipe)

        assert "p1" not in skill_orders

    def test_skill_orders_non_list_ignored(self) -> None:
        """Non-list JSON in skills_raw is not stored as skill order."""
        puuids, pipe = _make_pipe_results([
            (
                "p1",
                {"team_id": "100", "total_damage_dealt_to_champions": "10000"},
                {"game_name": "P1"},
                None,
                json.dumps({"not": "a list"}),
            ),
        ])

        _, _, skill_orders, _ = _group_participants(puuids, pipe)

        assert "p1" not in skill_orders

    def test_damage_non_numeric_defaults_to_zero(self) -> None:
        """Non-numeric damage value defaults to 0, does not affect max_damage."""
        puuids, pipe = _make_pipe_results([
            (
                "p1",
                {"team_id": "100", "total_damage_dealt_to_champions": "bad"},
                {"game_name": "P1"},
                None,
                None,
            ),
        ])

        _, _, _, max_dmg = _group_participants(puuids, pipe)

        # max_damage starts at 1, dmg defaults to 0, so max stays at 1
        assert max_dmg == 1

    def test_max_damage_tracks_highest(self) -> None:
        """max_damage reflects the highest damage across all participants."""
        puuids, pipe = _make_pipe_results([
            (
                "p1",
                {"team_id": "100", "total_damage_dealt_to_champions": "10000"},
                {},
                None,
                None,
            ),
            (
                "p2",
                {"team_id": "200", "total_damage_dealt_to_champions": "50000"},
                {},
                None,
                None,
            ),
            (
                "p3",
                {"team_id": "100", "total_damage_dealt_to_champions": "30000"},
                {},
                None,
                None,
            ),
        ])

        _, _, _, max_dmg = _group_participants(puuids, pipe)

        assert max_dmg == 50000
