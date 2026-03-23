"""Tests for rendering.py — _time_ago and _duration_fmt."""

from __future__ import annotations

import time
from unittest.mock import patch

from lol_ui.rendering import _duration_fmt, _time_ago


class TestTimeAgo:
    """_time_ago returns human-readable relative time from epoch ms."""

    def test_zero__returns_empty(self) -> None:
        assert _time_ago(0) == ""

    def test_future_timestamp__returns_just_now(self) -> None:
        future_ms = (int(time.time()) + 3600) * 1000
        assert _time_ago(future_ms) == "just now"

    def test_minutes_ago(self) -> None:
        now = 1700000000
        game_start_ms = (now - 1800) * 1000  # 30 minutes ago
        with patch("lol_ui.rendering.time.time", return_value=float(now)):
            result = _time_ago(game_start_ms)
        assert result == "30m ago"

    def test_hours_ago(self) -> None:
        now = 1700000000
        game_start_ms = (now - 7200) * 1000  # 2 hours ago
        with patch("lol_ui.rendering.time.time", return_value=float(now)):
            result = _time_ago(game_start_ms)
        assert result == "2h ago"

    def test_days_ago(self) -> None:
        now = 1700000000
        game_start_ms = (now - 172800) * 1000  # 2 days ago
        with patch("lol_ui.rendering.time.time", return_value=float(now)):
            result = _time_ago(game_start_ms)
        assert result == "2d ago"

    def test_just_under_one_hour__shows_minutes(self) -> None:
        now = 1700000000
        game_start_ms = (now - 3599) * 1000  # 59m 59s ago
        with patch("lol_ui.rendering.time.time", return_value=float(now)):
            result = _time_ago(game_start_ms)
        assert result == "59m ago"

    def test_exactly_one_hour__shows_hours(self) -> None:
        now = 1700000000
        game_start_ms = (now - 3600) * 1000
        with patch("lol_ui.rendering.time.time", return_value=float(now)):
            result = _time_ago(game_start_ms)
        assert result == "1h ago"

    def test_exactly_one_day__shows_days(self) -> None:
        now = 1700000000
        game_start_ms = (now - 86400) * 1000
        with patch("lol_ui.rendering.time.time", return_value=float(now)):
            result = _time_ago(game_start_ms)
        assert result == "1d ago"

    def test_zero_seconds_ago__shows_zero_minutes(self) -> None:
        now = 1700000000
        game_start_ms = now * 1000
        with patch("lol_ui.rendering.time.time", return_value=float(now)):
            result = _time_ago(game_start_ms)
        assert result == "0m ago"


class TestDurationFmt:
    """_duration_fmt formats game duration as mm:ss."""

    def test_zero__returns_empty(self) -> None:
        assert _duration_fmt(0) == ""

    def test_one_minute(self) -> None:
        assert _duration_fmt(60) == "1:00"

    def test_with_seconds(self) -> None:
        assert _duration_fmt(125) == "2:05"

    def test_long_game(self) -> None:
        assert _duration_fmt(2400) == "40:00"

    def test_short_game(self) -> None:
        assert _duration_fmt(15) == "0:15"

    def test_exact_seconds_padding(self) -> None:
        assert _duration_fmt(61) == "1:01"

    def test_30_minutes(self) -> None:
        assert _duration_fmt(1800) == "30:00"
