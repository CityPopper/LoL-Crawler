"""Analyzer service — aggregates per-player stats from parsed match data."""

from __future__ import annotations

import logging
import os
import socket
import uuid

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.constants import CHAMPION_STATS_TTL_SECONDS, PLAYER_DATA_TTL_SECONDS
from lol_pipeline.helpers import is_system_halted
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import clear_priority
from lol_pipeline.redis_client import get_redis
from lol_pipeline.service import run_consumer
from lol_pipeline.streams import ack

_IN_STREAM = "stream:analyze"
_GROUP = "analyzers"
# Atomic lock-release: only delete if we still own it.
_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Atomic lock-refresh: only extend TTL if we still own the lock.
# Returns 1 if refreshed, 0 if ownership lost.
_REFRESH_LOCK_LUA = """
local val = redis.call("GET", KEYS[1])
if val == ARGV[1] then
    return redis.call("PEXPIRE", KEYS[1], ARGV[2])
else
    return 0
end
"""

# V15-1: Atomic stats update + ownership check.
# KEYS[1] = lock_key, KEYS[2] = stats_key, KEYS[3] = cursor_key
# Optional: KEYS[4] = champs_key, KEYS[5] = roles_key
# ARGV[1] = worker_id, ARGV[2] = lock_ttl_ms
# ARGV[3] = win, ARGV[4] = kills, ARGV[5] = deaths, ARGV[6] = assists
# ARGV[7] = cursor_score, ARGV[8] = champion_name (or ""), ARGV[9] = role (or "")
_PROCESS_MATCH_LUA = """
local lock_key  = KEYS[1]
local stats_key = KEYS[2]
local cursor_key = KEYS[3]
local champs_key = KEYS[4]
local roles_key  = KEYS[5]
local worker_id = ARGV[1]
local ttl_ms    = tonumber(ARGV[2])

if redis.call("GET", lock_key) ~= worker_id then
    return 0
end

redis.call("HINCRBY", stats_key, "total_games", 1)
redis.call("HINCRBY", stats_key, "total_wins", tonumber(ARGV[3]))
redis.call("HINCRBY", stats_key, "total_kills", tonumber(ARGV[4]))
redis.call("HINCRBY", stats_key, "total_deaths", tonumber(ARGV[5]))
redis.call("HINCRBY", stats_key, "total_assists", tonumber(ARGV[6]))

if ARGV[8] ~= "" then
    redis.call("ZINCRBY", champs_key, 1, ARGV[8])
end
if ARGV[9] ~= "" then
    redis.call("ZINCRBY", roles_key, 1, ARGV[9])
end

redis.call("SET", cursor_key, ARGV[7])
redis.call("PEXPIRE", lock_key, ttl_ms)
return 1
"""


# Champion aggregate stats: atomic HINCRBY + index update + patch list.
# KEYS[1] = champion:stats:{name}:{patch}:{role}
# KEYS[2] = champion:index:{patch}
# KEYS[3] = patch:list
# ARGV[1] = win (0/1), ARGV[2] = kills, ARGV[3] = deaths, ARGV[4] = assists
# ARGV[5] = gold, ARGV[6] = cs, ARGV[7] = damage, ARGV[8] = vision
# ARGV[9] = champion_name:role (index member)
# ARGV[10] = game_start_epoch (patch:list score)
# ARGV[11] = patch_string
# ARGV[12] = ttl_seconds
# ARGV[13] = double_kills, ARGV[14] = triple_kills
# ARGV[15] = quadra_kills, ARGV[16] = penta_kills
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


def _derived(stats: dict[str, str]) -> dict[str, str]:
    games = int(stats.get("total_games", "0"))
    if games == 0:
        return {}
    wins = int(stats.get("total_wins", "0"))
    kills = int(stats.get("total_kills", "0"))
    deaths = int(stats.get("total_deaths", "0"))
    assists = int(stats.get("total_assists", "0"))
    return {
        "win_rate": f"{wins / games:.4f}",
        "avg_kills": f"{kills / games:.4f}",
        "avg_deaths": f"{deaths / games:.4f}",
        "avg_assists": f"{assists / games:.4f}",
        "kda": f"{(kills + assists) / max(deaths, 1):.4f}",
    }


async def _safe_clear_priority(r: aioredis.Redis, puuid: str, log: logging.Logger) -> None:
    """Clear priority, logging but swallowing errors so ack() is never skipped."""
    try:
        await clear_priority(r, puuid)
    except Exception:
        log.exception("clear_priority failed for %s — priority may be stale", puuid)


async def _refresh_lock(r: aioredis.Redis, lock_key: str, worker_id: str, ttl_ms: int) -> bool:
    """Refresh lock TTL only if we still own it. Returns True if refreshed."""
    result = await r.eval(  # type: ignore[misc]
        _REFRESH_LOCK_LUA,
        1,
        lock_key,
        worker_id,
        ttl_ms,
    )
    return bool(result)


async def _process_matches(  # noqa: PLR0913
    r: aioredis.Redis,
    puuid: str,
    new_matches: list[tuple[str, float]],
    participant_data: list[dict[str, str]],
    lock_key: str,
    worker_id: str,
    lock_ttl_ms: int,
    log: logging.Logger,
) -> bool:
    """Process each match atomically and refresh lock. Returns False if lock lost.

    V15-1: Uses a Lua script to combine HINCRBY + cursor SET + ownership check
    + lock refresh into a single atomic operation.  This eliminates the race
    window where stats could commit before lock ownership is re-verified.
    """
    stats_key = f"player:stats:{puuid}"
    cursor_key = f"player:stats:cursor:{puuid}"
    champs_key = f"player:champions:{puuid}"
    roles_key = f"player:roles:{puuid}"
    for (_match_id, score), p in zip(new_matches, participant_data, strict=True):
        if not p:
            continue
        result = await r.eval(  # type: ignore[misc]
            _PROCESS_MATCH_LUA,
            5,
            lock_key,
            stats_key,
            cursor_key,
            champs_key,
            roles_key,
            worker_id,
            lock_ttl_ms,
            int(p.get("win", "0")),
            int(p.get("kills", "0")),
            int(p.get("deaths", "0")),
            int(p.get("assists", "0")),
            str(score),
            p.get("champion_name", ""),
            p.get("team_position", ""),
        )
        if not result:
            log.warning(
                "lock ownership lost mid-processing — aborting",
                extra={"puuid": puuid},
            )
            return False
    return True


