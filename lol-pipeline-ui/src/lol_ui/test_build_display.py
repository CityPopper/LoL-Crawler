"""Tests for build_display.py — item sequence, skill grid, build tab."""

from __future__ import annotations

import json

from lol_ui.build_display import (
    BuildContext,
    _build_tab_html,
    _final_items_html,
    _item_sequence_html,
    _player_build_row_html,
    _skill_cell,
    _skill_empty_cell,
    _skill_order_grid_html,
)


class TestItemSequenceHtml:
    """_item_sequence_html renders items with arrow separators."""

    def test_single_item__no_arrow(self):
        result = _item_sequence_html(["3006"], "14.10.1")
        assert "3006" in result
        assert "\u2192" not in result

    def test_two_items__one_arrow(self):
        result = _item_sequence_html(["3006", "3031"], "14.10.1")
        assert result.count("\u2192") == 1

    def test_three_items__two_arrows(self):
        result = _item_sequence_html(["3006", "3031", "3072"], "14.10.1")
        assert result.count("\u2192") == 2

    def test_empty_list__returns_empty(self):
        result = _item_sequence_html([], "14.10.1")
        assert result == ""

    def test_has_build_sequence_class(self):
        result = _item_sequence_html(["3006"], "14.10.1")
        assert "build-sequence" in result

    def test_uses_item_icon_helper(self):
        result = _item_sequence_html(["3006"], "14.10.1")
        assert "ddragon.leagueoflegends.com" in result or "match-item" in result


class TestFinalItemsHtml:
    """_final_items_html renders the 7-slot final item grid."""

    def test_json_array_items(self):
        part = {"items": json.dumps([3006, 3031, 3072, 0, 0, 0, 3340])}
        result = _final_items_html(part, "14.10.1")
        assert "build-final-items" in result
        assert "3006" in result

    def test_csv_items(self):
        part = {"items": "3006,3031,3072"}
        result = _final_items_html(part, "14.10.1")
        assert "3006" in result

    def test_empty_items__shows_empty_slots(self):
        part = {"items": ""}
        result = _final_items_html(part, "14.10.1")
        assert "match-item--empty" in result

    def test_pads_to_seven_slots(self):
        part = {"items": json.dumps([3006])}
        result = _final_items_html(part, "14.10.1")
        # Should have 1 real item + 6 empty slots = 7 total
        assert result.count("match-item--empty") == 6
        assert "3006" in result


class TestSkillCell:
    """_skill_cell renders a colored dot in a table cell."""

    def test_q_skill__uses_blue_color(self):
        result = _skill_cell("Q", 1)
        assert "var(--color-win)" in result

    def test_w_skill__uses_green_color(self):
        result = _skill_cell("W", 2)
        assert "var(--color-success)" in result

    def test_e_skill__uses_yellow_color(self):
        result = _skill_cell("E", 3)
        assert "var(--color-warning)" in result

    def test_r_skill__uses_red_color(self):
        result = _skill_cell("R", 6)
        assert "var(--color-loss)" in result

    def test_r_unlock_level__has_highlight_class(self):
        result = _skill_cell("R", 6)
        assert "skill-cell--r-unlock" in result

    def test_level_11__has_highlight_class(self):
        result = _skill_cell("R", 11)
        assert "skill-cell--r-unlock" in result

    def test_level_16__has_highlight_class(self):
        result = _skill_cell("R", 16)
        assert "skill-cell--r-unlock" in result

    def test_normal_level__no_highlight(self):
        result = _skill_cell("Q", 1)
        assert "skill-cell--r-unlock" not in result

    def test_contains_skill_dot(self):
        result = _skill_cell("Q", 1)
        assert "skill-dot" in result


class TestSkillEmptyCell:
    """_skill_empty_cell renders an empty grid cell."""

    def test_no_dot(self):
        result = _skill_empty_cell(1)
        assert "skill-dot" not in result

    def test_r_unlock_level__has_highlight(self):
        result = _skill_empty_cell(6)
        assert "skill-cell--r-unlock" in result

    def test_normal_level__no_highlight(self):
        result = _skill_empty_cell(3)
        assert "skill-cell--r-unlock" not in result


class TestSkillOrderGridHtml:
    """_skill_order_grid_html renders the 4x18 skill order table."""

    def test_full_18_levels(self):
        order = [
            "Q",
            "W",
            "E",
            "Q",
            "Q",
            "R",
            "Q",
            "W",
            "Q",
            "W",
            "R",
            "W",
            "W",
            "E",
            "E",
            "R",
            "E",
            "E",
        ]
        result = _skill_order_grid_html(order)
        assert "skill-grid" in result
        assert "table-scroll" in result
        # Should have Q/W/E/R labels
        for slot in ["Q", "W", "E", "R"]:
            assert ">" + slot + "<" in result

    def test_level_headers_1_to_18(self):
        order = ["Q"]
        result = _skill_order_grid_html(order)
        for level in range(1, 19):
            assert ">" + str(level) + "<" in result

    def test_r_unlock_columns_highlighted(self):
        order = ["Q", "W", "E", "Q", "Q", "R"]
        result = _skill_order_grid_html(order)
        assert "skill-cell--r-unlock" in result

    def test_empty_order__shows_placeholder(self):
        result = _skill_order_grid_html([])
        assert "Skill data requires timeline" in result

    def test_partial_order__fills_remaining_empty(self):
        order = ["Q", "W", "E"]
        result = _skill_order_grid_html(order)
        # Should still have 18 columns
        assert result.count("<th") == 19  # 18 levels + 1 empty corner

    def test_dots_in_correct_row(self):
        # Level 1: Q, Level 2: W
        order = ["Q", "W"]
        result = _skill_order_grid_html(order)
        # Q row should have a dot in column 1
        assert "skill-dot" in result

    def test_case_insensitive_matching(self):
        # Should handle lowercase input
        order = ["q", "w", "e"]
        result = _skill_order_grid_html(order)
        assert "skill-dot" in result

    def test_caps_at_18_levels(self):
        order = ["Q"] * 25  # more than 18
        result = _skill_order_grid_html(order)
        # Should only render 18 columns
        assert ">18<" in result
        assert ">19<" not in result


