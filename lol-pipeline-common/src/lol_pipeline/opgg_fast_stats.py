"""Op.gg fast-path stats — compute player stats directly from raw op.gg games.

Writes ``player:stats:{puuid}``, ``player:champions:{puuid}``,
``player:roles:{puuid}`` to Redis with a ``source=opgg_prefetch`` marker.
The real pipeline's player-stats service will detect this marker and
clear the fast-path data before recomputing from authoritative match data.
"""

from __future__ import annotations

import logging
from collections import Counter

import redis.asyncio as aioredis

from lol_pipeline.constants import PLAYER_DATA_TTL_SECONDS

_log = logging.getLogger(__name__)


async def compute_opgg_fast_stats(
    r: aioredis.Redis,
    puuid: str,
    raw_games: list[dict[str, object]],
    champion_id_map: dict[str, str],
    ttl_seconds: int = PLAYER_DATA_TTL_SECONDS,
) -> int:
    """Compute and write player stats from raw op.gg game dicts.

    Returns the number of games processed, or 0 if stats already exist.
    """
    if await r.exists(f"player:stats:{puuid}"):
        return 0

    total_games = 0
    total_wins = 0
    total_kills = 0
    total_deaths = 0
    total_assists = 0
    total_cs = 0
    total_duration_sec = 0
    champion_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()

    for game in raw_games:
        participant = _find_participant(game, puuid)
        if participant is None:
            continue

        team_key = participant.get("team_key", "")
        win = _is_team_win(game, team_key)
        stats = participant.get("stats", {})

        kills = int(stats.get("kill", 0))
        deaths = int(stats.get("death", 0))
        assists = int(stats.get("assist", 0))
        cs = int(stats.get("cs", 0))
        champion_id = participant.get("champion_id", 0)
        position = participant.get("position", "")
        game_length = int(game.get("game_length_second", 0))

        total_games += 1
        total_wins += int(win)
        total_kills += kills
        total_deaths += deaths
        total_assists += assists
        total_cs += cs
        total_duration_sec += game_length

        champ_name = champion_id_map.get(str(champion_id), str(champion_id))
        champion_counts[champ_name] += 1
        if position:
            role_counts[position] += 1

    if total_games == 0:
        return 0

    total_duration_min = total_duration_sec / 60.0

    derived = _derive_stats(
        total_games, total_wins, total_kills, total_deaths, total_assists,
        total_cs, total_duration_min,
    )

    stats_mapping: dict[str, str] = {
        "total_games": str(total_games),
        "total_wins": str(total_wins),
        "total_kills": str(total_kills),
        "total_deaths": str(total_deaths),
        "total_assists": str(total_assists),
        "source": "opgg_prefetch",
        **derived,
    }

    stats_key = f"player:stats:{puuid}"
    champs_key = f"player:champions:{puuid}"
    roles_key = f"player:roles:{puuid}"

    async with r.pipeline(transaction=False) as pipe:
        pipe.hset(stats_key, mapping=stats_mapping)  # type: ignore[misc]
        pipe.expire(stats_key, ttl_seconds)
        for champ_name, count in champion_counts.items():
            pipe.zadd(champs_key, {champ_name: count})
        pipe.expire(champs_key, ttl_seconds)
        for role, count in role_counts.items():
            pipe.zadd(roles_key, {role: count})
        pipe.expire(roles_key, ttl_seconds)
        await pipe.execute()

    return total_games


def _find_participant(
    game: dict[str, object], puuid: str
) -> dict[str, object] | None:
    """Find the participant dict matching the target puuid."""
    participants = game.get("participants", [])
    if not isinstance(participants, list):
        return None
    for p in participants:
        summoner = p.get("summoner", {})
        if isinstance(summoner, dict) and summoner.get("puuid") == puuid:
            return p  # type: ignore[return-value]
    return None


def _is_team_win(game: dict[str, object], team_key: str) -> bool:
    """Return whether the given team won the game."""
    teams = game.get("teams", [])
    if not isinstance(teams, list):
        return False
    for team in teams:
        if isinstance(team, dict) and team.get("key") == team_key:
            game_stat = team.get("game_stat", {})
            if isinstance(game_stat, dict):
                return bool(game_stat.get("is_win", False))
    return False


def _derive_stats(
    games: int,
    wins: int,
    kills: int,
    deaths: int,
    assists: int,
    total_cs: int,
    total_duration_min: float,
) -> dict[str, str]:
    """Compute derived stats using the same format as player-stats service (.4f)."""
    return {
        "win_rate": f"{wins / games:.4f}",
        "avg_kills": f"{kills / games:.4f}",
        "avg_deaths": f"{deaths / games:.4f}",
        "avg_assists": f"{assists / games:.4f}",
        "kda": f"{(kills + assists) / max(deaths, 1):.4f}",
        "avg_cs_per_min": (
            f"{total_cs / total_duration_min:.4f}"
            if total_duration_min > 0
            else "0.0000"
        ),
    }
