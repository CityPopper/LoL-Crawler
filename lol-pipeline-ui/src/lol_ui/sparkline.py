"""7-day win rate sparkline — pure CSS stacked bar chart.

Provides:
- ``_bucket_by_day(matches, days)`` -- group matches into (wins, losses) per day
- ``_sparkline_html(matches)`` -- render CSS stacked bars per day
"""

from __future__ import annotations

import time

_DAY_S = 86400


def _bucket_by_day(
    matches: list[dict[str, str]],
    days: int = 7,
) -> list[tuple[int, int]]:
    """Group matches into (wins, losses) per day, oldest first.

    *matches* is a list of participant dicts with ``win`` ("1"/"0") and
    ``game_start`` (milliseconds as string) keys.
    Returns a list of ``(wins, losses)`` tuples, one per day.
    Days without matches are ``(0, 0)``.
    """
    now_s = time.time()
    buckets: list[list[int]] = [[0, 0] for _ in range(days)]

    for m in matches:
        raw_start = m.get("game_start", "")
        if not raw_start:
            continue
        try:
            start_ms = int(raw_start)
        except (ValueError, TypeError):
            continue
        start_s = start_ms / 1000
        age_days = (now_s - start_s) / _DAY_S
        if age_days < 0 or age_days >= days:
            continue
        day_idx = days - 1 - int(age_days)
        is_win = m.get("win") == "1"
        if is_win:
            buckets[day_idx][0] += 1
        else:
            buckets[day_idx][1] += 1

    return [(wins, losses) for wins, losses in buckets]


def _sparkline_html(matches: list[dict[str, str]]) -> str:
    """Render a 7-day win rate sparkline as pure CSS stacked bars.

    Blue bars for wins, red bars for losses. No JavaScript.
    """
    buckets = _bucket_by_day(matches, days=7)
    max_games = max((w + lo for w, lo in buckets), default=0)
    if max_games == 0:
        max_games = 1  # prevent division by zero

    day_htmls: list[str] = []
    for wins, losses in buckets:
        total = wins + losses
        bar_height = round(total / max_games * 100) if total > 0 else 0
        win_pct = round(wins / total * 100) if total > 0 else 0
        loss_pct = 100 - win_pct if total > 0 else 0

        bar_parts: list[str] = []
        if wins > 0:
            bar_parts.append(
                '<div class="sparkline__win"'
                ' style="height:' + str(win_pct) + '%"'
                ' title="' + str(wins) + 'W"'
                "></div>"
            )
        if losses > 0:
            bar_parts.append(
                '<div class="sparkline__loss"'
                ' style="height:' + str(loss_pct) + '%"'
                ' title="' + str(losses) + 'L"'
                "></div>"
            )

        bar_inner = "".join(bar_parts)
        day_htmls.append(
            '<div class="sparkline__day">'
            '<div class="sparkline__bar"'
            ' style="height:' + str(bar_height) + '%">' + bar_inner + "</div></div>"
        )

    inner = "".join(day_htmls)
    return '<div class="sparkline">' + inner + "</div>"
