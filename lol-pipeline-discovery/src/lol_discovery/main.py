"""Discovery service — promotes discovered players to stream:puuid when pipeline is idle."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import AuthError, NotFoundError, RiotAPIError, RiotClient
from lol_pipeline.streams import publish
from redis.exceptions import ResponseError

_STREAM_PUUID = "stream:puuid"
_DISCOVER_KEY = "discover:players"

_shutdown = False


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
    """Return True when stream:puuid has no unread or unACKed entries for any consumer group.

    XLEN counts all entries ever added (including already-consumed ones) so it cannot be
    used as an idle check.  XINFO GROUPS provides 'pending' (delivered but not yet ACKed)
    and 'lag' (not yet delivered) — both zero means the pipeline has caught up.
    """
    try:
        groups: list[Any] = await r.xinfo_groups(_STREAM_PUUID)  # type: ignore[misc]
    except ResponseError:
        return True  # stream or group does not exist yet — nothing to process
    if not groups:
        return True  # no consumer groups registered = nothing consuming the stream
    return all(int(g.get("pending", 0)) == 0 and int(g.get("lag", 0)) == 0 for g in groups)


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
    game_name: str | None = await r.hget(f"player:{puuid}", "game_name")
    tag_line: str | None = await r.hget(f"player:{puuid}", "tag_line")
    if game_name and tag_line:
        return game_name, tag_line

    try:
        account = await riot.get_account_by_puuid(puuid, region)
        return str(account["gameName"]), str(account["tagLine"])
    except NotFoundError:
        log.warning("account not found by puuid", extra={"puuid": puuid})
        return None


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
        seeded: bool = await r.hexists(f"player:{puuid}", "seeded_at")
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
        await publish(r, _STREAM_PUUID, envelope)
        await r.zrem(_DISCOVER_KEY, member)
        now_iso = datetime.now(tz=UTC).isoformat()
        await r.hset(  # type: ignore[misc]
            f"player:{puuid}",
            mapping={
                "game_name": game_name,
                "tag_line": tag_line,
                "region": region,
                "seeded_at": now_iso,
            },
        )
        promoted += 1

    if promoted:
        log.info("promoted discovered players", extra={"count": promoted})
    return promoted


def _handle_sigterm(_signum: int, _frame: Any) -> None:
    global _shutdown  # noqa: PLW0603
    _shutdown = True


async def main() -> None:
    """Discovery loop — runs only when stream:puuid is idle."""
    global _shutdown  # noqa: PLW0603
    _shutdown = False
    signal.signal(signal.SIGTERM, _handle_sigterm)

    log = get_logger("discovery")
    cfg = Config()
    r = get_redis(cfg.redis_url)
    riot = RiotClient(cfg.riot_api_key, r=r)

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
        while not _shutdown:
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
            await asyncio.sleep(interval_s)
        log.info("SIGTERM received — shutting down gracefully")
    finally:
        await riot.close()
        await r.aclose()
