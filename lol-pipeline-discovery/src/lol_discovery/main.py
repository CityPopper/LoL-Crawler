"""Discovery service — promotes discovered players to stream:puuid when pipeline is idle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import priority_count
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import AuthError, NotFoundError, RiotAPIError, RiotClient
from lol_pipeline.streams import publish
from redis.exceptions import RedisError, ResponseError

_STREAM_PUUID = "stream:puuid"
_DISCOVER_KEY = "discover:players"
_PIPELINE_STREAMS = (
    "stream:puuid",
    "stream:match_id",
    "stream:parse",
    "stream:analyze",
)
_MAX_STREAM_BACKLOG = int(os.getenv("MAX_STREAM_BACKLOG", "500"))


def _parse_member(member: str) -> tuple[str, str]:
    """Split 'puuid:region' member into (puuid, region). Region has no colons."""
    idx = member.rfind(":")
    if idx == -1:
        return member, "na1"
    puuid, region = member[:idx], member[idx + 1 :]
    if not puuid:
        return member, "na1"
    return puuid, region


async def _is_idle(r: aioredis.Redis) -> bool:
    """Return True when ALL pipeline streams are drained (no pending or lagging messages).

    Checks stream:puuid, stream:match_id, stream:parse, and stream:analyze.

    Two-layer check:
    1. XLEN — if any stream has more than MAX_STREAM_BACKLOG messages, the
       pipeline is backlogged regardless of consumer group state.
    2. XINFO GROUPS — 'pending' (delivered but not yet ACKed) and 'lag'
       (not yet delivered) — both zero across all groups on all streams means
       the pipeline has caught up.

    Also returns False when priority players are in-flight (system:priority_count > 0)
    to avoid promoting discovery players that would compete with seeded players.

    Streams that do not exist yet (ResponseError) or have no consumer groups
    are treated as idle.
    """
    count = await priority_count(r)
    if count > 0:
        return False
    for stream in _PIPELINE_STREAMS:
        # Layer 1: absolute backlog check via XLEN
        stream_len: int = await r.xlen(stream)
        if stream_len > _MAX_STREAM_BACKLOG:
            return False
        # Layer 2: consumer group pending/lag check
        try:
            groups: list[Any] = await r.xinfo_groups(stream)
        except ResponseError:
            continue  # stream does not exist yet — idle for this stream
        if not groups:
            continue  # no consumer groups registered — idle for this stream
        if not all(int(g.get("pending", 0)) == 0 and int(g.get("lag", 0)) == 0 for g in groups):
            return False
    return True


async def _resolve_names(
    r: aioredis.Redis,
    riot: RiotClient,
    puuid: str,
    region: str,
    log: logging.Logger,
) -> tuple[str, str] | None:
    """Return (game_name, tag_line) from Redis backfill or Riot API.

    Returns None when the player permanently cannot be resolved (404).
    Raises RiotAPIError on transient failures so the caller can retry.
    """
    game_name, tag_line = await r.hmget(f"player:{puuid}", ["game_name", "tag_line"])  # type: ignore[misc]
    if game_name and tag_line:
        return game_name, tag_line

    try:
        account = await riot.get_account_by_puuid(puuid, region)
    except NotFoundError:
        log.warning("account not found by puuid", extra={"puuid": puuid})
        return None

    game_name = account.get("gameName") or ""
    tag_line = account.get("tagLine") or ""
    if not game_name or not tag_line:
        log.warning(
            "account missing gameName/tagLine (deleted/banned?)",
            extra={"puuid": puuid},
        )
        return None
    return str(game_name), str(tag_line)


async def _promote_batch(
    r: aioredis.Redis,
    cfg: Config,
    log: logging.Logger,
    riot: RiotClient,
) -> int:
    """Promote up to discovery_batch_size players from discover:players to stream:puuid."""
    if await r.get("system:halted"):
        return 0
    # ZREVRANGE: highest score first (newest game_start = most recent activity)
    members: list[Any] = await r.zrevrange(_DISCOVER_KEY, 0, cfg.discovery_batch_size - 1)
    if not members:
        return 0

    promoted = 0
    for member in members:
        puuid, region = _parse_member(str(member))

        # Skip if player was seeded after being added to discover:players
        seeded: bool = await r.hexists(f"player:{puuid}", "seeded_at")  # type: ignore[misc]
        if seeded:
            await r.zrem(_DISCOVER_KEY, member)
            continue

        try:
            names = await _resolve_names(r, riot, puuid, region, log)
        except AuthError:
            log.error("auth error (403) — halting system", extra={"puuid": puuid})
            await r.set("system:halted", "1")
            break
        except RiotAPIError:
            log.error("transient api error — will retry", extra={"puuid": puuid})
            continue  # leave in queue for next batch
        if names is None:
            await r.zrem(_DISCOVER_KEY, member)
            continue
        game_name, tag_line = names

        envelope = MessageEnvelope(
            source_stream=_STREAM_PUUID,
            type="puuid",
            payload={
                "puuid": puuid,
                "game_name": game_name,
                "tag_line": tag_line,
                "region": region,
            },
            max_attempts=cfg.max_attempts,
        )
        # I2-H12: Atomic promotion ordering — at-least-once safe.
        # 1. XADD to stream:puuid FIRST.  If we crash here the player stays
        #    in discover:players and will be re-promoted on next batch (safe:
        #    downstream consumers handle duplicate PUUIDs idempotently).
        # 2. HSET seeded_at + ZREM from discover:players in a single MULTI/EXEC
        #    pipeline.  If we crash after XADD but before the pipeline, the
        #    player is re-promoted next cycle — at-least-once, never lost.
        await publish(r, _STREAM_PUUID, envelope)
        now_iso = datetime.now(tz=UTC).isoformat()
        async with r.pipeline(transaction=True) as pipe:
            await pipe.hset(  # type: ignore[misc]
                f"player:{puuid}",
                mapping={
                    "game_name": game_name,
                    "tag_line": tag_line,
                    "region": region,
                    "seeded_at": now_iso,
                },
            )
            await pipe.zadd("players:all", {puuid: time.time()})
            await pipe.zrem(_DISCOVER_KEY, member)
            await pipe.execute()
        promoted += 1

    if promoted:
        log.info("promoted discovered players", extra={"count": promoted})
    return promoted


async def main() -> None:
    """Discovery loop — runs only when all pipeline streams are idle."""
    shutdown_event = asyncio.Event()

    log = get_logger("discovery")
    cfg = Config()
    r = get_redis(cfg.redis_url)
    riot = RiotClient(cfg.riot_api_key, r=r)

    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, OSError):
        loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)

    interval_s = cfg.discovery_poll_interval_ms / 1000
    log.info(
        "discovery started",
        extra={
            "poll_interval_ms": cfg.discovery_poll_interval_ms,
            "batch_size": cfg.discovery_batch_size,
        },
    )
    polls_since_log = 0
    heartbeat_polls = int(60 / interval_s)  # log roughly every 60s
    try:
        while not shutdown_event.is_set():
            try:
                if await r.get("system:halted"):
                    log.critical("system halted — exiting")
                    break
                idle = await _is_idle(r)
                if idle:
                    promoted = await _promote_batch(r, cfg, log, riot)
                    if promoted == 0:
                        polls_since_log += 1
                else:
                    polls_since_log += 1
                if polls_since_log >= heartbeat_polls:
                    queue_size: int = await r.zcard(_DISCOVER_KEY)
                    log.debug(
                        "heartbeat",
                        extra={"idle": idle, "queue": queue_size},
                    )
                    polls_since_log = 0
            except (RedisError, OSError):
                log.exception("Redis error — retrying in 1s")
                await asyncio.sleep(1)
                continue
            await asyncio.sleep(interval_s)
        log.info("SIGTERM received — shutting down gracefully")
    finally:
        await riot.close()
        await r.aclose()
