"""Parser helpers — validation, participant mapping, and Redis queuing utilities."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.constants import CHAMPION_STATS_TTL_SECONDS

from lol_parser._constants import _PARTICIPANT_FIELD_MAP
from lol_parser._data import _ITEM_KEYS
from lol_parser._extract import _extract_all_perks


def _key_player_matches(puuid: str) -> str:
    """Build Redis key for player:matches:{puuid}."""
    return f"player:matches:{puuid}"


def _key_match_participants(match_id: str) -> str:
    """Build Redis key for match:participants:{match_id}."""
    return f"match:participants:{match_id}"


def _key_player(puuid: str) -> str:
    """Build Redis key for player:{puuid}."""
    return f"player:{puuid}"


def _validate(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract and validate info + metadata; raise KeyError on missing required fields."""
    info: dict[str, Any] = data["info"]
    if "participants" not in info or not info["participants"]:
        raise KeyError("participants")
    if "gameStartTimestamp" not in info:
        raise KeyError("gameStartTimestamp")
    return data["metadata"], info


def _participant_fields(p: dict[str, Any]) -> dict[str, str]:
    """Build the Redis hash mapping for one participant."""
    keystone, primary_id, sub_id, primary_sel, sub_sel, stat_shards = _extract_all_perks(p)
    items = json.dumps([p.get(k, 0) for k in _ITEM_KEYS])
    fields: dict[str, str] = {
        "champion_name": p.get("championName", ""),
        "win": "1" if p.get("win") else "0",
        "items": items,
        "perk_keystone": str(keystone),
        "perk_primary_style": str(primary_id),
        "perk_sub_style": str(sub_id),
        "perk_primary_selections": json.dumps(primary_sel),
        "perk_sub_selections": json.dumps(sub_sel),
        "perk_stat_shards": json.dumps(stat_shards),
    }
    for redis_field, (riot_field, default) in _PARTICIPANT_FIELD_MAP.items():
        fields[redis_field] = str(p.get(riot_field, default))
    return fields


def _queue_participant(
    pipe: aioredis.client.Pipeline,
    match_id: str,
    game_start: int,
    p: dict[str, Any],
    match_data_ttl: int,
) -> str:
    """Queue all Redis commands for one participant onto *pipe* (no execute).

    Returns the participant's puuid.
    """
    puuid: str = p["puuid"]
    if not puuid or not puuid.strip():
        raise ValueError(f"invalid puuid: {puuid!r}")
    participant_key = f"participant:{match_id}:{puuid}"
    pipe.hset(participant_key, mapping=_participant_fields(p))
    pipe.expire(participant_key, match_data_ttl)
    mp_key = _key_match_participants(match_id)
    pipe.sadd(mp_key, puuid)
    pipe.expire(mp_key, match_data_ttl)
    pipe.zadd(_key_player_matches(puuid), {match_id: float(game_start)})
    riot_name = p.get("riotIdGameName", "")
    riot_tag = p.get("riotIdTagline", "")
    if riot_name and riot_tag:
        player_key = _key_player(puuid)
        pipe.hsetnx(player_key, "game_name", riot_name)
        pipe.hsetnx(player_key, "tag_line", riot_tag)
    return puuid


def _queue_pid_json(
    pipe: aioredis.client.Pipeline,
    pid_data: dict[int, list[int]],
    pid_to_puuid: dict[int, str],
    key_prefix: str,
    match_id: str,
    ttl: int,
) -> None:
    """Queue SET commands for per-participant JSON arrays onto a pipeline."""
    for pid, values in pid_data.items():
        puuid = pid_to_puuid.get(pid, "")
        if puuid:
            pipe.set(f"{key_prefix}:{match_id}:{puuid}", json.dumps(values), ex=ttl)


def _warn_non_monotonic_gold(
    gold_timelines: dict[int, list[int]],
    match_id: str,
    log: logging.Logger,
) -> None:
    """Log warning for any participant with non-monotonic totalGold sequence."""
    for pid, golds in gold_timelines.items():
        for i in range(1, len(golds)):
            if golds[i] < golds[i - 1]:
                log.warning(
                    "non-monotonic gold timeline",
                    extra={"match_id": match_id, "participant_id": pid},
                )
                break


def _group_by_team_position(
    participants: list[dict[str, Any]],
) -> dict[int, dict[str, dict[str, Any]]]:
    """Group participants by teamId and teamPosition."""
    team_positions: dict[int, dict[str, dict[str, Any]]] = {}
    for p in participants:
        team_id = p.get("teamId", 0)
        position = p.get("teamPosition", "")
        if not position or not team_id:
            continue
        if team_id not in team_positions:
            team_positions[team_id] = {}
        team_positions[team_id][position] = p
    return team_positions


def _find_shared_positions(
    team_positions: dict[int, dict[str, dict[str, Any]]],
) -> tuple[int, int, set[str]] | None:
    """Return (team_a, team_b, shared_positions) or None if < 2 teams."""
    teams = sorted(team_positions.keys())
    if len(teams) != 2:
        return None
    team_a, team_b = teams[0], teams[1]
    shared = set(team_positions[team_a]) & set(team_positions[team_b])
    if not shared:
        return None
    return team_a, team_b, shared


def _queue_matchup_cmds(
    pipe: aioredis.client.Pipeline,
    team_positions: dict[int, dict[str, dict[str, Any]]],
    team_a: int,
    team_b: int,
    shared_positions: set[str],
    patch: str,
) -> None:
    """Queue HINCRBY / SADD / EXPIRE commands for all matchups onto *pipe*."""
    for position in sorted(shared_positions):
        a = team_positions[team_a][position]
        b = team_positions[team_b][position]
        champ_a = a.get("championName", "")
        champ_b = b.get("championName", "")
        if not champ_a or not champ_b:
            continue
        win_a = 1 if a.get("win") else 0
        win_b = 1 - win_a

        key_ab = f"matchup:{champ_a}:{champ_b}:{position}:{patch}"
        pipe.hincrby(key_ab, "games", 1)
        pipe.hincrby(key_ab, "wins", win_a)
        pipe.expire(key_ab, CHAMPION_STATS_TTL_SECONDS)

        key_ba = f"matchup:{champ_b}:{champ_a}:{position}:{patch}"
        pipe.hincrby(key_ba, "games", 1)
        pipe.hincrby(key_ba, "wins", win_b)
        pipe.expire(key_ba, CHAMPION_STATS_TTL_SECONDS)

        idx_a = f"matchup:index:{champ_a}:{position}:{patch}"
        pipe.sadd(idx_a, champ_b)
        pipe.expire(idx_a, CHAMPION_STATS_TTL_SECONDS)
        idx_b = f"matchup:index:{champ_b}:{position}:{patch}"
        pipe.sadd(idx_b, champ_a)
        pipe.expire(idx_b, CHAMPION_STATS_TTL_SECONDS)
