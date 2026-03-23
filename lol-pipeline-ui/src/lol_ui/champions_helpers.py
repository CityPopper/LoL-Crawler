"""Champion tier list helpers — PBI tiers, filtering, detail pages, matchups."""

from __future__ import annotations

import html
from urllib.parse import quote as _url_quote

import redis.asyncio as aioredis

from lol_ui._helpers import _safe_int
from lol_ui.constants import (
    _CHAMPION_ROLE_LABELS,
    _DELTA_DISPLAY_THRESHOLD,
    _DELTA_MIN_GAMES,
    _PBI_MIN_GAMES,
    _TIER_COLORS,
    _TIER_PERCENTILE_CUTOFFS,
    _WR_COLOR_HIGH_THRESHOLD,
    _WR_COLOR_MID_THRESHOLD,
)
from lol_ui.rendering import _champion_icon_html, _empty_state


def _patch_delta(
    current_stats: dict[str, object],
    prev_stats: dict[str, object],
) -> float | None:
    """Compute win-rate delta between current and previous patch.

    Returns None when either patch has fewer than DELTA_MIN_GAMES games.
    """
    cur_games = _safe_int(current_stats.get("games"))  # type: ignore[arg-type]
    prev_games = _safe_int(prev_stats.get("games"))  # type: ignore[arg-type]
    if cur_games < _DELTA_MIN_GAMES or prev_games < _DELTA_MIN_GAMES:
        return None
    cur_wr = float(current_stats.get("win_rate", 0.0))  # type: ignore[arg-type]
    prev_wr = float(prev_stats.get("win_rate", 0.0))  # type: ignore[arg-type]
    delta = cur_wr - prev_wr
    if abs(delta) < _DELTA_DISPLAY_THRESHOLD:
        return 0.0
    return delta


def _pbi_tier(
    win_rate: float,
    pick_rate: float,
    ban_rate: float,
) -> tuple[float, str, str]:
    """Compute PBI score, tier letter, and color.

    PBI = (win_rate - 50) * pick_rate / (100 - ban_rate).
    Tier assignment requires ranking across all champions, so this returns
    the raw PBI score; use _assign_tiers() for percentile-based tier letters.
    Returns (pbi_score, "", "") -- tier/color filled by _assign_tiers.
    """
    denominator = 100.0 - ban_rate
    if denominator <= 0:
        denominator = 0.01
    pbi = (win_rate - 50.0) * pick_rate / denominator
    return pbi, "", ""


def _assign_tiers(rows: list[dict[str, object]]) -> None:
    """Assign PBI-based tier letters to rows in-place using percentile rank."""
    scored: list[tuple[int, float]] = []
    for i, row in enumerate(rows):
        games = _safe_int(row.get("games"))  # type: ignore[arg-type]
        if games < _PBI_MIN_GAMES:
            row["tier"] = ""
            row["tier_color"] = ""
            continue
        wr = float(row.get("win_rate", 0.0))  # type: ignore[arg-type]
        pr = float(row.get("pick_rate", 0.0))  # type: ignore[arg-type]
        br = float(row.get("ban_rate", 0.0))  # type: ignore[arg-type]
        pbi, _, _ = _pbi_tier(wr, pr, br)
        row["pbi"] = pbi
        scored.append((i, pbi))

    if not scored:
        return

    scored.sort(key=lambda x: x[1], reverse=True)
    n = len(scored)
    for rank, (idx, _pbi) in enumerate(scored):
        pct = rank / n  # 0.0 = best
        tier, color = "D", _TIER_COLORS["D"]
        for cutoff, tier_letter in _TIER_PERCENTILE_CUTOFFS:
            if pct < cutoff:
                tier, color = tier_letter, _TIER_COLORS[tier_letter]
                break
        rows[idx]["tier"] = tier
        rows[idx]["tier_color"] = color


