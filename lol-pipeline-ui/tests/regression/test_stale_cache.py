"""Regression: match detail 'not available' must NOT be cached.

If match:participants is empty when first requested, the 'not available'
response was cached for 6 hours. After data populates, users still see
the stale 'not available' until TTL expires.
"""

from __future__ import annotations

from lol_ui.routes.stats import _has_timeline_data


class TestStaleCachePrevention:
    def test_not_available_is_not_cacheable(self) -> None:
        """'Match details not available' must NOT pass the cache check."""
        html = "<p class='warning'>Match details not available</p>"
        assert _has_timeline_data(html) is False, "Stale 'not available' response would be cached"

    def test_full_detail_is_cacheable(self) -> None:
        """Normal match detail with tabs should be cacheable."""
        html = '<div class="match-tabs"><div class="tab-strip">Overview</div></div>'
        assert _has_timeline_data(html) is True

    def test_timeline_placeholder_is_not_cacheable(self) -> None:
        """Timeline placeholder should not be cached."""
        html = "<div>Timeline data unavailable for this match.</div>"
        assert _has_timeline_data(html) is False
