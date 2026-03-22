"""Stat and DLQ output formatting for admin CLI."""

from __future__ import annotations

import math

from lol_pipeline.models import DLQEnvelope

from lol_admin._helpers import _relative_age

# Priority stat labels for display ordering
_STAT_PRIORITY = ["win_rate", "kda", "total_games"]
_STAT_LABELS: dict[str, str] = {
    "win_rate": "Win Rate",
    "kda": "KDA",
    "total_games": "Total Games",
    "total_kills": "Total Kills",
    "total_wins": "Total Wins",
    "total_deaths": "Total Deaths",
    "total_assists": "Total Assists",
    "kills": "Kills",
    "deaths": "Deaths",
    "assists": "Assists",
    "avg_kills": "Avg Kills",
    "avg_deaths": "Avg Deaths",
    "avg_assists": "Avg Assists",
    "wins": "Wins",
}


def _format_stat_value(key: str, value: str, stats: dict[str, str]) -> str:
    """Format a single stat value for display."""
    formatters: dict[str, str | None] = {
        "win_rate": "percent",
        "kda": "float2",
        "total_games": "int",
    }
    fmt = formatters.get(key)
    if fmt is None:
        return value
    try:
        f = float(value)
        if fmt == "int":
            return str(int(f))
        if not math.isfinite(f):
            return value
        if fmt == "percent":
            return f"{f * 100:.1f}%  ({stats.get('total_games', '?')} games)"
        return f"{f:.2f}"
    except ValueError:
        return value


def _format_stats_output(
    stats: dict[str, str],
    game_name: str,
    tag_line: str,
    puuid: str,
) -> str:
    """Format player stats as a human-readable block with aligned values."""
    rule = "\u2500" * 36
    lines: list[str] = [
        f"Player: {game_name}#{tag_line}  [{puuid[:8]}\u2026]",
        rule,
    ]

    # Priority keys first, then remaining alphabetically
    ordered_keys: list[str] = [k for k in _STAT_PRIORITY if k in stats]
    remaining = sorted(k for k in stats if k not in _STAT_PRIORITY)
    ordered_keys.extend(remaining)

    for key in ordered_keys:
        label = _STAT_LABELS.get(key, key)
        value = _format_stat_value(key, stats[key], stats)
        lines.append(f"  {label:<18}{value}")

    lines.append(rule)
    return "\n".join(lines)


def _format_dlq_table(entries: list[tuple[str, DLQEnvelope]]) -> str:
    """Format DLQ entries as a human-readable table."""
    hdr = (
        f"{'Entry ID':<18}\u2502 {'Stream':<16}\u2502 {'Code':<14}"
        f"\u2502 {'Attempts':>8} \u2502 {'Age':<10}"
    )
    sep = (
        f"{'\u2500' * 18}\u253c{'\u2500' * 17}\u253c{'\u2500' * 15}"
        f"\u253c{'\u2500' * 10}\u253c{'\u2500' * 10}"
    )
    rows: list[str] = [hdr, sep]
    for entry_id, dlq in entries:
        stream = dlq.original_stream[:15]
        age = _relative_age(dlq.enqueued_at)
        attempts = f"{dlq.dlq_attempts} dlq"
        rows.append(
            f"{entry_id:<18}\u2502 {stream:<16}\u2502 {dlq.failure_code:<14}"
            f"\u2502 {attempts:>8} \u2502 {age:<10}"
        )
    return "\n".join(rows)
