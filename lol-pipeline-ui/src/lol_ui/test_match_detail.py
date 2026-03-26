"""Tests for match_detail.py — dead code removal + rendering coverage."""

from __future__ import annotations

import html
import inspect

from lol_ui import match_detail
from lol_ui.match_detail import _render_detail_player


def _make_participant(
    champion_name: str = "Jinx",
    kills: str = "5",
    deaths: str = "2",
    assists: str = "8",
    total_minions_killed: str = "180",
    gold_earned: str = "12500",
    vision_score: str = "22",
    total_damage_dealt_to_champions: str = "25000",
    team_id: str = "100",
    items: str = "3006,3047,0,0,0,0,0",
) -> dict[str, str]:
    return {
        "champion_name": champion_name,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "total_minions_killed": total_minions_killed,
        "gold_earned": gold_earned,
        "vision_score": vision_score,
        "total_damage_dealt_to_champions": total_damage_dealt_to_champions,
        "team_id": team_id,
        "items": items,
    }


def _make_player(
    game_name: str = "TestPlayer",
    tag_line: str = "NA1",
    region: str = "na1",
) -> dict[str, str]:
    return {"game_name": game_name, "tag_line": tag_line, "region": region}


class TestDeadCodeRemoval:
    """_render_build_section should not exist (dead code removed)."""

    def test_render_build_section__removed(self) -> None:
        members = [name for name, _ in inspect.getmembers(match_detail, inspect.isfunction)]
        assert "_render_build_section" not in members

    def test_render_detail_player__still_exists(self) -> None:
        assert hasattr(match_detail, "_render_detail_player")


class TestRenderDetailPlayer:
    """_render_detail_player renders a participant row in match detail."""

    def test_basic_rendering(self) -> None:
        """Renders KDA, CS, vision, gold, and damage for a participant."""
        part = _make_participant()
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 30000, "14.10.1")

        assert "5/2/8" in result
        assert "180 CS" in result
        assert "22 V" in result
        assert "12.5k" in result
        assert "25,000 dmg" in result

    def test_me_highlight(self) -> None:
        """Current player row gets the --me CSS class."""
        part = _make_participant()
        player = _make_player()
        result = _render_detail_player("me", part, player, "me", 30000, "14.10.1")
        assert "match-detail__player--me" in result

    def test_not_me_no_highlight(self) -> None:
        """Other player rows do not get the --me CSS class."""
        part = _make_participant()
        player = _make_player()
        result = _render_detail_player("other", part, player, "me", 30000, "14.10.1")
        assert "match-detail__player--me" not in result

    def test_name_link_with_game_name(self) -> None:
        """Player with game_name and tag_line gets a clickable stats link."""
        part = _make_participant()
        player = _make_player(game_name="Faker", tag_line="KR1")
        result = _render_detail_player("p1", part, player, "other", 30000, "14.10.1")

        assert "/stats?riot_id=" in result
        assert "Faker" in result
        assert "KR1" in result
        assert "<a href=" in result

    def test_name_link_without_game_name(self) -> None:
        """Player without game_name shows truncated puuid, no link."""
        part = _make_participant()
        player: dict[str, str] = {"game_name": "", "tag_line": "", "region": "na1"}
        result = _render_detail_player("abcd1234efgh", part, player, "other", 30000, "14.10.1")

        assert "abcd1234" in result
        assert "<a href=" not in result

    def test_gold_value_error(self) -> None:
        """Non-numeric gold_earned falls back to raw string."""
        part = _make_participant(gold_earned="notanumber")
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 30000, "14.10.1")

        assert "notanumber" in result

    def test_damage_bar_percentage(self) -> None:
        """Damage bar width is capped at 100% and proportional to max_damage."""
        part = _make_participant(total_damage_dealt_to_champions="15000")
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 30000, "14.10.1")

        assert "width:50%" in result

    def test_damage_bar_max_100_percent(self) -> None:
        """Damage at max_damage should result in width:100%."""
        part = _make_participant(total_damage_dealt_to_champions="30000")
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 30000, "14.10.1")

        assert "width:100%" in result

    def test_red_team_damage_fill(self) -> None:
        """team_id=200 gets the red damage fill class."""
        part = _make_participant(team_id="200")
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 30000, "14.10.1")

        assert "match-detail__dmg-fill--red" in result

    def test_blue_team_damage_fill(self) -> None:
        """team_id=100 gets the blue damage fill class."""
        part = _make_participant(team_id="100")
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 30000, "14.10.1")

        assert "match-detail__dmg-fill--blue" in result

    def test_champion_icon_rendered(self) -> None:
        """Champion icon <img> tag rendered when version is provided."""
        part = _make_participant(champion_name="Jinx")
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 30000, "14.10.1")

        assert "Jinx.png" in result
        assert "champion-icon" in result

    def test_no_version_no_icon(self) -> None:
        """No champion icon when version is None."""
        part = _make_participant()
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 30000, None)

        assert "champion-icon" not in result

    def test_localized_champion_name(self) -> None:
        """name_map translates champion display name in title attribute."""
        part = _make_participant(champion_name="MonkeyKing")
        player = _make_player()
        name_map = {"MonkeyKing": "Wukong"}
        result = _render_detail_player(
            "p1", part, player, "other", 30000, "14.10.1", name_map=name_map
        )

        assert html.escape("Wukong") in result

    def test_missing_champion_data(self) -> None:
        """Participant with missing champion_name falls back to '?'."""
        part = _make_participant()
        del part["champion_name"]
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 30000, "14.10.1")

        assert "?" in result

    def test_damage_non_numeric(self) -> None:
        """Non-numeric damage defaults to 0."""
        part = _make_participant(total_damage_dealt_to_champions="bad")
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 30000, "14.10.1")

        assert "width:0%" in result

    def test_zero_max_damage_no_division_error(self) -> None:
        """max_damage=0 does not cause division by zero."""
        part = _make_participant(total_damage_dealt_to_champions="1000")
        player = _make_player()
        result = _render_detail_player("p1", part, player, "other", 0, "14.10.1")

        assert "width:100%" in result
