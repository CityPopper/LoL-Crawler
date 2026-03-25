"""Recently Played With panel — find frequent co-players across recent matches.

Provides:
- ``_count_co_players(participant_sets, current_puuid)`` -- Counter of co-player PUUIDs
- ``_recently_played_html(r, puuid, match_ids)`` -- HTML panel with top 5 co-players
"""

from __future__ import annotations

import html
from typing import Any

from lol_ui._helpers import _count_co_players
from lol_ui.strings import t

_MAX_MATCHES = 20
_MIN_SHARED_GAMES = 3
_TOP_N = 5


async def _recently_played_html(
    r: Any,
    puuid: str,
    match_ids: list[str],
) -> str:
    """Render a Recently Played With panel showing top 5 co-players.

    Scans at most 20 matches (capped). Pipelines all SMEMBERS calls
    in one round-trip (2 RTTs total: 1 for SMEMBERS, 1 for player data).
    Only shows players with >= 3 shared games.
    Returns empty string when no co-players meet the threshold.
    """
    if not match_ids:
        return ""

    # Cap at 20 matches
    capped_ids = match_ids[:_MAX_MATCHES]

    # RTT 1: Pipeline all SMEMBERS calls
    async with r.pipeline(transaction=False) as pipe:
        for mid in capped_ids:
            pipe.smembers("match:participants:" + mid)
        sets_results: list[set[str]] = await pipe.execute()

    # Count co-players
    participant_sets = [s for s in sets_results if isinstance(s, set)]
    counts = _count_co_players(participant_sets, puuid)

    # Filter by threshold
    qualifying = [(p, c) for p, c in counts.most_common() if c >= _MIN_SHARED_GAMES]
    if not qualifying:
        return ""

    # Limit to top 5
    top = qualifying[:_TOP_N]

    # RTT 2: Pipeline player data lookups
    async with r.pipeline(transaction=False) as pipe:
        for co_puuid, _ in top:
            pipe.hgetall("player:" + co_puuid)
        player_results: list[dict[str, str]] = await pipe.execute()

    # Build HTML rows
    rows: list[str] = []
    for (co_puuid, count), player_data in zip(top, player_results, strict=True):
        if player_data:
            name = html.escape(player_data.get("game_name", co_puuid[:8]))
            tag = html.escape(player_data.get("tag_line", ""))
            display = name + "#" + tag if tag else name
        else:
            display = html.escape(co_puuid[:8] + "...")

        rows.append(
            '<div class="recently-played__row">'
            '<span class="recently-played__name">' + display + "</span>"
            '<span class="recently-played__count">'
            + str(count)
            + " "
            + t("games_shared")
            + "</span>"
            "</div>"
        )

    body = "".join(rows)
    header = t("recently_played_with")
    return (
        '<div class="recently-played">'
        '<div class="recently-played__header">' + header + "</div>" + body + "</div>"
    )
