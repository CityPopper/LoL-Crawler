"""Hypothesis property-based (fuzz) tests for parser _validate() and _normalize_patch()."""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from lol_parser.main import _normalize_patch, _validate

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=50),
)

_json_values = st.recursive(
    _json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=20), children, max_size=5),
    ),
    max_leaves=20,
)

_random_dict = st.dictionaries(st.text(max_size=30), _json_values, max_size=10)

_version_str = st.one_of(
    st.text(max_size=100),
    st.just(""),
    st.just("13.24.1"),
    st.just("14.1"),
    st.just("14"),
    st.just("..."),
    st.just("."),
    st.just("a.b.c"),
    st.from_regex(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}", fullmatch=True),
)

_adversarial_str = st.one_of(
    st.text(max_size=200),
    st.just(""),
    st.just("\x00"),
    st.just("\n\r\t"),
    st.binary(max_size=50).map(lambda b: b.decode("utf-8", errors="replace")),
    st.text(
        alphabet=st.characters(categories=("L", "N", "P", "S", "Z", "C")),
        max_size=200,
    ),
)


@st.composite
def _valid_match_data(draw: st.DrawFn) -> dict[str, Any]:
    """Generate a valid match data dict that _validate will accept."""
    participants = draw(
        st.lists(
            st.dictionaries(st.text(max_size=20), _json_primitives, max_size=5),
            min_size=1,
            max_size=10,
        )
    )
    game_start = draw(st.integers(min_value=0, max_value=2**53))
    info: dict[str, Any] = {
        "participants": participants,
        "gameStartTimestamp": game_start,
    }
    # Add extra random fields
    extra = draw(st.dictionaries(st.text(max_size=20), _json_primitives, max_size=3))
    info.update(extra)
    metadata = draw(st.dictionaries(st.text(max_size=20), _json_primitives, max_size=5))
    return {"info": info, "metadata": metadata}


# ---------------------------------------------------------------------------
# _validate fuzz tests
# ---------------------------------------------------------------------------


class TestValidateFuzz:
    @given(data=_random_dict)
    @settings(max_examples=200)
    def test_validate__random_dict__only_raises_key_error(
        self, data: dict[str, Any]
    ) -> None:
        """_validate with random dicts only raises KeyError — no other exception types."""
        try:
            result = _validate(data)
            metadata, info = result
            assert isinstance(metadata, dict)
            assert isinstance(info, dict)
        except KeyError:
            pass  # Expected — the only allowed error type for _validate

    @given(
        data=st.fixed_dictionaries(
            {
                "metadata": _random_dict,
                # info is missing entirely
            }
        )
    )
    @settings(max_examples=50)
    def test_validate__missing_info_key__raises_key_error(
        self, data: dict[str, Any]
    ) -> None:
        """Missing 'info' key always raises KeyError."""
        try:
            _validate(data)
            raise AssertionError("Should have raised KeyError for missing 'info'")
        except KeyError as exc:
            assert "info" in str(exc)

    @given(
        metadata=_random_dict,
    )
    @settings(max_examples=50)
    def test_validate__empty_participants__raises_key_error(
        self, metadata: dict[str, Any]
    ) -> None:
        """Empty participants list always raises KeyError."""
        data = {"info": {"participants": [], "gameStartTimestamp": 0}, "metadata": metadata}
        try:
            _validate(data)
            raise AssertionError("Should have raised KeyError for empty participants")
        except KeyError as exc:
            assert "participants" in str(exc)

    @given(metadata=_random_dict)
    @settings(max_examples=50)
    def test_validate__missing_participants__raises_key_error(
        self, metadata: dict[str, Any]
    ) -> None:
        """Missing 'participants' key raises KeyError."""
        data = {"info": {"gameStartTimestamp": 0}, "metadata": metadata}
        try:
            _validate(data)
            raise AssertionError("Should have raised KeyError for missing participants")
        except KeyError as exc:
            assert "participants" in str(exc)

    @given(metadata=_random_dict)
    @settings(max_examples=50)
    def test_validate__missing_game_start_timestamp__raises_key_error(
        self, metadata: dict[str, Any]
    ) -> None:
        """Missing 'gameStartTimestamp' raises KeyError."""
        data = {"info": {"participants": [{"puuid": "test"}]}, "metadata": metadata}
        try:
            _validate(data)
            raise AssertionError("Should have raised KeyError for missing gameStartTimestamp")
        except KeyError as exc:
            assert "gameStartTimestamp" in str(exc)

    @given(data=_valid_match_data())
    @settings(max_examples=200)
    def test_validate__valid_structure__returns_metadata_and_info(
        self, data: dict[str, Any]
    ) -> None:
        """Valid match data returns (metadata, info) tuple."""
        metadata, info = _validate(data)
        assert metadata == data["metadata"]
        assert info == data["info"]
        assert "participants" in info
        assert len(info["participants"]) > 0
        assert "gameStartTimestamp" in info


# ---------------------------------------------------------------------------
# _normalize_patch fuzz tests
# ---------------------------------------------------------------------------


class TestNormalizePatchFuzz:
    @given(version=_version_str)
    @settings(max_examples=200)
    def test_normalize_patch__random_strings__always_returns_str(
        self, version: str
    ) -> None:
        """_normalize_patch always returns a string, never raises."""
        result = _normalize_patch(version)
        assert isinstance(result, str)

    @given(
        major=st.integers(min_value=0, max_value=99).map(str),
        minor=st.integers(min_value=0, max_value=99).map(str),
        patch_num=st.integers(min_value=0, max_value=99).map(str),
    )
    @settings(max_examples=200)
    def test_normalize_patch__dotted_version__returns_major_minor(
        self, major: str, minor: str, patch_num: str
    ) -> None:
        """Versions with 2+ dots produce 'major.minor' format."""
        version = f"{major}.{minor}.{patch_num}"
        result = _normalize_patch(version)
        assert result == f"{major}.{minor}"

    def test_normalize_patch__empty_string__returns_empty(self) -> None:
        """Empty string returns empty string."""
        assert _normalize_patch("") == ""

    @given(version=st.text(max_size=100).filter(lambda s: "." not in s))
    @settings(max_examples=200)
    def test_normalize_patch__no_dots__returns_original(self, version: str) -> None:
        """Strings without dots are returned unchanged."""
        assert _normalize_patch(version) == version

    @given(version=_adversarial_str)
    @settings(max_examples=200)
    def test_normalize_patch__adversarial_inputs__never_crashes(
        self, version: str
    ) -> None:
        """Adversarial inputs (unicode, null bytes, very long) never crash."""
        result = _normalize_patch(version)
        assert isinstance(result, str)

    @given(
        major=st.text(max_size=20),
        minor=st.text(max_size=20),
    )
    @settings(max_examples=200)
    def test_normalize_patch__two_parts__returns_both(
        self, major: str, minor: str
    ) -> None:
        """Version with exactly one dot returns 'part0.part1'."""
        # Only test cases where neither part contains a dot
        from hypothesis import assume

        assume("." not in major and "." not in minor)
        version = f"{major}.{minor}"
        result = _normalize_patch(version)
        assert result == f"{major}.{minor}"

    @given(version=st.just("..."))
    @settings(max_examples=10)
    def test_normalize_patch__only_dots__returns_empty_parts(self, version: str) -> None:
        """A string of only dots splits into empty parts — returns '.'."""
        result = _normalize_patch(version)
        # "...".split(".") -> ["", "", "", ""] — parts[0] + "." + parts[1] = "."
        assert result == "."