def _champion_tier_table(
    rows: list[dict[str, object]],
    patch: str,
    version: str | None,
    prev_rows: list[dict[str, object]] | None = None,
) -> str:
    """Render the champion tier list table HTML."""
    if not rows:
        return _empty_state(
            "No champion data for this patch",
            "Try a different patch or role filter.",
        )
    # Build lookup: (name, role) -> prev row for delta computation
    prev_lookup: dict[tuple[str, str], dict[str, object]] = {}
    if prev_rows:
        for pr in prev_rows:
            prev_lookup[(str(pr["name"]), str(pr["role"]))] = pr

    # Assign PBI tiers
    _assign_tiers(rows)

    trs = ""
    for row in rows:
        name = str(row["name"])
        role = str(row["role"])
        games = _safe_int(row.get("games"))  # type: ignore[arg-type]
        win_rate = float(row["win_rate"])  # type: ignore[arg-type]
        pick_rate = float(row["pick_rate"])  # type: ignore[arg-type]
        kda = float(row["kda"])  # type: ignore[arg-type]
        cs = float(row["cs"])  # type: ignore[arg-type]
        ban_rate = float(row.get("ban_rate", 0.0))  # type: ignore[arg-type]
        safe_name = html.escape(name)
        icon = _champion_icon_html(name, version)
        wr_color = (
            "var(--color-win)"
            if win_rate >= _WR_COLOR_HIGH_THRESHOLD
            else (
                "var(--color-warning)"
                if win_rate >= _WR_COLOR_MID_THRESHOLD
                else "var(--color-loss)"
            )
        )
        wr_cell = (
            f'<td style="min-width:120px"><div style="display:flex;align-items:center;'
            f'gap:6px">'
            f'<div style="flex:1;background:var(--color-surface2);border-radius:3px;'
            f'height:5px">'
            f'<div style="background:{wr_color};width:{min(win_rate, 100):.0f}%;'
            f'height:5px;border-radius:3px"></div></div>'
            f'<span style="font-family:var(--font-sans);font-size:var(--font-size-sm);'
            f'color:{wr_color};min-width:42px">'
            f"{win_rate:.1f}%</span></div></td>"
        )
        # Patch-over-patch delta column
        prev_row = prev_lookup.get((name, role))
        delta = _patch_delta(row, prev_row) if prev_row is not None else None
        if delta is not None and delta > 0:
            delta_cell = f'<td style="color:var(--color-win)">&#9650; +{delta:.1f}%</td>'
        elif delta is not None and delta < 0:
            delta_cell = f'<td style="color:var(--color-loss)">&#9660; {delta:.1f}%</td>'
        else:
            delta_cell = '<td style="color:#888">&mdash;</td>'
        # Tier badge column
        tier = str(row.get("tier", ""))
        tier_color = str(row.get("tier_color", ""))
        if tier:
            tier_cell = (
                f'<td><span class="tier-badge" style="display:inline-block;'
                f"padding:2px 8px;border-radius:4px;font-weight:bold;"
                f'color:#fff;background:{tier_color}">{tier}</span></td>'
            )
        else:
            tier_cell = '<td style="color:#888">&mdash;</td>'
        href = (
            f"/champions/{_url_quote(name)}?patch={_url_quote(patch)}&amp;role={_url_quote(role)}"
        )
        trs += (
            f'<tr><td><a href="{href}">{icon}{safe_name}</a></td>'
            f"{tier_cell}"
            f"<td>{html.escape(role)}</td>"
            f"<td>{games}</td>"
            f"{wr_cell}"
            f"{delta_cell}"
            f"<td>{pick_rate:.1f}%</td>"
            f"<td>{ban_rate:.1f}%</td>"
            f"<td>{kda:.2f}</td>"
            f"<td>{cs:.0f}</td></tr>"
        )
    return (
        '<div class="table-scroll">'
        "<table>"
        "<thead><tr>"
        '<th scope="col">Champion</th>'
        '<th scope="col">Tier</th>'
        '<th scope="col">Role</th>'
        '<th scope="col">Games</th>'
        '<th scope="col">Win Rate</th>'
        '<th scope="col">WR Delta</th>'
        '<th scope="col">Pick Rate</th>'
        '<th scope="col">Ban %</th>'
        '<th scope="col">Avg KDA</th>'
        '<th scope="col">Avg CS</th>'
        "</tr></thead>"
        f"<tbody>{trs}</tbody>"
        "</table></div>"
    )


def _champion_filter_html(
    patches: list[str],
    selected_patch: str,
    selected_role: str,
) -> str:
    """Render patch selector and role filter buttons."""
    patch_options = "\n      ".join(
        f'<option value="{html.escape(p)}"'
        f"{' selected' if p == selected_patch else ''}>"
        f"{html.escape(p)}</option>"
        for p in patches
    )
    role_links = []
    for role_key, role_label in _CHAMPION_ROLE_LABELS.items():
        active = ' class="active"' if role_key == selected_role else ""
        href = f"/champions?patch={_url_quote(selected_patch)}&amp;role={_url_quote(role_key)}"
        role_links.append(
            f'<a href="{href}"{active}'
            f' aria-label="Filter by {html.escape(role_label)}">'
            f"{html.escape(role_label)}</a>"
        )
    role_html = "\n  ".join(role_links)
    return f"""<form class="form-inline" method="get" action="/champions">
  <label for="champ-patch">Patch:</label>
  <select id="champ-patch" name="patch">
      {patch_options}
  </select>
  <input type="hidden" name="role" value="{html.escape(selected_role, quote=True)}">
  <button type="submit">Apply</button>
</form>
<div class="sort-controls">
  <span>Role:</span>
  {role_html}
</div>"""


