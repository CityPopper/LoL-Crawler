"""Parser service — parses raw match JSON and writes structured Redis data."""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.helpers import is_system_halted
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.redis_client import get_redis
from lol_pipeline.service import run_consumer
from lol_pipeline.streams import ANALYZE_STREAM_MAXLEN, ack, nack_to_dlq, publish

_IN_STREAM = "stream:parse"
_OUT_STREAM = "stream:analyze"
_GROUP = "parsers"
_DISCOVER_KEY = "discover:players"
_ITEM_KEYS = [f"item{i}" for i in range(7)]

MATCH_DATA_TTL_SECONDS: int = int(os.getenv("MATCH_DATA_TTL_SECONDS", "604800"))
MAX_DISCOVER_PLAYERS: int = int(os.getenv("MAX_DISCOVER_PLAYERS", "50000"))
PLAYER_MATCHES_MAX: int = int(os.getenv("PLAYER_MATCHES_MAX", "500"))


def _validate(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Extract and validate info + metadata; raise KeyError on missing required fields."""
    info: dict[str, Any] = data["info"]
    if "participants" not in info or not info["participants"]:
        raise KeyError("participants")
    if "gameStartTimestamp" not in info:
        raise KeyError("gameStartTimestamp")
    return data["metadata"], info


def _queue_participant(
    pipe: aioredis.client.Pipeline,
    match_id: str,
    game_start: int,
    p: dict[str, Any],
) -> str:
    """Queue all Redis commands for one participant onto *pipe* (no execute).

    Returns the participant's puuid.
    """
    puuid: str = p["puuid"]
    items = json.dumps([p.get(k, 0) for k in _ITEM_KEYS])
    participant_key = f"participant:{match_id}:{puuid}"
    pipe.hset(
        participant_key,
        mapping={
            "champion_id": str(p.get("championId", "")),
            "champion_name": p.get("championName", ""),
            "team_id": str(p.get("teamId", "")),
            "team_position": p.get("teamPosition", ""),
            "role": p.get("role", ""),
            "win": "1" if p.get("win") else "0",
            "kills": str(p.get("kills", 0)),
            "deaths": str(p.get("deaths", 0)),
            "assists": str(p.get("assists", 0)),
            "gold_earned": str(p.get("goldEarned", 0)),
            "total_damage_dealt_to_champions": str(p.get("totalDamageDealtToChampions", 0)),
            "total_minions_killed": str(p.get("totalMinionsKilled", 0)),
            "vision_score": str(p.get("visionScore", 0)),
            "items": items,
        },
    )
    pipe.expire(participant_key, MATCH_DATA_TTL_SECONDS)
    pipe.zadd(f"player:matches:{puuid}", {match_id: float(game_start)})
    riot_name = p.get("riotIdGameName", "")
    riot_tag = p.get("riotIdTagline", "")
    if riot_name and riot_tag:
        pipe.hsetnx(f"player:{puuid}", "game_name", riot_name)
        pipe.hsetnx(f"player:{puuid}", "tag_line", riot_tag)
    return puuid


async def _write_participants(
    r: aioredis.Redis,
    match_id: str,
    game_start: int,
    participants: list[dict[str, Any]],
    log: logging.Logger,
) -> set[str]:
    """Batch all participant writes into a single pipeline round-trip."""
    seen: set[str] = set()
    async with r.pipeline(transaction=False) as pipe:
        for participant in participants:
            try:
                puuid = _queue_participant(pipe, match_id, game_start, participant)
            except (KeyError, TypeError) as exc:
                log.warning(
                    "skipping participant with missing data",
                    extra={"match_id": match_id, "error": str(exc)},
                )
                continue
            seen.add(puuid)
        if seen:
            await pipe.execute()
    # P10-CR-6: Cap player:matches per player to prevent unbounded growth.
    for puuid in seen:
        await r.zremrangebyrank(f"player:matches:{puuid}", 0, -(PLAYER_MATCHES_MAX + 1))
    return seen


async def _parse_match(
    r: aioredis.Redis,
    raw_store: RawStore,
    cfg: Config,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
) -> None:
    if await is_system_halted(r):
        log.critical("system halted — skipping message")
        return

    match_id: str = envelope.payload["match_id"]
    region: str = envelope.payload["region"]
    log.info("parsing match", extra={"match_id": match_id, "region": region})

    raw = await raw_store.get(match_id)
    if raw is None:
        log.error("raw blob missing", extra={"match_id": match_id})
        await nack_to_dlq(
            r,
            envelope,
            failure_code="parse_error",
            failed_by="parser",
            original_message_id=msg_id,
        )
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        return

    try:
        data = json.loads(raw)
        _meta, info = _validate(data)
    except (KeyError, json.JSONDecodeError, TypeError) as exc:
        log.error("parse error", extra={"match_id": match_id, "error": str(exc)})
        await nack_to_dlq(
            r,
            envelope,
            failure_code="parse_error",
            failed_by="parser",
            original_message_id=msg_id,
        )
        await ack(r, _IN_STREAM, _GROUP, msg_id)
        return

    game_start: int = info["gameStartTimestamp"]
    match_key = f"match:{match_id}"
    async with r.pipeline(transaction=True) as pipe:
        pipe.hset(
            match_key,
            mapping={
                "queue_id": str(info.get("queueId", "")),
                "game_mode": info.get("gameMode", ""),
                "game_type": info.get("gameType", ""),
                "game_version": info.get("gameVersion", ""),
                "game_duration": str(info.get("gameDuration", "")),
                "game_start": str(game_start),
                "platform_id": info.get("platformId", ""),
                "region": region,
                "status": "parsed",
            },
        )
        pipe.sadd("match:status:parsed", match_id)
        pipe.expire(match_key, MATCH_DATA_TTL_SECONDS)
        await pipe.execute()

    seen_puuids = await _write_participants(r, match_id, game_start, info["participants"], log)

    for puuid in seen_puuids:
        out = MessageEnvelope(
            source_stream=_OUT_STREAM,
            type="analyze",
            payload={"puuid": puuid},
            max_attempts=cfg.max_attempts,
        )
        await publish(r, _OUT_STREAM, out, maxlen=ANALYZE_STREAM_MAXLEN)

    # Discover co-players: ZADD with GT so score only increases (newest game wins).
    # Member encodes "puuid:region" so discovery service has full context.
    # Only add PUUIDs not already seeded (no seeded_at field in player:{puuid}).
    # Note: backfill above may create player:{puuid} with game_name/tag_line,
    # but those still need discovery to crawl their match history.
    # Batch all HEXISTS checks in a single pipeline round-trip (avoid N+1).
    puuid_list = sorted(seen_puuids)
    async with r.pipeline(transaction=False) as pipe:
        for puuid in puuid_list:
            await pipe.hexists(f"player:{puuid}", "seeded_at")  # type: ignore[misc]
        seeded_results: list[bool] = await pipe.execute()
    discover_scores: dict[str, float] = {}
    for puuid, already_seeded in zip(puuid_list, seeded_results, strict=True):
        if not already_seeded:
            discover_scores[f"{puuid}:{region}"] = float(game_start)
    if discover_scores:
        await r.zadd(_DISCOVER_KEY, discover_scores, gt=True)
        await r.zremrangebyrank(_DISCOVER_KEY, 0, -(MAX_DISCOVER_PLAYERS + 1))
        log.debug("queued for discovery", extra={"count": len(discover_scores)})

    await ack(r, _IN_STREAM, _GROUP, msg_id)
    log.info(
        "parsed",
        extra={
            "match_id": match_id,
            "region": region,
            "game_mode": info.get("gameMode", ""),
            "participants": len(seen_puuids),
        },
    )


async def main() -> None:
    """Parser worker loop."""
    log = get_logger("parser")
    cfg = Config()
    r = get_redis(cfg.redis_url)
    raw_store = RawStore(r, data_dir=cfg.match_data_dir)
    consumer = f"{socket.gethostname()}-{os.getpid()}"

    async def _handler(msg_id: str, envelope: MessageEnvelope) -> None:
        await _parse_match(r, raw_store, cfg, msg_id, envelope, log)

    log.info("parser started", extra={"consumer": consumer})
    try:
        autoclaim_ms = cfg.stream_ack_timeout * 1000
        await run_consumer(
            r,
            _IN_STREAM,
            _GROUP,
            consumer,
            _handler,
            log,
            autoclaim_min_idle_ms=autoclaim_ms,
        )
    finally:
        await r.aclose()
