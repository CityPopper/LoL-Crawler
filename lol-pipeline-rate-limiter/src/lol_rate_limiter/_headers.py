"""Parse Riot API rate-limit response headers and update Redis buckets.

Ported from lol-pipeline-common/src/lol_pipeline/riot_api.py (_parse_rate_limit_header).
"""

from __future__ import annotations

import logging

_log = logging.getLogger("rate_limiter.headers")

# Default Riot API window durations in seconds
_SHORT_WINDOW_S: int = 1
_LONG_WINDOW_S: int = 120


def parse_rate_limit_header(
    header: str,
    *,
    short_window_s: int | None = None,
    long_window_s: int | None = None,
) -> tuple[int, int] | None:
    """Parse a Riot rate-limit header into (short_value, long_value).

    Works for both ``X-App-Rate-Limit`` (limits) and ``X-App-Rate-Limit-Count``
    (current usage).  Expects the standard Riot format ``"20:1,100:120"`` where
    each entry is ``"value:window_seconds"``.

    Returns None if the header is absent, malformed, or missing either window.
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
            _log.warning(
                "Rate-limit header missing expected windows",
                extra={
                    "header": header,
                    "windows_found": list(by_window.keys()),
                    "expected_short": target_short,
                    "expected_long": target_long,
                },
            )
            return None
        return short, long_
    except ValueError, TypeError:
        _log.warning(
            "Failed to parse rate-limit header",
            extra={"header": header},
        )
        return None
