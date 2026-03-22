"""Unit tests for lol_ui._helpers — _safe_int and _safe_float."""

from __future__ import annotations

import pytest

from lol_ui._helpers import _safe_float, _safe_int


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