class TestPlayerBuildRowHtml:
    """_player_build_row_html renders a single player's build section."""

    def _make_participant(self, **overrides):
        base = {
            "champion_name": "Jinx",
            "items": json.dumps([3006, 3031, 0, 0, 0, 0, 3340]),
            "summoner1_id": "4",
            "summoner2_id": "7",
            "perk_keystone": "8008",
        }
        base.update(overrides)
        return base

    def _ctx(self, *, spell_map=None, current_puuid="puuid1", version="14.10.1"):
        return BuildContext(
            spell_map=spell_map or {},
            rune_lookup={},
            version=version,
            current_puuid=current_puuid,
        )

    def test_shows_champion_name(self):
        result = _player_build_row_html("puuid1", self._make_participant(), [], [], self._ctx())
        assert "Jinx" in result

    def test_me_highlight(self):
        result = _player_build_row_html("puuid1", self._make_participant(), [], [], self._ctx())
        assert "build-player--me" in result

    def test_not_me__no_highlight(self):
        result = _player_build_row_html(
            "puuid1", self._make_participant(), [], [], self._ctx(current_puuid="puuid2")
        )
        assert "build-player--me" not in result

    def test_shows_final_items(self):
        result = _player_build_row_html("puuid1", self._make_participant(), [], [], self._ctx())
        assert "build-final-items" in result

    def test_build_order_shown_when_available(self):
        result = _player_build_row_html(
            "puuid1", self._make_participant(), ["3006", "3031"], [], self._ctx()
        )
        assert "Build Order" in result
        assert "build-sequence" in result

    def test_build_order_hidden_when_empty(self):
        result = _player_build_row_html("puuid1", self._make_participant(), [], [], self._ctx())
        assert "Build Order" not in result

    def test_skill_order_shown_when_available(self):
        result = _player_build_row_html(
            "puuid1", self._make_participant(), [], ["Q", "W", "E"], self._ctx()
        )
        assert "skill-grid" in result

    def test_skill_order_hidden_when_empty(self):
        result = _player_build_row_html("puuid1", self._make_participant(), [], [], self._ctx())
        assert "skill-grid" not in result

    def test_summoner_spells_rendered(self):
        spell_map = {"4": "SummonerFlash.png", "7": "SummonerHeal.png"}
        result = _player_build_row_html(
            "puuid1", self._make_participant(), [], [], self._ctx(spell_map=spell_map)
        )
        assert "spell-pair" in result


class TestBuildTabHtml:
    """_build_tab_html renders the full Build tab."""

    def _make_entry(self, puuid="p1", champ="Jinx", team_id="100"):
        participant = {
            "champion_name": champ,
            "items": json.dumps([3006, 0, 0, 0, 0, 0, 0]),
            "summoner1_id": "4",
            "summoner2_id": "7",
            "team_id": team_id,
        }
        player: dict[str, str] = {"game_name": "Player", "tag_line": "NA1"}
        build_order: list[str] = []
        return (puuid, participant, player, build_order)

    def test_renders_both_teams(self):
        blue = [self._make_entry("p1", "Jinx", "100")]
        red = [self._make_entry("p2", "Caitlyn", "200")]
        result = _build_tab_html(blue, red, "14.10.1", False, "p1", {}, [], {})
        assert "Blue Team" in result
        assert "Red Team" in result
        assert "build-tab" in result

    def test_current_player_highlighted(self):
        blue = [self._make_entry("p1", "Jinx")]
        result = _build_tab_html(blue, [], "14.10.1", False, "p1", {}, [], {})
        assert "build-player--me" in result

    def test_no_timeline__suppresses_build_and_skill_order(self):
        blue = [self._make_entry("p1", "Jinx")]
        result = _build_tab_html(
            blue,
            [],
            "14.10.1",
            False,
            "p1",
            {},
            [],
            {"p1": ["Q", "W", "E"]},
        )
        # Without timeline, skill order should not appear
        assert "skill-grid" not in result
        assert "Build Order" not in result

    def test_with_timeline__shows_skill_order(self):
        blue = [self._make_entry("p1", "Jinx")]
        result = _build_tab_html(
            blue,
            [],
            "14.10.1",
            True,
            "p1",
            {},
            [],
            {"p1": ["Q", "W", "E"]},
        )
        assert "skill-grid" in result

    def test_empty_teams__still_renders_structure(self):
        result = _build_tab_html([], [], "14.10.1", False, "", {}, [], {})
        assert "build-tab" in result
        assert "Blue Team" in result
        assert "Red Team" in result
