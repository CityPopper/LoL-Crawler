"""Test analyzer _derived() uses 4 decimal places for all fields."""

from lol_analyzer.main import _derived


class TestDerivedPrecision:
    def test_all_fields_use_4_decimal_places(self) -> None:
        # Input: known stats where derived values have long decimal expansions
        # Output: all 5 derived fields formatted to exactly 4 decimal places
        stats = {
            "total_games": "3",
            "total_wins": "1",
            "total_kills": "10",
            "total_deaths": "7",
            "total_assists": "5",
        }
        result = _derived(stats)
        # 1/3 = 0.3333...
        assert result["win_rate"] == "0.3333"
        # 10/3 = 3.3333...
        assert result["avg_kills"] == "3.3333"
        # 7/3 = 2.3333...
        assert result["avg_deaths"] == "2.3333"
        # 5/3 = 1.6667 (rounded)
        assert result["avg_assists"] == "1.6667"
        # (10+5)/7 = 2.1429 (rounded)
        assert result["kda"] == "2.1429"

    def test_zero_deaths_kda(self) -> None:
        # Input: 0 deaths -> kda = (kills + assists) / max(0, 1) = kills + assists
        stats = {
            "total_games": "1",
            "total_wins": "1",
            "total_kills": "10",
            "total_deaths": "0",
            "total_assists": "5",
        }
        result = _derived(stats)
        assert result["kda"] == "15.0000"

    def test_zero_games_returns_empty(self) -> None:
        stats = {"total_games": "0"}
        assert _derived(stats) == {}
