"""Unit tests for lol_ui._helpers — _safe_int, _safe_float, _parse_item_ids."""

from __future__ import annotations

import pytest

from lol_ui._helpers import _parse_item_ids, _safe_float, _safe_int


class TestSafeInt:
    def test_safe_int__valid_string(self) -> None:
        assert _safe_int("42") == 42

    def test_safe_int__zero_string(self) -> None:
        assert _safe_int("0") == 0

    def test_safe_int__negative_string(self) -> None:
        assert _safe_int("-7") == -7

    def test_safe_int__empty_string__returns_default(self) -> None:
        assert _safe_int("") == 0

    def test_safe_int__none__returns_default(self) -> None:
        assert _safe_int(None) == 0

    def test_safe_int__non_numeric__returns_default(self) -> None:
        assert _safe_int("abc") == 0

    def test_safe_int__float_string__returns_default(self) -> None:
        """'3.14' is not a valid int literal — returns default."""
        assert _safe_int("3.14") == 0

    def test_safe_int__custom_default(self) -> None:
        assert _safe_int("bad", default=-1) == -1

    def test_safe_int__whitespace__returns_default(self) -> None:
        assert _safe_int("  ") == 0


class TestSafeFloat:
    def test_safe_float__valid_string(self) -> None:
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_safe_float__integer_string(self) -> None:
        assert _safe_float("42") == 42.0

    def test_safe_float__zero_string(self) -> None:
        assert _safe_float("0") == 0.0

    def test_safe_float__negative_string(self) -> None:
        assert _safe_float("-2.5") == pytest.approx(-2.5)

    def test_safe_float__empty_string__returns_default(self) -> None:
        assert _safe_float("") == 0.0

    def test_safe_float__none__returns_default(self) -> None:
        assert _safe_float(None) == 0.0

    def test_safe_float__non_numeric__returns_default(self) -> None:
        assert _safe_float("xyz") == 0.0

    def test_safe_float__custom_default(self) -> None:
        assert _safe_float("bad", default=-1.0) == -1.0

    def test_safe_float__whitespace__returns_default(self) -> None:
        assert _safe_float("  ") == 0.0


class TestParseItemIds:
    """_parse_item_ids parses item IDs from participant hash, padded to slot count."""

    def test_json_array__returns_string_ids(self) -> None:
        participant = {"items": "[3006,3047,0,3111,0,0,3340]"}
        result = _parse_item_ids(participant)
        assert result == ["3006", "3047", "0", "3111", "0", "0", "3340"]

    def test_comma_separated__returns_string_ids(self) -> None:
        participant = {"items": "3006,3047,0,3111,0,0,3340"}
        result = _parse_item_ids(participant)
        assert result == ["3006", "3047", "0", "3111", "0", "0", "3340"]

    def test_empty_string__returns_empty_first_then_padded(self) -> None:
        """Empty string splits to [''], so first slot is '' not '0'."""
        participant = {"items": ""}
        result = _parse_item_ids(participant)
        assert len(result) == 7
        assert result[0] == ""
        assert result[1:] == ["0"] * 6

    def test_missing_key__returns_empty_first_then_padded(self) -> None:
        """Missing 'items' key defaults to '', same as empty string."""
        result = _parse_item_ids({})
        assert len(result) == 7
        assert result[0] == ""
        assert result[1:] == ["0"] * 6

    def test_fewer_items__pads_to_slots(self) -> None:
        participant = {"items": "[3006,3047]"}
        result = _parse_item_ids(participant)
        assert len(result) == 7
        assert result[:2] == ["3006", "3047"]
        assert result[2:] == ["0"] * 5

    def test_more_items__truncates_to_slots(self) -> None:
        participant = {"items": "[1,2,3,4,5,6,7,8,9]"}
        result = _parse_item_ids(participant)
        assert len(result) == 7
        assert result == ["1", "2", "3", "4", "5", "6", "7"]

    def test_custom_slots(self) -> None:
        participant = {"items": "[1,2,3]"}
        result = _parse_item_ids(participant, slots=5)
        assert len(result) == 5
        assert result == ["1", "2", "3", "0", "0"]

    def test_malformed_json__returns_padded_zeros(self) -> None:
        participant = {"items": "[invalid json"}
        result = _parse_item_ids(participant)
        assert result == ["0"] * 7

    def test_single_item_json__pads(self) -> None:
        participant = {"items": "[3006]"}
        result = _parse_item_ids(participant)
        assert len(result) == 7
        assert result[0] == "3006"
        assert result[1:] == ["0"] * 6

    def test_single_item_comma__pads(self) -> None:
        participant = {"items": "3006"}
        result = _parse_item_ids(participant)
        assert len(result) == 7
        assert result[0] == "3006"
