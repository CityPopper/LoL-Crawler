"""Analyzer service — aggregates per-player stats from parsed match data."""

from __future__ import annotations

import logging
import os
import socket
import uuid

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.helpers import is_system_halted
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import clear_priority
from lol_pipeline.redis_client import get_redis
from lol_pipeline.service import run_consumer
from lol_pipeline.streams import ack

_IN_STREAM = "stream:analyze"
_GROUP = "analyzers"
_30_DAYS = 30 * 24 * 3600  # 2592000 seconds

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

    Uses a single pipeline context manager across all matches to avoid per-match
    pipeline creation overhead.  Each match's commands are executed as a separate
    MULTI/EXEC (via ``pipe.execute()``) so the cursor advances atomically with
    that match's stats, and the lock is refreshed between matches.
    """
    stats_key = f"player:stats:{puuid}"
    cursor_key = f"player:stats:cursor:{puuid}"
    champs_key = f"player:champions:{puuid}"
    roles_key = f"player:roles:{puuid}"
    async with r.pipeline(transaction=True) as pipe:
        for (_match_id, score), p in zip(new_matches, participant_data, strict=True):
            if not p:
                continue
            pipe.hincrby(stats_key, "total_games", 1)
            pipe.hincrby(stats_key, "total_wins", int(p.get("win", "0")))
            pipe.hincrby(stats_key, "total_kills", int(p.get("kills", "0")))
            pipe.hincrby(stats_key, "total_deaths", int(p.get("deaths", "0")))
            pipe.hincrby(stats_key, "total_assists", int(p.get("assists", "0")))
            if champ := p.get("champion_name"):
                pipe.zincrby(champs_key, 1, champ)
            if role := p.get("role"):
                pipe.zincrby(roles_key, 1, role)
            # Cursor in same MULTI/EXEC as stats —
            # atomic: either all commit or none, preventing double-count on crash.
            pipe.set(cursor_key, str(score))
            await pipe.execute()
            # Refresh lock outside MULTI/EXEC via Lua ownership check.
            # If we no longer own the lock, abort to prevent double-counting.
            if not await _refresh_lock(r, lock_key, worker_id, lock_ttl_ms):
                log.warning(
                    "lock ownership lost mid-processing — aborting",
                    extra={"puuid": puuid},
                )
                return False
    return True


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
            # Batch all HGETALL calls into a single pipeline round-trip
            async with r.pipeline(transaction=False) as fetch_pipe:
                for match_id, _score in new_matches:
                    fetch_pipe.hgetall(f"participant:{match_id}:{puuid}")
                participant_data: list[dict[str, str]] = await fetch_pipe.execute()

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
                ttl_pipe.expire(key, _30_DAYS)
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
