"""Unit tests for lol_ui.streams_helpers — group cell rendering."""

from __future__ import annotations

import pytest

from lol_ui.streams_helpers import _format_group_cells


class TestFormatGroupCells:
    """_format_group_cells renders Group / Pending / Lag table cells."""

    def test_no_groups_returns_mdash(self):
        """Empty groups list renders mdash for group name and 0 for pending/lag."""
        result = _format_group_cells([])
        assert "&mdash;" in result
        assert ">0<" in result

    def test_null_lag_returns_zero(self):
        """When lag is None (null from Redis), display '0', not '?'."""
        result = _format_group_cells([{"name": "crawlers", "pending": 0, "lag": None}])
        assert ">0<" in result
        assert ">?<" not in result

    def test_positive_lag_returns_value(self):
        """When lag is a positive integer, display that value."""
        result = _format_group_cells([{"name": "crawlers", "pending": 3, "lag": 5}])
        assert ">5<" in result
