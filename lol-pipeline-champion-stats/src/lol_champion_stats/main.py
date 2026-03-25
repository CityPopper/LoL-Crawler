"""Champion stats service — aggregates per-champion stats from parsed match data.

Consumer group: champion-stats-workers
Input stream: stream:analyze
Writes: champion:stats:{champion}:{patch}:{role}, champion:builds, champion:runes,
        matchup hashes, champion:index:{patch}, patch:list
"""

from __future__ import annotations

import json
import logging

import redis.asyncio as aioredis
from lol_pipeline._helpers import is_system_halted
from lol_pipeline.config import Config
from lol_pipeline.constants import CHAMPION_STATS_TTL_SECONDS, RANKED_SOLO_QUEUE_ID
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import ack

_IN_STREAM = "stream:analyze"
_GROUP = "champion-stats-workers"

# Atomic champion stats update: HINCRBY + index + patch list.
_UPDATE_CHAMPION_LUA = """
local stats_key = KEYS[1]
local index_key = KEYS[2]
local patch_key = KEYS[3]
local ttl = tonumber(ARGV[12])

redis.call("HINCRBY", stats_key, "games", 1)
redis.call("HINCRBY", stats_key, "wins", tonumber(ARGV[1]))
redis.call("HINCRBY", stats_key, "kills", tonumber(ARGV[2]))
redis.call("HINCRBY", stats_key, "deaths", tonumber(ARGV[3]))
redis.call("HINCRBY", stats_key, "assists", tonumber(ARGV[4]))
redis.call("HINCRBY", stats_key, "gold", tonumber(ARGV[5]))
redis.call("HINCRBY", stats_key, "cs", tonumber(ARGV[6]))
redis.call("HINCRBY", stats_key, "damage", tonumber(ARGV[7]))
redis.call("HINCRBY", stats_key, "vision", tonumber(ARGV[8]))
redis.call("HINCRBY", stats_key, "double_kills", tonumber(ARGV[13]))
redis.call("HINCRBY", stats_key, "triple_kills", tonumber(ARGV[14]))
redis.call("HINCRBY", stats_key, "quadra_kills", tonumber(ARGV[15]))
redis.call("HINCRBY", stats_key, "penta_kills", tonumber(ARGV[16]))
redis.call("EXPIRE", stats_key, ttl)
redis.call("ZINCRBY", index_key, 1, ARGV[9])
redis.call("EXPIRE", index_key, ttl)
redis.call("ZADD", patch_key, "NX", tonumber(ARGV[10]), ARGV[11])
redis.call("EXPIRE", patch_key, ttl)
return 1
"""


async def _update_champion_stats(
    r: aioredis.Redis,
    new_matches: list[tuple[str, float]],
    participant_data: list[dict[str, str]],
    match_metadata: list[dict[str, str]],
) -> None:
    """Update per-champion aggregate stats for ranked matches."""
    calls: list[tuple[str, str, str, dict[str, str], int]] = []
    for (_match_id, score), p, meta in zip(
        new_matches, participant_data, match_metadata, strict=True
    ):
        if not p or not meta:
            continue
        queue_id = meta.get("queue_id", "")
        patch = meta.get("patch", "")
        team_position = p.get("team_position", "")
        champion_name = p.get("champion_name", "")
        if queue_id != RANKED_SOLO_QUEUE_ID or not patch or not team_position or not champion_name:
            continue
        calls.append((champion_name, patch, team_position, p, int(score)))

    if not calls:
        return

    async with r.pipeline(transaction=False) as pipe:
        for champion_name, patch, team_position, p, score_int in calls:
            stats_key = f"champion:stats:{champion_name}:{patch}:{team_position}"
            index_key = f"champion:index:{patch}"
            index_member = f"{champion_name}:{team_position}"

            pipe.eval(
                _UPDATE_CHAMPION_LUA,
                3,
                stats_key,
                index_key,
                "patch:list",
                int(p.get("win", "0")),
                int(p.get("kills", "0")),
                int(p.get("deaths", "0")),
                int(p.get("assists", "0")),
                int(p.get("gold_earned", "0")),
                int(p.get("total_minions_killed", "0")),
                int(p.get("total_damage_dealt_to_champions", "0")),
                int(p.get("vision_score", "0")),
                index_member,
                str(score_int),
                patch,
                CHAMPION_STATS_TTL_SECONDS,
                int(p.get("double_kills", "0")),
                int(p.get("triple_kills", "0")),
                int(p.get("quadra_kills", "0")),
                int(p.get("penta_kills", "0")),
            )

            # Item build fingerprint
            _aggregate_builds(pipe, p, champion_name, patch, team_position)

            # Keystone rune
            _aggregate_runes(pipe, p, champion_name, patch, team_position)

        await pipe.execute()


