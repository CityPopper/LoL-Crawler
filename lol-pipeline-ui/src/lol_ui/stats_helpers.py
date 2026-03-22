"""Stats computation and rendering — breakdowns, tables, diversity."""

from __future__ import annotations

import html
import math
from datetime import UTC, datetime

from lol_ui.constants import (
    _DIVERSITY_LABELS,
    _DIVERSITY_MIN_GAMES,
    _RANKED_SPLIT_STARTS,
    _STATS_ORDER,
    _STATS_ORDER_SET,
)


def _champion_diversity(champ_data: list[tuple[str, float]]) -> tuple[float, str]:
    """Compute champion pool diversity from (champion_name, games_played) tuples.

    Returns (score, label) where score is ``(1 - HHI) * 100``.
    HHI = sum(p_i^2) where p_i = games_on_champ / total_games.
    """
    total = sum(g for _, g in champ_data)
    if total <= 0:
        return 0.0, "OTP"
    hhi = sum((g / total) ** 2 for _, g in champ_data)
    score = (1.0 - hhi) * 100.0
    label = "Flex"
    for threshold, lbl in _DIVERSITY_LABELS:
        if score < threshold:
            label = lbl
            break
    return round(score, 1), label


def _current_split() -> tuple[str, int]:
    """Return (split_label, start_timestamp_ms) for the current ranked split."""
    now = datetime.now(tz=UTC)
    for label, start in reversed(_RANKED_SPLIT_STARTS):
        if now >= start:
            return label, int(start.timestamp() * 1000)
    # Fallback: if before all known splits, use earliest
    label, start = _RANKED_SPLIT_STARTS[0]
    return label, int(start.timestamp() * 1000)


class _BreakdownEntry:
    """Per-champion or per-role aggregated stats."""

    __slots__ = ("games", "total_kda", "wins")

    def __init__(self) -> None:
        self.games: int = 0
        self.wins: int = 0
        self.total_kda: float = 0.0

    def add(self, win: bool, kills: int, deaths: int, assists: int) -> None:
        """Record one match result."""
        self.games += 1
        if win:
            self.wins += 1
        self.total_kda += (kills + assists) / max(deaths, 1)

    @property
    def win_rate(self) -> float:
        """Win rate as 0-100 percentage."""
        return round(self.wins / self.games * 100, 1) if self.games else 0.0

    @property
    def avg_kda(self) -> float:
        """Average KDA ratio across all recorded matches."""
        return round(self.total_kda / self.games, 2) if self.games else 0.0


def _compute_champion_breakdown(
    matches: list[dict[str, str]],
) -> dict[str, _BreakdownEntry]:
    """Group participant dicts by champion_name and compute stats.

    Returns ``{champion_name: _BreakdownEntry}`` sorted by games desc.
    """
    buckets: dict[str, _BreakdownEntry] = {}
    for m in matches:
        champ = m.get("champion_name", "")
        if not champ:
            continue
        entry = buckets.get(champ)
        if entry is None:
            entry = _BreakdownEntry()
            buckets[champ] = entry
        entry.add(
            win=str(m.get("win", "0")) == "1",
            kills=int(m.get("kills", "0")),
            deaths=int(m.get("deaths", "0")),
            assists=int(m.get("assists", "0")),
        )
    return dict(sorted(buckets.items(), key=lambda kv: kv[1].games, reverse=True))


def _compute_role_breakdown(
    matches: list[dict[str, str]],
) -> dict[str, _BreakdownEntry]:
    """Group participant dicts by team_position and compute stats.

    Returns ``{role: _BreakdownEntry}`` sorted by games desc.
    Only includes known roles (TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY).
    """
    valid_roles = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
    buckets: dict[str, _BreakdownEntry] = {}
    for m in matches:
        role = m.get("team_position", "")
        if role not in valid_roles:
            continue
        entry = buckets.get(role)
        if entry is None:
            entry = _BreakdownEntry()
            buckets[role] = entry
        entry.add(
            win=str(m.get("win", "0")) == "1",
            kills=int(m.get("kills", "0")),
            deaths=int(m.get("deaths", "0")),
            assists=int(m.get("assists", "0")),
        )
    return dict(sorted(buckets.items(), key=lambda kv: kv[1].games, reverse=True))


def _format_stat_value(key: str, value: str) -> str:  # noqa: PLR0911
    """Format a stat value for display.

    win_rate is multiplied by 100 and shown as %. Averages and kda rounded to 2dp.
    """
    if key == "win_rate":
        try:
            fval = float(value)
            if not math.isfinite(fval):
                return "N/A"
            return f"{fval * 100:.1f}%"
        except ValueError:
            return value
    if key.startswith("avg_") or key == "kda":
        try:
            fval = float(value)
            if not math.isfinite(fval):
                return "N/A"
            return f"{fval:.2f}"
        except ValueError:
            return value
    return value


