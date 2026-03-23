"""Tests for champions_helpers.py — _safe_int usage on Redis values."""

from __future__ import annotations

from lol_ui.champions_helpers import (
    _champion_detail_html,
    _patch_delta,
)


class TestPatchDeltaSafeInt:
    """_patch_delta handles non-numeric Redis values gracefully."""

    def test_non_numeric_games__returns_none(self):
        current = {"games": "not_a_number", "win_rate": 0.55}
        prev = {"games": "10", "win_rate": 0.50}
        result = _patch_delta(current, prev)
        assert result is None

    def test_none_value__returns_none(self):
        current: dict[str, object] = {"win_rate": 0.55}
        prev: dict[str, object] = {"games": "10", "win_rate": 0.50}
        result = _patch_delta(current, prev)
        assert result is None


class TestChampionDetailSafeInt:
    """_champion_detail_html handles non-numeric Redis values."""

    def test_non_numeric_stats__no_crash(self):
        stats = {
            "games": "bad",
            "wins": "bad",
            "kills": "bad",
            "deaths": "bad",
            "assists": "bad",
            "gold": "bad",
            "cs": "bad",
            "damage": "bad",
            "vision": "bad",
        }
        result = _champion_detail_html("Jinx", "BOTTOM", stats, [], ["BOTTOM"], None)
        assert "Jinx" in result
        # All bad values become 0 via _safe_int
        assert "0.0%" in result  # win rate