def _aggregate_builds(
    pipe: aioredis.client.Pipeline,
    p: dict[str, str],
    champion: str,
    patch: str,
    position: str,
) -> None:
    """Add item build fingerprint to builds sorted set."""
    builds_key = f"champion:builds:{champion}:{patch}:{position}"
    raw_items = p.get("items", "[]")
    try:
        item_list: list[int] = json.loads(raw_items)
        non_zero = sorted(i for i in item_list if isinstance(i, int) and i > 0)
        if non_zero:
            build_fp = ",".join(str(i) for i in non_zero)
            pipe.zincrby(builds_key, 1, build_fp)
            pipe.expire(builds_key, CHAMPION_STATS_TTL_SECONDS)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass


def _aggregate_runes(
    pipe: aioredis.client.Pipeline,
    p: dict[str, str],
    champion: str,
    patch: str,
    position: str,
) -> None:
    """Add keystone rune to runes sorted set."""
    runes_key = f"champion:runes:{champion}:{patch}:{position}"
    keystone = p.get("perk_keystone", "0")
    if keystone and keystone != "0":
        pipe.zincrby(runes_key, 1, keystone)
        pipe.expire(runes_key, CHAMPION_STATS_TTL_SECONDS)


async def _update_matchups(
    r: aioredis.Redis,
    puuid: str,
    new_matches: list[tuple[str, float]],
    participant_data: list[dict[str, str]],
    match_metadata: list[dict[str, str]],
) -> None:
    """Update head-to-head matchup stats for ranked matches with opponent data."""
    for (match_id, _score), p, meta in zip(
        new_matches, participant_data, match_metadata, strict=True
    ):
        if not p or not meta:
            continue
        queue_id = meta.get("queue_id", "")
        patch = meta.get("patch", "")
        team_position = p.get("team_position", "")
        champion_name = p.get("champion_name", "")
        if queue_id != RANKED_SOLO_QUEUE_ID or not patch or not team_position or not champion_name:
            continue

        opponent: dict[str, str] = await r.hgetall(f"opponent:{puuid}:{match_id}")  # type: ignore[misc]
        if not opponent:
            continue

        opp_champ = opponent.get("champion_name", "")
        opp_position = opponent.get("team_position", "")
        if not opp_champ or opp_position != team_position:
            continue

        # Alphabetical ordering for consistent key
        champ_a, champ_b = sorted([champion_name, opp_champ])
        matchup_key = f"matchup:{champ_a}:{champ_b}:{team_position}:{patch}"

        win = p.get("win", "0") == "1"
        async with r.pipeline(transaction=False) as pipe:
            pipe.hincrby(matchup_key, "games", 1)
            if win:
                pipe.hincrby(matchup_key, f"{champion_name}_wins", 1)
            pipe.expire(matchup_key, CHAMPION_STATS_TTL_SECONDS)
            await pipe.execute()


async def handle_champion_stats(
    r: aioredis.Redis,
    cfg: Config,
    worker_id: str,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
) -> None:
    """Process a single analyze message for champion stats aggregation."""
    if await is_system_halted(r):
        log.critical("system halted — skipping message")
        return

    puuid: str = envelope.payload["puuid"]

    # Fetch all matches for this player (no cursor — champion stats are commutative)
    all_matches: list[tuple[str, float]] = await r.zrangebyscore(
        f"player:matches:{puuid}", "-inf", "+inf", withscores=True
    )

    if all_matches:
        # Batch fetch participant + match metadata
        async with r.pipeline(transaction=False) as pipe:
            for match_id, _score in all_matches:
                pipe.hgetall(f"participant:{match_id}:{puuid}")
                pipe.hgetall(f"match:{match_id}")
            raw_results: list[dict[str, str]] = await pipe.execute()

        participant_data: list[dict[str, str]] = raw_results[0::2]
        match_metadata: list[dict[str, str]] = raw_results[1::2]

        await _update_champion_stats(r, all_matches, participant_data, match_metadata)
        await _update_matchups(r, puuid, all_matches, participant_data, match_metadata)

    await ack(r, _IN_STREAM, _GROUP, msg_id)
