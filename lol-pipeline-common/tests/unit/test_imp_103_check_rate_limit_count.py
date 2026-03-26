"""IMP-103: Test coverage for _check_rate_limit_count."""

from __future__ import annotations

import logging

from lol_pipeline.riot_api import _check_rate_limit_count


class TestCheckRateLimitCount:
    def test_near_limit_warning_90_percent(self, caplog):
        """At 90%+ usage, a warning is logged."""
        # limits: short=20, long=100
        # count:  short=19 (95%), long=50 (50%)
        with caplog.at_level(logging.WARNING, logger="riot_api"):
            result = _check_rate_limit_count("19:1,50:120", (20, 100))
        # 19/20 = 95% — should trigger short window warning AND throttle
        assert any("short window" in r.message for r in caplog.records)
        assert result is True  # < 5% remaining

    def test_at_limit_100_percent(self, caplog):
        """At 100% usage, warning is logged and throttle hint returned."""
        with caplog.at_level(logging.WARNING, logger="riot_api"):
            result = _check_rate_limit_count("20:1,100:120", (20, 100))
        assert result is True
        # Both windows should have warnings
        assert any("short window" in r.message for r in caplog.records)
        assert any("long window" in r.message for r in caplog.records)

    def test_far_from_limit_no_op(self, caplog):
        """At well under 90%, no warning and no throttle hint."""
        with caplog.at_level(logging.WARNING, logger="riot_api"):
            result = _check_rate_limit_count("5:1,10:120", (20, 100))
        assert result is False
        # No warnings should be logged for riot_api
        riot_warnings = [r for r in caplog.records if r.name == "riot_api"]
        assert len(riot_warnings) == 0

    def test_window_count_mismatch(self):
        """When count header has unexpected windows, returns False."""
        # Limits expect 1s/120s but count header has different windows
        result = _check_rate_limit_count("5:10,10:600", (20, 100))
        assert result is False

    def test_no_limits_returns_false(self):
        """When limits is None, returns False."""
        result = _check_rate_limit_count("19:1,50:120", None)
        assert result is False

    def test_empty_count_header_returns_false(self):
        """When count_header is empty, returns False."""
        result = _check_rate_limit_count("", (20, 100))
        assert result is False

    def test_long_window_near_limit(self, caplog):
        """Long window at 95%+ triggers throttle."""
        with caplog.at_level(logging.WARNING, logger="riot_api"):
            result = _check_rate_limit_count("1:1,96:120", (20, 100))
        assert result is True
        assert any("long window" in r.message for r in caplog.records)

    def test_both_windows_exactly_at_90_percent(self, caplog):
        """At exactly 90%, warning is logged (remaining < 10%)."""
        # short: 18/20 = 90%, remaining = 2 < 2 (10% of 20)
        # long: 90/100 = 90%, remaining = 10 = 10% of 100 — NOT less than
        with caplog.at_level(logging.WARNING, logger="riot_api"):
            result = _check_rate_limit_count("18:1,91:120", (20, 100))
        assert any("short window" in r.message for r in caplog.records)
