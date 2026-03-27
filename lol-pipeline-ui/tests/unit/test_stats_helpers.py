"""Tests for stats_helpers — preliminary badge behaviour."""

from __future__ import annotations

from lol_ui.stats_helpers import _stats_table


class TestStatsPreliminaryBadge:
    """Badge rendering when stats come from opgg_prefetch."""

    def test_stats_table__opgg_prefetch__shows_badge(self) -> None:
        stats = {"source": "opgg_prefetch", "total_games": "18"}
        html = _stats_table(stats=stats, champs=[], roles=[])
        assert "Preliminary" in html
        assert "18 matches" in html

    def test_stats_table__no_source__no_badge(self) -> None:
        html = _stats_table(stats={}, champs=[], roles=[])
        assert "Preliminary" not in html

    def test_stats_table__opgg_prefetch_55_games__still_shows_badge(self) -> None:
        stats = {"source": "opgg_prefetch", "total_games": "55"}
        html = _stats_table(stats=stats, champs=[], roles=[])
        assert "Preliminary" in html
