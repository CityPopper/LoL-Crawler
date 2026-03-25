"""Player stats service — aggregates per-player stats from parsed match data.

Consumer group: player-stats-workers
Input stream: stream:analyze
Writes: player:stats:{puuid}, player:champions:{puuid}, player:roles:{puuid}
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis
from lol_pipeline._helpers import is_system_halted
from lol_pipeline.config import Config
from lol_pipeline.constants import PLAYER_DATA_TTL_SECONDS
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import ack

_IN_STREAM = "stream:analyze"
_GROUP = "player-stats-workers"

# Atomic lock-release: only delete if we still own it.
_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Atomic stats update + ownership check.
# KEYS[1] = lock_key, KEYS[2] = stats_key, KEYS[3] = cursor_key
# KEYS[4] = champs_key, KEYS[5] = roles_key
# ARGV[1] = worker_id, ARGV[2] = lock_ttl_ms
# ARGV[3] = win, ARGV[4] = kills, ARGV[5] = deaths, ARGV[6] = assists
# ARGV[7] = cursor_score, ARGV[8] = champion_name, ARGV[9] = role
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


def _derived(stats: dict[str, str]) -> dict[str, str]:
    """Compute derived stats (averages, win rate, KDA) from raw totals."""
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


async def _process_matches(
    r: aioredis.Redis,
    puuid: str,
    new_matches: list[tuple[str, float]],
    participant_data: list[dict[str, str]],
    lock_key: str,
    worker_id: str,
    lock_ttl_ms: int,
) -> bool:
    """Process each match atomically via Lua. Returns False if lock lost."""
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
            return False
    return True


async def _set_ttls(r: aioredis.Redis, puuid: str) -> None:
    """Set 30-day TTL on all player stat keys."""
    async with r.pipeline(transaction=False) as pipe:
        for key in (
            f"player:stats:{puuid}",
            f"player:stats:cursor:{puuid}",
            f"player:champions:{puuid}",
            f"player:roles:{puuid}",
        ):
            pipe.expire(key, PLAYER_DATA_TTL_SECONDS)
        await pipe.execute()


async def handle_player_stats(
    r: aioredis.Redis,
    cfg: Config,
    worker_id: str,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
) -> None:
    """Process a single analyze message for player stats aggregation."""
    if await is_system_halted(r):
        log.critical("system halted — skipping message")
        return

    puuid: str = envelope.payload["puuid"]
    lock_key = f"player:stats:lock:{puuid}"
    lock_ttl_ms = cfg.analyzer_lock_ttl_seconds * 1000

    acquired = await r.set(lock_key, worker_id, nx=True, px=lock_ttl_ms)
    if not acquired:
        log.info("lock held — discarding duplicate", extra={"puuid": puuid})
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        return

    try:
        cursor_str: str | None = await r.get(f"player:stats:cursor:{puuid}")
        cursor = float(cursor_str) if cursor_str else 0.0

        new_matches: list[tuple[str, float]] = await r.zrangebyscore(
            f"player:matches:{puuid}", f"({cursor}", "+inf", withscores=True
        )

        if new_matches:
            async with r.pipeline(transaction=False) as pipe:
                for match_id, _score in new_matches:
                    pipe.hgetall(f"participant:{match_id}:{puuid}")
                raw_results: list[dict[str, str]] = await pipe.execute()

            participant_data: list[dict[str, str]] = raw_results

            lock_ok = await _process_matches(
                r,
                puuid,
                new_matches,
                participant_data,
                lock_key,
                worker_id,
                lock_ttl_ms,
            )
            if not lock_ok:
                await ack(r, _IN_STREAM, _GROUP, msg_id)
                return

        # Always recompute derived stats (crash recovery)
        stats: dict[str, str] = await r.hgetall(f"player:stats:{puuid}")  # type: ignore[misc]
        derived = _derived(stats)
        if derived:
            await r.hset(f"player:stats:{puuid}", mapping=derived)  # type: ignore[misc]

        await _set_ttls(r, puuid)

    finally:
        await r.eval(_RELEASE_LOCK_LUA, 1, lock_key, worker_id)  # type: ignore[misc]

    await ack(r, _IN_STREAM, _GROUP, msg_id)
