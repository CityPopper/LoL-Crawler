"""Analyzer service — aggregates per-player stats from parsed match data."""

from __future__ import annotations

import logging
import os
import socket
import uuid

import redis.asyncio as aioredis
from lol_pipeline.config import Config
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


async def _analyze_player(  # noqa: C901
    r: aioredis.Redis,
    cfg: Config,
    worker_id: str,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
) -> None:
    if await r.get("system:halted"):
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

            for (_match_id, score), p in zip(new_matches, participant_data, strict=True):
                if p:
                    stats_key = f"player:stats:{puuid}"
                    pipe = r.pipeline(transaction=True)
                    pipe.hincrby(stats_key, "total_games", 1)
                    pipe.hincrby(stats_key, "total_wins", int(p.get("win", "0")))
                    pipe.hincrby(stats_key, "total_kills", int(p.get("kills", "0")))
                    pipe.hincrby(stats_key, "total_deaths", int(p.get("deaths", "0")))
                    pipe.hincrby(stats_key, "total_assists", int(p.get("assists", "0")))
                    if champ := p.get("champion_name"):
                        pipe.zincrby(f"player:champions:{puuid}", 1, champ)
                    if role := p.get("role"):
                        pipe.zincrby(f"player:roles:{puuid}", 1, role)
                    await pipe.execute()
                # Advance cursor per match so a crash mid-loop doesn't cause re-processing
                await r.set(f"player:stats:cursor:{puuid}", str(score))
                # Refresh lock TTL to prevent expiry during long processing
                await r.pexpire(lock_key, lock_ttl_ms)
            log.info("analyzed", extra={"puuid": puuid, "new_matches": len(new_matches)})
        else:
            log.info("no new matches to process", extra={"puuid": puuid})

        # Always recompute derived stats — recovers from crashes where cursor
        # advanced but derived stats were not updated.
        stats: dict[str, str] = await r.hgetall(f"player:stats:{puuid}")  # type: ignore[misc]
        derived = _derived(stats)
        if derived:
            await r.hset(f"player:stats:{puuid}", mapping=derived)  # type: ignore[misc]

        await clear_priority(r, puuid)

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