async def _update_champion_stats(
    r: aioredis.Redis,
    new_matches: list[tuple[str, float]],
    participant_data: list[dict[str, str]],
    match_metadata: list[dict[str, str]],
) -> None:
    """Update per-champion aggregate stats for ranked matches.

    Uses a Redis pipeline to batch all independent EVAL calls into a single
    round-trip. Each Lua script operates on different champion stats keys using
    commutative operations (HINCRBY, ZINCRBY, ZADD NX), so order is irrelevant.
    """
    # Build the list of EVAL args to pipeline, filtering out skipped matches.
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
        if queue_id != "420" or not patch or not team_position or not champion_name:
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
        await pipe.execute()


async def _analyze_player(
    r: aioredis.Redis,
    cfg: Config,
    worker_id: str,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
) -> None:
    if await is_system_halted(r):
        log.critical("system halted — skipping message")
        return

    puuid: str = envelope.payload["puuid"]
    log.info("analyzing player", extra={"puuid": puuid})
    lock_key = f"player:stats:lock:{puuid}"
    lock_ttl_ms = cfg.analyzer_lock_ttl_seconds * 1000

    acquired = await r.set(lock_key, worker_id, nx=True, px=lock_ttl_ms)
    if not acquired:
        log.info("lock held by another worker — discarding duplicate", extra={"puuid": puuid})
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        return

    try:
        cursor_str: str | None = await r.get(f"player:stats:cursor:{puuid}")
        cursor = float(cursor_str) if cursor_str else 0.0

        new_matches: list[tuple[str, float]] = await r.zrangebyscore(
            f"player:matches:{puuid}", f"({cursor}", "+inf", withscores=True
        )

        log.debug(
            "cursor check",
            extra={"puuid": puuid, "cursor": cursor, "new_matches": len(new_matches)},
        )
        if new_matches:
            # Batch all HGETALL calls into a single pipeline round-trip.
            # Fetch both participant data and match metadata (interleaved).
            async with r.pipeline(transaction=False) as fetch_pipe:
                for match_id, _score in new_matches:
                    fetch_pipe.hgetall(f"participant:{match_id}:{puuid}")
                    fetch_pipe.hgetall(f"match:{match_id}")
                raw_results: list[dict[str, str]] = await fetch_pipe.execute()

            # De-interleave: even indices = participant, odd indices = match metadata
            participant_data: list[dict[str, str]] = raw_results[0::2]
            match_metadata: list[dict[str, str]] = raw_results[1::2]

            lock_ok = await _process_matches(
                r,
                puuid,
                new_matches,
                participant_data,
                lock_key,
                worker_id,
                lock_ttl_ms,
                log,
            )
            if not lock_ok:
                await ack(r, _IN_STREAM, _GROUP, msg_id)
                return

            await _update_champion_stats(r, new_matches, participant_data, match_metadata)
            log.info("analyzed", extra={"puuid": puuid, "new_matches": len(new_matches)})
        else:
            log.info("no new matches to process", extra={"puuid": puuid})

        # Always recompute derived stats — recovers from crashes where cursor
        # advanced but derived stats were not updated.
        stats: dict[str, str] = await r.hgetall(f"player:stats:{puuid}")  # type: ignore[misc]
        derived = _derived(stats)
        if derived:
            await r.hset(f"player:stats:{puuid}", mapping=derived)  # type: ignore[misc]

        # P10-DB-1: Set 30-day TTL on all player stat keys to prevent unbounded
        # growth for inactive players. Active players get TTL refreshed each analysis.
        # P14-OPT-1: Batch all EXPIRE calls into a single pipeline round-trip.
        async with r.pipeline(transaction=False) as ttl_pipe:
            for key in (
                f"player:stats:{puuid}",
                f"player:stats:cursor:{puuid}",
                f"player:champions:{puuid}",
                f"player:roles:{puuid}",
            ):
                ttl_pipe.expire(key, PLAYER_DATA_TTL_SECONDS)
            await ttl_pipe.execute()

        await _safe_clear_priority(r, puuid, log)

    finally:
        result = await r.eval(_RELEASE_LOCK_LUA, 1, lock_key, worker_id)  # type: ignore[misc]
        if not result:
            log.warning("lock expired before release", extra={"puuid": puuid})

    await ack(r, _IN_STREAM, _GROUP, msg_id)


async def main() -> None:
    """Analyzer worker loop."""
    log = get_logger("analyzer")
    cfg = Config()
    r = get_redis(cfg.redis_url)
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    async def _handler(msg_id: str, envelope: MessageEnvelope) -> None:
        await _analyze_player(r, cfg, worker_id, msg_id, envelope, log)

    log.info("analyzer started", extra={"worker_id": worker_id})
    try:
        autoclaim_ms = cfg.stream_ack_timeout * 1000
        await run_consumer(
            r,
            _IN_STREAM,
            _GROUP,
            worker_id,
            _handler,
            log,
            autoclaim_min_idle_ms=autoclaim_ms,
        )
    finally:
        await r.aclose()