async def _build_champion_rows(
    r: aioredis.Redis,
    patch: str,
    role: str,
    ban_hash: dict[str, str] | None = None,
    name_to_id: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """Fetch champion index and stats for a given patch/role, return row dicts."""
    index_key = f"champion:index:{patch}"
    members: list[tuple[str, float]] = await r.zrevrange(index_key, 0, -1, withscores=True)
    if role:
        members = [(m, s) for m, s in members if m.endswith(f":{role}")]
    if not members:
        return []
    async with r.pipeline(transaction=False) as pipe:
        for member, _score in members:
            name, pos = member.rsplit(":", 1)
            pipe.hgetall(f"champion:stats:{name}:{patch}:{pos}")
        stats_list: list[dict[str, str]] = await pipe.execute()
    # total games for pick rate (divide by 10 participants per game)
    total_all = sum(_safe_int(s.get("games")) for s in stats_list if s) // 10
    if total_all == 0:
        total_all = 1
    total_ban_games = _safe_int((ban_hash or {}).get("_total_games"))
    _name_to_id = name_to_id or {}
    rows: list[dict[str, object]] = []
    for (member, _score), stats in zip(members, stats_list, strict=True):
        if not stats:
            continue
        name, pos = member.rsplit(":", 1)
        games = _safe_int(stats.get("games"))
        wins = _safe_int(stats.get("wins"))
        kills = _safe_int(stats.get("kills"))
        deaths = _safe_int(stats.get("deaths"))
        assists = _safe_int(stats.get("assists"))
        wr = (wins / games * 100) if games > 0 else 0.0
        avg_kda = (kills + assists) / max(deaths, 1) if games > 0 else 0.0
        avg_cs = _safe_int(stats.get("cs")) / max(games, 1)
        pr = (games / total_all * 100) if total_all > 0 else 0.0
        # Ban rate: look up champion numeric ID, then count from ban hash
        champ_id = _name_to_id.get(name, "")
        bans = _safe_int((ban_hash or {}).get(champ_id)) if champ_id else 0
        br = (bans / total_ban_games * 100) if total_ban_games > 0 else 0.0
        rows.append(
            {
                "name": name,
                "role": pos,
                "games": games,
                "win_rate": wr,
                "kda": avg_kda,
                "cs": avg_cs,
                "pick_rate": pr,
                "ban_rate": br,
            }
        )
    rows.sort(key=lambda x: int(x["games"]), reverse=True)  # type: ignore[call-overload]
    return rows


def _champion_detail_html(
    name: str,
    role: str,
    stats: dict[str, str],
    patch_history: list[tuple[str, dict[str, str]]],
    all_roles: list[str],
    version: str | None,
    matchups_html: str = "",
) -> str:
    """Render champion detail page body."""
    safe_name = html.escape(name)
    icon = _champion_icon_html(name, version)
    games = _safe_int(stats.get("games"))
    wins = _safe_int(stats.get("wins"))
    wr = (wins / games * 100) if games > 0 else 0.0
    kills = _safe_int(stats.get("kills"))
    deaths = _safe_int(stats.get("deaths"))
    assists = _safe_int(stats.get("assists"))
    kda = (kills + assists) / max(deaths, 1) if games > 0 else 0.0
    gold = _safe_int(stats.get("gold"))
    cs = _safe_int(stats.get("cs"))
    damage = _safe_int(stats.get("damage"))
    vis = _safe_int(stats.get("vision"))
    # Per-game averages
    g = max(games, 1)
    stat_rows = (
        f"<tr><td>Games</td><td>{games}</td></tr>"
        f"<tr><td>Win Rate</td><td>{wr:.1f}%</td></tr>"
        f"<tr><td>Avg KDA</td><td>{kda:.2f}</td></tr>"
        f"<tr><td>Avg Kills</td><td>{kills / g:.1f}</td></tr>"
        f"<tr><td>Avg Deaths</td><td>{deaths / g:.1f}</td></tr>"
        f"<tr><td>Avg Assists</td><td>{assists / g:.1f}</td></tr>"
        f"<tr><td>Avg CS</td><td>{cs / g:.0f}</td></tr>"
        f"<tr><td>Avg Gold</td><td>{gold / g:.0f}</td></tr>"
        f"<tr><td>Avg Damage</td><td>{damage / g:.0f}</td></tr>"
        f"<tr><td>Avg Vision</td><td>{vis / g:.1f}</td></tr>"
    )
    # Multi-kill stats (optional)
    for mk in ("double_kills", "triple_kills", "quadra_kills", "penta_kills"):
        val = stats.get(mk, "0")
        label = mk.replace("_", " ").title()
        stat_rows += f"<tr><td>{html.escape(label)}</td><td>{val}</td></tr>"
    role_links = " ".join(
        f'<a href="/champions/{_url_quote(name)}?role={_url_quote(rl)}"'
        f' class="btn-sm{" active" if rl == role else ""}">'
        f"{html.escape(rl)}</a>"
        for rl in all_roles
    )
    # Patch history table
    ph_rows = ""
    for ph_patch, ph_stats in patch_history:
        ph_games = _safe_int(ph_stats.get("games"))
        ph_wins = _safe_int(ph_stats.get("wins"))
        ph_wr = (ph_wins / ph_games * 100) if ph_games > 0 else 0.0
        ph_k = _safe_int(ph_stats.get("kills"))
        ph_d = _safe_int(ph_stats.get("deaths"))
        ph_a = _safe_int(ph_stats.get("assists"))
        ph_kda = (ph_k + ph_a) / max(ph_d, 1) if ph_games > 0 else 0.0
        ph_rows += (
            f"<tr><td>{html.escape(ph_patch)}</td>"
            f"<td>{ph_games}</td>"
            f"<td>{ph_wr:.1f}%</td>"
            f"<td>{ph_kda:.2f}</td></tr>"
        )
    patch_table = ""
    if ph_rows:
        patch_table = (
            '<h3>Patch History</h3><div class="table-scroll"><table>'
            "<thead><tr>"
            '<th scope="col">Patch</th>'
            '<th scope="col">Games</th>'
            '<th scope="col">Win Rate</th>'
            '<th scope="col">KDA</th>'
            "</tr></thead>"
            f"<tbody>{ph_rows}</tbody></table></div>"
        )
    return f"""<h2>{icon}{safe_name} &mdash; {html.escape(role)}</h2>
<p>Roles: {role_links}</p>
<div class="table-scroll">
<table>
<thead><tr><th scope="col">Stat</th><th scope="col">Value</th></tr></thead>
<tbody>{stat_rows}</tbody>
</table>
</div>
{patch_table}
{matchups_html}
<p><a href="/champions">&larr; Back to Champions</a></p>"""


def _matchup_table_html(
    matchups: list[tuple[str, int, float]],
) -> str:
    """Render a matchup table from (opponent, games, win_rate) tuples."""
    if not matchups:
        return ""
    trs = ""
    for opponent, games, wr in matchups:
        wr_cls = (
            "success"
            if wr >= _WR_COLOR_HIGH_THRESHOLD
            else ("error" if wr < _WR_COLOR_MID_THRESHOLD else "")
        )
        safe_opp = html.escape(opponent)
        trs += f'<tr><td>{safe_opp}</td><td>{games}</td><td class="{wr_cls}">{wr:.1f}%</td></tr>'
    return (
        '<h3>Matchups</h3><div class="table-scroll"><table>'
        "<thead><tr>"
        '<th scope="col">vs Champion</th>'
        '<th scope="col">Games</th>'
        '<th scope="col">Win Rate</th>'
        "</tr></thead>"
        f"<tbody>{trs}</tbody></table></div>"
    )


async def _fetch_champion_matchups(
    r: aioredis.Redis,
    name: str,
    role: str,
    patch: str,
) -> list[tuple[str, int, float]]:
    """Fetch matchup data for a champion/role/patch from Redis.

    Returns (opponent, games, win_rate) tuples sorted by games descending.
    """
    index_key = f"matchup:index:{name}:{role}:{patch}"
    opponents: set[str] = await r.smembers(index_key)  # type: ignore[misc]
    if not opponents:
        return []
    sorted_opponents = sorted(opponents)
    async with r.pipeline(transaction=False) as pipe:
        for opp in sorted_opponents:
            pipe.hgetall(f"matchup:{name}:{opp}:{role}:{patch}")
        results: list[dict[str, str]] = await pipe.execute()
    matchups: list[tuple[str, int, float]] = []
    for opp, mdata in zip(sorted_opponents, results, strict=True):
        if not mdata:
            continue
        mg = _safe_int(mdata.get("games"))
        mw = _safe_int(mdata.get("wins"))
        mwr = (mw / mg * 100) if mg > 0 else 0.0
        matchups.append((opp, mg, mwr))
    matchups.sort(key=lambda x: x[1], reverse=True)
    return matchups


async def _fetch_patch_history(
    r: aioredis.Redis,
    name: str,
    role: str,
    patch_list: list[str],
) -> list[tuple[str, dict[str, str]]]:
    """Fetch champion stats across last 10 patches."""
    if not patch_list:
        return []
    async with r.pipeline(transaction=False) as pipe:
        for p in patch_list[:10]:
            pipe.hgetall(f"champion:stats:{name}:{p}:{role}")
        patch_stats_list: list[dict[str, str]] = await pipe.execute()
    return [(p, ps) for p, ps in zip(patch_list[:10], patch_stats_list, strict=True) if ps]
