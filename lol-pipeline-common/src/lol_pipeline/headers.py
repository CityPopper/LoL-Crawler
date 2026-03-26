"""Rate-limit header parsing — shared implementation for all services.

This module provides the canonical ``parse_rate_limit_header`` function.
Both the ``riot_api`` module (in common) and the standalone rate-limiter
service delegate to this implementation.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

# Default Riot API rate-limit window durations (seconds).
_SHORT_WINDOW_S: int = 1
_LONG_WINDOW_S: int = 120


def parse_rate_limit_header(
    header: str,
    field_name: str = "X-App-Rate-Limit",
    *,
    short_window_s: int | None = None,
    long_window_s: int | None = None,
) -> tuple[int, int] | None:
    """Parse a Riot rate-limit header into ``(short_value, long_value)``.

    Works for both ``X-App-Rate-Limit`` (limits) and ``X-App-Rate-Limit-Count``
    (current usage).  Expects the standard Riot format ``"20:1,100:120"`` where
    each entry is ``"value:window_seconds"``.

    Returns ``None`` if the header is absent, malformed, or missing either window.
    """
    if not header:
        return None
    target_short = short_window_s if short_window_s is not None else _SHORT_WINDOW_S
    target_long = long_window_s if long_window_s is not None else _LONG_WINDOW_S
    try:
        by_window: dict[int, int] = {}
        for entry in header.split(","):
            count_str, window_str = entry.strip().split(":")
            by_window[int(window_str)] = int(count_str)
        short = by_window.get(target_short)
        long_ = by_window.get(target_long)
        if short is None or long_ is None:
            if field_name == "X-App-Rate-Limit":
                _log.warning(
                    "%s missing expected windows — using defaults",
                    field_name,
                    extra={
                        "header": header,
                        "windows_found": list(by_window.keys()),
                        "expected_short": target_short,
                        "expected_long": target_long,
                    },
                )
            return None
        return short, long_
    except (ValueError, TypeError):
        if field_name == "X-App-Rate-Limit":
            _log.warning(
                "failed to parse %s header — using defaults",
                field_name,
                extra={"header": header},
            )
        return None