def _stats_table(
    stats: dict[str, str],
    champs: list[tuple[str, float]],
    roles: list[tuple[str, float]],
    champ_breakdown: dict[str, _BreakdownEntry] | None = None,
    role_breakdown: dict[str, _BreakdownEntry] | None = None,
    split_label: str = "Current Split",
) -> str:
    ordered = [(k, stats[k]) for k in _STATS_ORDER if k in stats]
    remaining = [(k, v) for k, v in sorted(stats.items()) if k not in _STATS_ORDER_SET]
    rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td>{html.escape(_format_stat_value(k, v))}</td></tr>"
        for k, v in ordered + remaining
    )
    has_bd = champ_breakdown is not None
    champ_rows = _render_champion_rows(champs, champ_breakdown)
    role_rows = _render_role_rows(roles, role_breakdown)
    champ_hdr = _champion_table_header(has_bd)
    role_hdr = _role_table_header(has_bd)
    empty_cols = "4" if has_bd else "2"
    total_champ_games = sum(g for _, g in champs)
    if total_champ_games >= _DIVERSITY_MIN_GAMES:
        div_score, div_label = _champion_diversity(champs)
        diversity_html = (
            f'<div style="margin-top:var(--space-sm);font-size:var(--font-size-sm);'
            f'color:var(--color-muted)">'
            f"Pool Diversity: <strong>{div_score:.1f}</strong> &mdash; {html.escape(div_label)}"
            f"</div>"
        )
    else:
        diversity_html = (
            '<div style="margin-top:var(--space-sm);font-size:var(--font-size-sm);'
            'color:var(--color-muted)">'
            "Pool Diversity: &mdash;</div>"
        )
    return f"""
<details>
<summary><h3 style="display:inline">Player Stats</h3></summary>
<div class="table-scroll">
<table><thead><tr><th scope="col">Stat</th><th scope="col">Value</th></tr></thead>\
<tbody>{rows}</tbody></table>
</div>
</details>
<div class="stats-grid">
<div>
<h3>Top Champions &mdash; {html.escape(split_label)}</h3>
<div class="table-scroll">
<table><thead><tr>{champ_hdr}</tr></thead>
<tbody>{champ_rows or f"<tr><td colspan='{empty_cols}'>No data</td></tr>"}</tbody></table>
</div>
{diversity_html}
</div>
<div>
<h3>Role Performance</h3>
<div class="table-scroll">
<table><thead><tr>{role_hdr}</tr></thead>
<tbody>{role_rows or f"<tr><td colspan='{empty_cols}'>No data</td></tr>"}</tbody></table>
</div>
</div>
</div>
"""


def _champion_table_header(has_breakdown: bool) -> str:
    """Return <th> elements for the champion table."""
    base = '<th scope="col">Champion</th><th scope="col">Games</th>'
    if has_breakdown:
        return base + '<th scope="col">Win%</th><th scope="col">KDA</th>'
    return base


def _role_table_header(has_breakdown: bool) -> str:
    """Return <th> elements for the role table."""
    base = '<th scope="col">Role</th><th scope="col">Games</th>'
    if has_breakdown:
        return base + '<th scope="col">Win%</th><th scope="col">KDA</th>'
    return base


def _render_champion_rows(
    champs: list[tuple[str, float]],
    breakdown: dict[str, _BreakdownEntry] | None,
) -> str:
    """Render champion table rows, with optional breakdown columns."""
    parts: list[str] = []
    for c, n in champs:
        safe = html.escape(c)
        base = f"<tr><td>{safe}</td><td>{int(n)}</td>"
        if breakdown is not None:
            entry = breakdown.get(c)
            if entry and entry.games:
                base += f"<td>{entry.win_rate:.1f}%</td><td>{entry.avg_kda:.2f}</td>"
            else:
                base += "<td>&mdash;</td><td>&mdash;</td>"
        parts.append(base + "</tr>")
    return "".join(parts)


def _render_role_rows(
    roles: list[tuple[str, float]],
    breakdown: dict[str, _BreakdownEntry] | None,
) -> str:
    """Render role table rows, with optional breakdown columns."""
    parts: list[str] = []
    for r_name, n in roles:
        safe = html.escape(r_name)
        base = f"<tr><td>{safe}</td><td>{int(n)}</td>"
        if breakdown is not None:
            entry = breakdown.get(r_name)
            if entry and entry.games:
                base += f"<td>{entry.win_rate:.1f}%</td><td>{entry.avg_kda:.2f}</td>"
            else:
                base += "<td>&mdash;</td><td>&mdash;</td>"
        parts.append(base + "</tr>")
    return "".join(parts)
