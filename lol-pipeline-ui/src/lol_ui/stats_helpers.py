"""Stats computation and rendering — breakdowns, tables, diversity."""

from __future__ import annotations

import contextlib
import html
import json
import math
from datetime import UTC, datetime
from typing import Any

from lol_ui._helpers import _kda, _safe_int, _win_rate
from lol_ui.constants import (
    _DIVERSITY_LABELS,
    _DIVERSITY_MIN_GAMES,
    _RANKED_SPLIT_STARTS,
    _STATS_ORDER,
    _STATS_ORDER_SET,
    VALID_ROLES,
)
from lol_ui.rendering import _badge


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
        self.total_kda += _kda(kills, deaths, assists)

    @property
    def win_rate(self) -> float:
        """Win rate as 0-100 percentage."""
        return round(_win_rate(self.wins, self.games), 1)

    @property
    def avg_kda(self) -> float:
        """Average KDA ratio across all recorded matches."""
        return round(self.total_kda / self.games, 2) if self.games else 0.0


_VALID_ROLES = VALID_ROLES


def _compute_breakdown(
    matches: list[dict[str, str]],
    key: str,
    valid_values: frozenset[str] | None = None,
) -> dict[str, _BreakdownEntry]:
    """Group participant dicts by *key* and compute stats.

    When *valid_values* is provided, only values in that set are accepted.
    Otherwise, empty strings are skipped but all non-empty values are kept.
    Returns ``{value: _BreakdownEntry}`` sorted by games desc.
    """
    buckets: dict[str, _BreakdownEntry] = {}
    for m in matches:
        value = m.get(key, "")
        if valid_values is not None:
            if value not in valid_values:
                continue
        elif not value:
            continue
        entry = buckets.get(value)
        if entry is None:
            entry = _BreakdownEntry()
            buckets[value] = entry
        entry.add(
            win=str(m.get("win", "0")) == "1",
            kills=_safe_int(m.get("kills", "0")),
            deaths=_safe_int(m.get("deaths", "0")),
            assists=_safe_int(m.get("assists", "0")),
        )
    return dict(sorted(buckets.items(), key=lambda kv: kv[1].games, reverse=True))


def _compute_champion_breakdown(
    matches: list[dict[str, str]],
) -> dict[str, _BreakdownEntry]:
    """Group participant dicts by champion_name and compute stats."""
    return _compute_breakdown(matches, "champion_name")


