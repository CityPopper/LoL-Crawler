"""Team analysis tab — stat comparison bars between blue and red teams."""

from __future__ import annotations

from lol_ui.strings import t


def _team_stat_bar(blue_val: int, red_val: int, label: str) -> str:
    """Render a single comparison row with a dual-fill percentage bar.

    When both values are zero, shows a 50/50 split.
    """
    total = blue_val + red_val
    blue_pct = 50.0 if total == 0 else round(blue_val / total * 100, 1)
    blue_fmt = f"{blue_val:,}"
    red_fmt = f"{red_val:,}"
    return (
        '<div class="team-analysis__row">'
        '<span class="team-analysis__val team-analysis__val--blue">' + blue_fmt + "</span>"
        '<div class="team-analysis__bar" style="'
        "background:linear-gradient(to right,"
        "var(--color-win) " + str(blue_pct) + "%,"
        "var(--color-loss) " + str(blue_pct) + "%)"
        '">'
        "</div>"
        '<span class="team-analysis__val team-analysis__val--red">' + red_fmt + "</span>"
        '<span class="team-analysis__label">' + label + "</span>"
        "</div>"
    )


def _sum_stat(team: list[dict[str, str]], key: str) -> int:
    """Sum an integer stat across all players in a team."""
    total = 0
    for player in team:
        try:
            total += int(player.get(key, "0"))
        except ValueError:
            continue
    return total


def _team_analysis_html(
    blue_team: list[dict[str, str]],
    red_team: list[dict[str, str]],
    match_data: dict[str, str],
) -> str:
    """Render the full Team Analysis tab with 5-6 stat comparison rows.

    The Objectives row is only shown when team objective fields are present
    in match_data.
    """
    rows = [
        _team_stat_bar(
            _sum_stat(blue_team, "gold_earned"),
            _sum_stat(red_team, "gold_earned"),
            t("gold"),
        ),
        _team_stat_bar(
            _sum_stat(blue_team, "total_damage_dealt_to_champions"),
            _sum_stat(red_team, "total_damage_dealt_to_champions"),
            t("damage"),
        ),
        _team_stat_bar(
            _sum_stat(blue_team, "kills"),
            _sum_stat(red_team, "kills"),
            t("kills"),
        ),
        _team_stat_bar(
            _sum_stat(blue_team, "total_minions_killed"),
            _sum_stat(red_team, "total_minions_killed"),
            t("cs"),
        ),
        _team_stat_bar(
            _sum_stat(blue_team, "vision_score"),
            _sum_stat(red_team, "vision_score"),
            t("vision"),
        ),
    ]

    # Objectives row: only if team objective data is present
    if "team_blue_dragons" in match_data:
        blue_obj = _objectives_total(match_data, "blue")
        red_obj = _objectives_total(match_data, "red")
        rows.append(_team_stat_bar(blue_obj, red_obj, t("objectives")))

    body = "".join(rows)
    return '<div class="team-analysis">' + body + "</div>"


def _objectives_total(match_data: dict[str, str], side: str) -> int:
    """Sum all objective counts for a team side (blue or red)."""
    total = 0
    for obj in ("dragons", "barons", "towers", "heralds"):
        key = "team_" + side + "_" + obj
        try:
            total += int(match_data.get(key, "0"))
        except ValueError:
            continue
    return total
