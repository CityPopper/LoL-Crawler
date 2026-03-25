"""Hypothesis property-based (fuzz) tests for RawStore._find_in_lines and _search_bundle_file."""

from __future__ import annotations

import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from lol_pipeline.raw_store import RawStore

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_match_id = st.text(
    alphabet=st.characters(codec="ascii", categories=("L", "N", "P")),
    min_size=1,
    max_size=50,
).filter(lambda s: "\t" not in s and "\n" not in s)

_data_str = st.text(max_size=200).filter(lambda s: "\n" not in s)

# Variant excluding \r for file round-trip tests.  Python's text-mode
# universal-newline translation silently converts \r\n → \n, which
# corrupts payloads containing \r when written to a file and read back.
_data_str_file_safe = st.text(max_size=200).filter(
    lambda s: "\n" not in s and "\r" not in s
)

_random_line = st.text(max_size=200).filter(lambda s: "\n" not in s)


@st.composite
def _lines_with_match(draw: st.DrawFn) -> tuple[list[str], str, str]:
    """Generate a list of lines containing exactly one matching entry.

    Returns (lines, match_id, expected_data).
    """
    match_id = draw(_match_id)
    data = draw(_data_str)
    target_line = f"{match_id}\t{data}"
    # Add some noise lines before and after
    prefix_lines = draw(st.lists(_random_line, min_size=0, max_size=5))
    suffix_lines = draw(st.lists(_random_line, min_size=0, max_size=5))
    all_lines = prefix_lines + [target_line] + suffix_lines
    return all_lines, match_id, data


# ---------------------------------------------------------------------------
# _find_in_lines fuzz tests
# ---------------------------------------------------------------------------


class TestFindInLinesFuzz:
    @given(match_id=_match_id)
    @settings(max_examples=200)
    def test_find_in_lines__empty_list__always_none(self, match_id: str) -> None:
        """Empty line list always returns None."""
        assert RawStore._find_in_lines([], match_id) is None

    @given(lines=st.lists(_random_line, min_size=1, max_size=20), match_id=_match_id)
    @settings(max_examples=200)
    def test_find_in_lines__no_tab__returns_none(
        self, lines: list[str], match_id: str
    ) -> None:
        """Lines without the match_id tab prefix return None."""
        # Filter out any lines that accidentally match
        filtered = [ln for ln in lines if not ln.startswith(match_id + "\t")]
        result = RawStore._find_in_lines(filtered, match_id)
        assert result is None

    @given(
        lines=st.lists(
            st.text(max_size=100).filter(lambda s: "\t" not in s and "\n" not in s),
            min_size=1,
            max_size=20,
        ),
        match_id=_match_id,
    )
    @settings(max_examples=200)
    def test_find_in_lines__lines_without_tab__returns_none(
        self, lines: list[str], match_id: str
    ) -> None:
        """Lines that have no tab separator at all always return None."""
        result = RawStore._find_in_lines(lines, match_id)
        assert result is None

    @given(data=st.data())
    @settings(max_examples=200)
    def test_find_in_lines__matching_line__returns_data(
        self, data: st.DataObject
    ) -> None:
        """A line formatted as 'match_id\\tdata' returns data after the tab."""
        lines, match_id, expected_data = data.draw(_lines_with_match())
        result = RawStore._find_in_lines(lines, match_id)
        assert result == expected_data

    @given(match_id=_match_id, payload=_data_str)
    @settings(max_examples=200)
    def test_find_in_lines__round_trip__write_then_find(
        self, match_id: str, payload: str
    ) -> None:
        """Writing 'match_id\\tdata\\n' then searching for match_id returns data."""
        line = f"{match_id}\t{payload}\n"
        result = RawStore._find_in_lines([line], match_id)
        assert result == payload

    @given(
        match_id=_match_id,
        lines=st.lists(
            st.text(max_size=100).filter(lambda s: "\n" not in s),
            min_size=0,
            max_size=20,
        ),
    )
    @settings(max_examples=200)
    def test_find_in_lines__return_type__always_str_or_none(
        self, match_id: str, lines: list[str]
    ) -> None:
        """Return value is always str or None, never raises."""
        result = RawStore._find_in_lines(lines, match_id)
        assert result is None or isinstance(result, str)

    @given(
        match_id=_match_id,
        prefix_data=_data_str,
        suffix_data=_data_str,
    )
    @settings(max_examples=200)
    def test_find_in_lines__tab_at_different_positions__correct_split(
        self, match_id: str, prefix_data: str, suffix_data: str
    ) -> None:
        """Data containing additional tabs is returned in full (only first tab matters)."""
        data_with_tab = f"{prefix_data}\t{suffix_data}"
        line = f"{match_id}\t{data_with_tab}"
        result = RawStore._find_in_lines([line], match_id)
        assert result == data_with_tab


# ---------------------------------------------------------------------------
# _search_bundle_file fuzz tests
# ---------------------------------------------------------------------------


class TestSearchBundleFileFuzz:
    @given(match_id=_match_id)
    @settings(max_examples=50)
    def test_search_bundle_file__empty_file__returns_none(
        self, match_id: str
    ) -> None:
        """Empty file always returns None."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle = Path(tmp_dir) / "empty.jsonl"
            bundle.write_text("", encoding="utf-8")
            assert RawStore._search_bundle_file(bundle, match_id) is None

    @given(data=st.data())
    @settings(max_examples=50)
    def test_search_bundle_file__matching_entry__returns_data(
        self, data: st.DataObject
    ) -> None:
        """File containing match_id entry returns the data."""
        match_id = data.draw(_match_id)
        payload = data.draw(_data_str_file_safe)
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle = Path(tmp_dir) / "test.jsonl"
            bundle.write_text(f"{match_id}\t{payload}\n", encoding="utf-8")
            result = RawStore._search_bundle_file(bundle, match_id)
            assert result == payload

    @given(
        lines=st.lists(
            st.text(max_size=100).filter(lambda s: "\n" not in s),
            min_size=1,
            max_size=10,
        ),
        match_id=_match_id,
    )
    @settings(max_examples=50)
    def test_search_bundle_file__corrupted_lines__no_crash(
        self, lines: list[str], match_id: str
    ) -> None:
        """Files with arbitrary corrupted lines never crash, return str or None."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle = Path(tmp_dir) / "corrupt.jsonl"
            bundle.write_text("\n".join(lines) + "\n", encoding="utf-8")
            result = RawStore._search_bundle_file(bundle, match_id)
            assert result is None or isinstance(result, str)

    @given(
        noise=st.lists(
            st.text(max_size=80).filter(lambda s: "\n" not in s),
            min_size=0,
            max_size=5,
        ),
        match_id=_match_id,
        payload=_data_str_file_safe,
    )
    @settings(max_examples=50)
    def test_search_bundle_file__mixed_lines__finds_target(
        self, noise: list[str], match_id: str, payload: str
    ) -> None:
        """Target entry is found among noise lines."""
        target = f"{match_id}\t{payload}"
        all_lines = noise + [target]
        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle = Path(tmp_dir) / "mixed.jsonl"
            bundle.write_text("\n".join(all_lines) + "\n", encoding="utf-8")
            result = RawStore._search_bundle_file(bundle, match_id)
            assert result == payload