def _compute_role_breakdown(
    matches: list[dict[str, str]],
) -> dict[str, _BreakdownEntry]:
    """Group participant dicts by team_position (known roles only)."""
    return _compute_breakdown(matches, "team_position", valid_values=_VALID_ROLES)


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
    preliminary_badge = ""
    if stats.get("source") == "opgg_prefetch":
        match_count = stats.get("total_games", "?")
        preliminary_badge = " " + _badge(
            "warning", f"Preliminary \u2014 {match_count} matches"
        )
    return f"""
<details>
<summary><h3 style="display:inline">Player Stats</h3>{preliminary_badge}</summary>
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


def _breakdown_table_header(label: str, has_breakdown: bool) -> str:
    """Return <th> elements for a breakdown table with the given first-column *label*."""
    base = f'<th scope="col">{html.escape(label)}</th><th scope="col">Games</th>'
    if has_breakdown:
        return base + '<th scope="col">Win%</th><th scope="col">KDA</th>'
    return base


def _champion_table_header(has_breakdown: bool) -> str:
    """Return <th> elements for the champion table."""
    return _breakdown_table_header("Champion", has_breakdown)


def _role_table_header(has_breakdown: bool) -> str:
    """Return <th> elements for the role table."""
    return _breakdown_table_header("Role", has_breakdown)


def _render_breakdown_rows(
    items: list[tuple[str, float]],
    breakdown: dict[str, _BreakdownEntry] | None,
    domain: str | None = None,
) -> str:
    """Render breakdown table rows, with optional win%/KDA columns.

    *domain* is the i18n domain used to translate row names (e.g. ``"role"``).
    When ``None`` the raw name is displayed (champion names need no translation).
    """
    from lol_pipeline.i18n import label as _ilabel

    from lol_ui.language import _current_lang

    lang = _current_lang.get()
    parts: list[str] = []
    for name, n in items:
        display = _ilabel(domain, name, lang) if domain is not None else name
        safe = html.escape(display)
        base = f"<tr><td>{safe}</td><td>{int(n)}</td>"
        if breakdown is not None:
            entry = breakdown.get(name)
            if entry and entry.games:
                base += f"<td>{entry.win_rate:.1f}%</td><td>{entry.avg_kda:.2f}</td>"
            else:
                base += "<td>&mdash;</td><td>&mdash;</td>"
        parts.append(base + "</tr>")
    return "".join(parts)


def _render_champion_rows(
    champs: list[tuple[str, float]],
    breakdown: dict[str, _BreakdownEntry] | None,
) -> str:
    """Render champion table rows, with optional breakdown columns."""
    return _render_breakdown_rows(champs, breakdown)


def _render_role_rows(
    roles: list[tuple[str, float]],
    breakdown: dict[str, _BreakdownEntry] | None,
) -> str:
    """Render role table rows using localized role names."""
    return _render_breakdown_rows(roles, breakdown, domain="role")


# ---------------------------------------------------------------------------
# Type alias used by route-level helpers below and by routes/stats.py
# ---------------------------------------------------------------------------

_TeamEntry = tuple[str, dict[str, str], dict[str, str], list[str]]


# ---------------------------------------------------------------------------
# Helpers extracted from routes/stats.py (REFACTOR-9)
# ---------------------------------------------------------------------------


def _build_participant_list(
    blue_team: list[_TeamEntry],
    red_team: list[_TeamEntry],
) -> list[dict[str, str]]:
    """Flatten blue + red team entries into participant dicts with puuid added."""
    result: list[dict[str, str]] = []
    for p, part, _player, _build in blue_team:
        entry = dict(part)
        entry["puuid"] = p
        result.append(entry)
    for p, part, _player, _build in red_team:
        entry = dict(part)
        entry["puuid"] = p
        result.append(entry)
    return result


def _build_minimap_events(
    kill_events: list[dict[str, object]],
    gold_data: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Map kill events to minimap-ready dicts with killer_team resolved."""
    champ_team: dict[str, str] = {}
    for info in gold_data.values():
        cname = str(info.get("champion_name", ""))
        tid = str(info.get("team_id", "100"))
        if cname:
            champ_team[cname] = tid

    result: list[dict[str, object]] = []
    for event in kill_events:
        killer_name = str(event.get("killer", ""))
        result.append(
            {
                "x": event.get("x", 0),
                "y": event.get("y", 0),
                "t": event.get("t", 0),
                "killer": killer_name,
                "victim": str(event.get("victim", "")),
                "killer_team": champ_team.get(killer_name, "100"),
            }
        )
    return result


def _group_participants(
    sorted_puuids: list[str],
    pipe_results: list[Any],
) -> tuple[list[_TeamEntry], list[_TeamEntry], dict[str, list[str]], int]:
    """Group pipeline results into blue/red teams, skill orders, and max damage."""
    blue_team: list[_TeamEntry] = []
    red_team: list[_TeamEntry] = []
    skill_orders: dict[str, list[str]] = {}
    max_damage = 1
    for i, p in enumerate(sorted_puuids):
        participant_data: dict[str, str] = pipe_results[i * 4]
        player_data: dict[str, str] = pipe_results[i * 4 + 1]
        build_raw: str | None = pipe_results[i * 4 + 2]
        skills_raw: str | None = pipe_results[i * 4 + 3]
        if not participant_data:
            continue
        try:
            dmg = int(participant_data.get("total_damage_dealt_to_champions", "0"))
        except ValueError:
            dmg = 0
        max_damage = max(max_damage, dmg)
        build_order: list[str] = []
        if build_raw:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                build_order = [str(x) for x in json.loads(build_raw)]
        if skills_raw:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                parsed = json.loads(skills_raw)
                if isinstance(parsed, list):
                    skill_orders[p] = [str(x) for x in parsed]
        team_id = participant_data.get("team_id", "")
        entry: _TeamEntry = (p, participant_data, player_data, build_order)
        if team_id == "200":
            red_team.append(entry)
        else:
            blue_team.append(entry)
    return blue_team, red_team, skill_orders, max_damage


def _has_timeline_data(html_content: str) -> bool:
    """Check if the rendered HTML has real content worth caching.

    Returns False for placeholder/error responses that should not be cached:
    - "Match details not available" (empty match:participants)
    - "Timeline data unavailable" (FETCH_TIMELINE=false)
    """
    if "Match details not available" in html_content:
        return False
    return "Timeline data unavailable" not in html_content
