"""Discovery service — promotes discovered players to stream:puuid when pipeline is idle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys
import time
import uuid
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline._helpers import is_system_halted, register_player
from lol_pipeline.config import Config
from lol_pipeline.constants import (
    DELAYED_MESSAGES_KEY,
    PLAYER_DATA_TTL_SECONDS,
    PLAYERS_ALL_KEY,
    STREAM_PUUID,
    SYSTEM_HALTED_KEY,
)
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import PRIORITY_AUTO_20, has_priority_players
from lol_pipeline.rate_limiter_client import try_token
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import AuthError, NotFoundError, RiotAPIError, RiotClient
from lol_pipeline.streams import publish
from pydantic import ValidationError
from redis.exceptions import RedisError

from lol_discovery._constants import (
    _DISCOVER_KEY,
)
from lol_discovery._constants import (
    _PIPELINE_STREAMS as _PIPELINE_STREAMS,
)
from lol_discovery._helpers import (
    _parse_member as _parse_member,
)
from lol_discovery._helpers import (
    _should_skip_seeded,
    _xinfo_groups_safe,
)

_THROTTLED: tuple[str, str] = ("__throttled__", "__throttled__")


async def _is_idle(r: aioredis.Redis) -> bool:
    """Return True when ALL pipeline streams are drained (no pending or lagging messages).

    Checks stream:puuid, stream:match_id, stream:parse, stream:analyze, and
    stream:dlq.  Also checks delayed:messages (ZSET) cardinality to ensure
    no delayed retries are waiting to be re-dispatched.

    Uses XINFO GROUPS pending/lag: both zero across all groups on all streams
    means the pipeline has caught up. Streams that do not exist yet (ResponseError)
    or have no consumer groups are treated as idle.

    Also returns False when priority players are in-flight (any player:priority:*
    key exists) to avoid promoting discovery players that would compete with
    seeded players.  Uses SCAN-based detection instead of a counter to avoid
    TTL-expiry drift.

    All checks (priority, XINFO GROUPS per stream, ZCARD) run concurrently
    via asyncio.gather to reduce sequential RTTs.
    """
    # Fire all checks concurrently: priority + XINFO per stream + ZCARD
    results = await asyncio.gather(
        has_priority_players(r),
        *[_xinfo_groups_safe(r, stream) for stream in _PIPELINE_STREAMS],
        r.zcard(DELAYED_MESSAGES_KEY),
    )
    # results[0] = has_priority_players
    # results[1..N] = XINFO GROUPS per stream (None = stream missing)
    # results[-1] = delayed:messages ZCARD
    has_priority: bool = results[0]
    if has_priority:
        return False

    xinfo_results: list[list[Any] | None] = results[1:-1]
    for groups in xinfo_results:
        if groups is None or not groups:
            continue  # stream missing or no consumer groups — idle
        if not all(int(g.get("pending") or 0) == 0 and int(g.get("lag") or 0) == 0 for g in groups):
            return False

    # Check delayed:messages ZSET — non-empty means retries are queued
    delayed_count: int = results[-1]
    return delayed_count == 0


async def _resolve_names(
    r: aioredis.Redis,
    riot: RiotClient,
    puuid: str,
    region: str,
    log: logging.Logger,
) -> tuple[str, str] | None:
    """Return (game_name, tag_line) from Redis backfill or Riot API.

    Returns None when the player permanently cannot be resolved (404).
    Returns ``_THROTTLED`` when the rate-limiter denies a token so the
    caller can skip this member without removing it from the queue.
    Raises RiotAPIError on transient failures so the caller can retry.
    """
    game_name, tag_line = await r.hmget(f"player:{puuid}", ["game_name", "tag_line"])  # type: ignore[misc]
    if game_name and tag_line:
        return game_name, tag_line

    if not await try_token("riot", "account"):
        log.debug("throttled — skipping name resolution", extra={"puuid": puuid})
        return _THROTTLED

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


async def _publish_and_commit(
    r: aioredis.Redis,
    cfg: Config,
    puuid: str,
    region: str,
    game_name: str,
    tag_line: str,
    member: Any,
) -> None:
    """Publish envelope and atomically update player state."""
    envelope = MessageEnvelope(
        source_stream=STREAM_PUUID,
        type="puuid",
        payload={
            "puuid": puuid,
            "game_name": game_name,
            "tag_line": tag_line,
            "region": region,
        },
        max_attempts=cfg.max_attempts,
        priority=PRIORITY_AUTO_20,
        correlation_id=str(uuid.uuid4()),
    )
    await publish(r, STREAM_PUUID, envelope)
    await register_player(
        r,
        puuid=puuid,
        region=region,
        game_name=game_name,
        tag_line=tag_line,
        players_all_max=cfg.players_all_max,
        transaction=True,
        extra_ops=lambda pipe: pipe.zrem(_DISCOVER_KEY, member),
    )


async def _fetch_member_state(
    r: aioredis.Redis,
    members: list[Any],
    *,
    default_region: str,
) -> tuple[list[bool], list[str | None]]:
    """Batch-fetch seeded/recrawl state for all members."""
    async with r.pipeline(transaction=False) as hex_pipe:
        for member in members:
            puuid_check, _ = _parse_member(str(member), default_region=default_region)
            hex_pipe.hexists(f"player:{puuid_check}", "seeded_at")
            hex_pipe.hget(f"player:{puuid_check}", "recrawl_after")
        pipe_results: list[Any] = await hex_pipe.execute()
    seeded = [pipe_results[i * 2] for i in range(len(members))]
    recrawl = [pipe_results[i * 2 + 1] for i in range(len(members))]
    return seeded, recrawl


async def _try_promote_member(  # noqa: PLR0913
    r: aioredis.Redis,
    cfg: Config,
    log: logging.Logger,
    riot: RiotClient,
    member: Any,
    already_seeded: bool,
    recrawl_after: str | None,
    now: float,
) -> int | None:
    """Try to promote one member. Return 1 on success, 0 on skip, None on halt."""
    puuid, region = _parse_member(str(member), default_region=cfg.default_region)
    if already_seeded:
        skip = _should_skip_seeded(recrawl_after, now)
        if skip:
            await r.zrem(_DISCOVER_KEY, member)
            return 0
        if skip is not None:
            return 0
    try:
        names = await _resolve_names(r, riot, puuid, region, log)
    except AuthError:
        log.critical("auth error — halt", extra={"puuid": puuid})
        await r.set(SYSTEM_HALTED_KEY, "1")
        return None
    except RiotAPIError:
        log.error("transient api error — deprioritizing", extra={"puuid": puuid})
        score = await r.zscore(_DISCOVER_KEY, member)
        if score is not None:
            new_score = max(score - 86_400_000, 0)
            if new_score == 0:
                await r.zrem(_DISCOVER_KEY, member)
            else:
                await r.zadd(_DISCOVER_KEY, {member: new_score})
        return 0
    if names is None:
        await r.zrem(_DISCOVER_KEY, member)
        return 0
    if names is _THROTTLED:
        return 0
    await _publish_and_commit(
        r,
        cfg,
        puuid,
        region,
        names[0],
        names[1],
        member,
    )
    return 1


async def _promote_batch(
    r: aioredis.Redis,
    cfg: Config,
    log: logging.Logger,
    riot: RiotClient,
    *,
    batch_size: int | None = None,
) -> int:
    """Promote discovered players from discover:players to stream:puuid."""
    if await is_system_halted(r):
        return 0
    size = batch_size if batch_size is not None else cfg.discovery_batch_size
    cutoff = time.time() - PLAYER_DATA_TTL_SECONDS
    removed = await r.zremrangebyscore(PLAYERS_ALL_KEY, "-inf", cutoff)
    if removed:
        log.info("trimmed stale entries", extra={"removed": removed})
    members: list[Any] = await r.zrevrange(
        _DISCOVER_KEY,
        0,
        size - 1,
    )
    if not members:
        return 0

    seeded_results, recrawl_values = await _fetch_member_state(
        r, members, default_region=cfg.default_region
    )
    promoted = 0
    now = time.time()
    for member, already_seeded, recrawl_after in zip(
        members, seeded_results, recrawl_values, strict=True
    ):
        result = await _try_promote_member(
            r,
            cfg,
            log,
            riot,
            member,
            already_seeded,
            recrawl_after,
            now,
        )
        if result is None:
            break  # auth error -- halt
        promoted += result

    if promoted:
        log.info("promoted discovered players", extra={"count": promoted})
    return promoted


async def _try_trickle_promote(
    r: aioredis.Redis,
    cfg: Config,
    log: logging.Logger,
    riot: RiotClient,
) -> bool:
    """Attempt a single trickle promotion when stream:puuid is shallow.

    Returns True when a player was promoted (caller should NOT bump
    polls_since_log), False otherwise (caller should bump).
    """
    puuid_depth: int = await r.xlen(STREAM_PUUID)
    if puuid_depth >= cfg.discovery_trickle_threshold:
        return False
    promoted = await _promote_batch(r, cfg, log, riot, batch_size=1)
    if promoted:
        log.debug(
            "trickle-promoted player",
            extra={
                "puuid_depth": puuid_depth,
                "threshold": cfg.discovery_trickle_threshold,
            },
        )
    return bool(promoted)


async def main() -> None:
    """Discovery loop — runs only when all pipeline streams are idle."""
    shutdown_event = asyncio.Event()

    log = get_logger("discovery")
    try:
        cfg = Config()
    except ValidationError as exc:
        print(
            f"Configuration error: {exc}\nCheck .env.example for required variables.",
            file=sys.stderr,
        )
        sys.exit(1)
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
                if await is_system_halted(r):
                    log.critical("system halted — exiting")
                    break
                idle = await _is_idle(r)
                if idle:
                    promoted = await _promote_batch(r, cfg, log, riot)
                    if promoted == 0:
                        polls_since_log += 1
                elif cfg.discovery_trickle_threshold > 0:
                    if not await _try_trickle_promote(r, cfg, log, riot):
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
