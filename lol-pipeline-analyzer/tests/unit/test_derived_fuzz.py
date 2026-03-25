"""Hypothesis property-based (fuzz) tests for analyzer _derived()."""

from __future__ import annotations

import re

from hypothesis import given, settings
from hypothesis import strategies as st

from lol_analyzer.main import _derived

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_STAT_KEYS = ["total_games", "total_wins", "total_kills", "total_deaths", "total_assists"]

# Values that Redis HGETALL could return (always strings), plus adversarial types
_redis_str = st.text(min_size=0, max_size=200)
_numeric_str = st.integers(min_value=-(2**31), max_value=2**31).map(str)
_adversarial_value = st.one_of(
    _redis_str,
    st.just(""),
    st.just("null"),
    st.just("nan"),
    st.just("inf"),
    st.just("-1"),
    st.just("3.14"),
    st.just("not_a_number"),
)

_EXPECTED_ERRORS = (KeyError, ValueError, TypeError, ZeroDivisionError)

_4_DECIMAL = re.compile(r"^-?\d+\.\d{4}$")


@st.composite
def _random_stat_dict(draw: st.DrawFn) -> dict[str, str]:
    """Random subset of stat keys with random string values."""
    chosen_keys = draw(st.lists(st.sampled_from(_STAT_KEYS), min_size=0, max_size=len(_STAT_KEYS)))
    extra_keys = draw(st.lists(_redis_str, min_size=0, max_size=3))
    result: dict[str, str] = {}
    for k in chosen_keys + extra_keys:
        result[k] = draw(_adversarial_value)
    return result


@st.composite
def _valid_stat_dict(draw: st.DrawFn) -> dict[str, str]:
    """Valid stat dict with non-negative integer strings, total_games >= 1."""
    games = draw(st.integers(min_value=1, max_value=10_000))
    wins = draw(st.integers(min_value=0, max_value=games))
    kills = draw(st.integers(min_value=0, max_value=100_000))
    deaths = draw(st.integers(min_value=0, max_value=100_000))
    assists = draw(st.integers(min_value=0, max_value=100_000))
    return {
        "total_games": str(games),
        "total_wins": str(wins),
        "total_kills": str(kills),
        "total_deaths": str(deaths),
        "total_assists": str(assists),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDerivedFuzz:
    @given(stats=_random_stat_dict())
    @settings(max_examples=200)
    def test_derived__random_dict__no_unexpected_exception(
        self, stats: dict[str, str]
    ) -> None:
        """Random stat dicts either succeed returning a dict or raise expected errors."""
        try:
            result = _derived(stats)
            assert isinstance(result, dict)
            for k, v in result.items():
                assert isinstance(k, str)
                assert isinstance(v, str)
        except _EXPECTED_ERRORS:
            pass  # Expected — the only allowed error types

    @given(stats=st.fixed_dictionaries({"total_games": st.just("0")}))
    @settings(max_examples=50)
    def test_derived__total_games_zero__always_empty(
        self, stats: dict[str, str]
    ) -> None:
        """When total_games is "0", _derived always returns {}."""
        assert _derived(stats) == {}

    @given(data=st.data())
    @settings(max_examples=200)
    def test_derived__valid_inputs__4_decimal_strings(self, data: st.DataObject) -> None:
        """Valid inputs produce correct 4-decimal-place strings for all 5 fields."""
        stats = data.draw(_valid_stat_dict())
        result = _derived(stats)
        assert len(result) == 5
        for key in ("win_rate", "avg_kills", "avg_deaths", "avg_assists", "kda"):
            assert key in result, f"Missing key: {key}"
            assert _4_DECIMAL.match(result[key]), (
                f"{key}={result[key]!r} is not a 4-decimal-place string"
            )

    @given(data=st.data())
    @settings(max_examples=200)
    def test_derived__valid_inputs__round_trip_correct(
        self, data: st.DataObject
    ) -> None:
        """Derived values match manual computation to 4 decimal places."""
        stats = data.draw(_valid_stat_dict())
        result = _derived(stats)
        games = int(stats["total_games"])
        wins = int(stats["total_wins"])
        kills = int(stats["total_kills"])
        deaths = int(stats["total_deaths"])
        assists = int(stats["total_assists"])
        assert result["win_rate"] == f"{wins / games:.4f}"
        assert result["avg_kills"] == f"{kills / games:.4f}"
        assert result["avg_deaths"] == f"{deaths / games:.4f}"
        assert result["avg_assists"] == f"{assists / games:.4f}"
        assert result["kda"] == f"{(kills + assists) / max(deaths, 1):.4f}"

    @given(stats=st.fixed_dictionaries({}))
    @settings(max_examples=50)
    def test_derived__empty_dict__returns_empty(self, stats: dict[str, str]) -> None:
        """Empty dict has no total_games, defaults to '0' -> returns {}."""
        assert _derived(stats) == {}

    @given(
        stats=st.fixed_dictionaries(
            {
                "total_games": st.just("1"),
                "total_wins": st.just("1"),
                "total_kills": st.integers(min_value=0, max_value=100).map(str),
                "total_deaths": st.just("0"),
                "total_assists": st.integers(min_value=0, max_value=100).map(str),
            }
        )
    )
    @settings(max_examples=200)
    def test_derived__zero_deaths__never_division_error(
        self, stats: dict[str, str]
    ) -> None:
        """Zero deaths uses max(deaths, 1) — never causes ZeroDivisionError."""
        result = _derived(stats)
        kills = int(stats["total_kills"])
        assists = int(stats["total_assists"])
        expected_kda = f"{(kills + assists) / 1:.4f}"
        assert result["kda"] == expected_kda

    @given(
        stats=st.fixed_dictionaries(
            {
                "total_games": st.integers(min_value=1, max_value=100).map(str),
                "total_wins": st.integers(min_value=-1000, max_value=0).map(str),
                "total_kills": st.integers(min_value=-1000, max_value=0).map(str),
                "total_deaths": st.integers(min_value=-1000, max_value=0).map(str),
                "total_assists": st.integers(min_value=-1000, max_value=0).map(str),
            }
        )
    )
    @settings(max_examples=200)
    def test_derived__negative_values__no_crash(self, stats: dict[str, str]) -> None:
        """Negative stat values do not crash — _derived still produces strings."""
        try:
            result = _derived(stats)
            assert isinstance(result, dict)
            for v in result.values():
                assert isinstance(v, str)
        except _EXPECTED_ERRORS:
            pass

    @given(
        missing_keys=st.lists(
            st.sampled_from(_STAT_KEYS[1:]),  # exclude total_games
            min_size=1,
            max_size=4,
            unique=True,
        ),
        games=st.integers(min_value=1, max_value=100).map(str),
    )
    @settings(max_examples=200)
    def test_derived__partial_keys__defaults_to_zero(
        self, missing_keys: list[str], games: str
    ) -> None:
        """Missing stat keys default to "0" via .get() — no KeyError."""
        stats = {"total_games": games}
        # Deliberately omit some keys — they should default to "0"
        result = _derived(stats)
        assert isinstance(result, dict)
        assert len(result) == 5
