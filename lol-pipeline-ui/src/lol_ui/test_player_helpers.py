"""Tests for player_helpers.py — sort and render."""

from __future__ import annotations

from lol_ui.player_helpers import _apply_player_sort, _rank_sort_key


class TestRankSortKey:
    """_rank_sort_key returns a tuple for rank ordering (lower = better)."""

    def test_diamond__before_gold(self) -> None:
        diamond = {"tier": "DIAMOND", "division": "IV", "lp": "10"}
        gold = {"tier": "GOLD", "division": "I", "lp": "99"}
        assert _rank_sort_key(diamond) < _rank_sort_key(gold)

    def test_same_tier__lower_division_first(self) -> None:
        gold1 = {"tier": "GOLD", "division": "I", "lp": "0"}
        gold4 = {"tier": "GOLD", "division": "IV", "lp": "99"}
        assert _rank_sort_key(gold1) < _rank_sort_key(gold4)

    def test_same_tier_and_division__higher_lp_first(self) -> None:
        high_lp = {"tier": "GOLD", "division": "II", "lp": "75"}
        low_lp = {"tier": "GOLD", "division": "II", "lp": "25"}
        assert _rank_sort_key(high_lp) < _rank_sort_key(low_lp)

    def test_unranked__sorts_last(self) -> None:
        ranked = {"tier": "IRON", "division": "IV", "lp": "0"}
        unranked: dict[str, str] = {}
        assert _rank_sort_key(ranked) < _rank_sort_key(unranked)

    def test_challenger__before_master(self) -> None:
        challenger = {"tier": "CHALLENGER", "division": "I", "lp": "500"}
        master = {"tier": "MASTER", "division": "I", "lp": "200"}
        assert _rank_sort_key(challenger) < _rank_sort_key(master)


class TestApplyPlayerSortRank:
    """_apply_player_sort with sort='rank' reorders by rank data."""

    def test_rank_sort__orders_by_rank_key(self) -> None:
        rows = [
            ("Alice", "NA1", "na1", "2024-01-01", {"tier": "SILVER", "division": "I", "lp": "50"}),
            ("Bob", "EUW", "euw1", "2024-01-02", {"tier": "DIAMOND", "division": "IV", "lp": "10"}),
        ]
        _apply_player_sort(rows, "rank")
        assert rows[0][0] == "Bob"  # Diamond before Silver
        assert rows[1][0] == "Alice"
