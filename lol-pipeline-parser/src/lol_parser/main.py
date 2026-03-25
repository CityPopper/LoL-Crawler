"""Parser service — parses raw match JSON and writes structured Redis data."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline._helpers import consumer_id, is_system_halted
from lol_pipeline.config import Config
from lol_pipeline.constants import CHAMPION_STATS_TTL_SECONDS, PLAYER_DATA_TTL_SECONDS
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.redis_client import get_redis
from lol_pipeline.service import run_consumer
from lol_pipeline.streams import ANALYZE_STREAM_MAXLEN, ack, nack_to_dlq

from lol_parser._data import (
    _DISCOVER_KEY,
    _GROUP,
    _IN_STREAM,
    _OUT_STREAM,
    _RANKED_QUEUE_ID,
)
from lol_parser._extract import (
    _extract_full_perks as _extract_full_perks,
)
from lol_parser._extract import (
    _extract_gold_timelines as _extract_gold_timelines,
)
from lol_parser._extract import (
    _extract_kill_events as _extract_kill_events,
)
from lol_parser._extract import (
    _extract_perks as _extract_perks,
)
from lol_parser._extract import (
    _extract_team_objectives,
    _extract_timeline_events,
    _normalize_patch,
)
from lol_parser._helpers import (
    _find_shared_positions,
    _group_by_team_position,
    _key_player,
    _key_player_matches,
    _queue_matchup_cmds,
    _queue_participant,
    _queue_pid_json,
    _validate,
    _warn_non_monotonic_gold,
)

# Re-export for tests that import from lol_parser.main
_normalize_patch = _normalize_patch
_validate = _validate


def _build_pid_mappings(
    participants: list[dict[str, Any]],
) -> tuple[dict[int, str], dict[int, str]]:
    """Build participantId -> puuid and participantId -> championName mappings."""
    pid_to_puuid: dict[int, str] = {}
    pid_to_champ: dict[int, str] = {}
    for p in participants:
        pid = p.get("participantId", 0)
        pid_to_puuid[pid] = p.get("puuid", "")
        pid_to_champ[pid] = p.get("championName", "Unknown")
    return pid_to_puuid, pid_to_champ


def _queue_player_matches_trim(
    pipe: aioredis.client.Pipeline,
    puuids: set[str],
    cfg: Config,
) -> None:
    """Queue ZREMRANGEBYRANK + EXPIRE for player:matches trim onto *pipe*."""
    for puuid in puuids:
        pm_key = _key_player_matches(puuid)
        pipe.zremrangebyrank(pm_key, 0, -(cfg.player_matches_max + 1))
        pipe.expire(pm_key, PLAYER_DATA_TTL_SECONDS)


async def _write_participants(
    r: aioredis.Redis,
    match_id: str,
    game_start: int,
    participants: list[dict[str, Any]],
    log: logging.Logger,
    cfg: Config,
) -> set[str]:
    """Batch all participant writes + trim ops into a single pipeline round-trip."""
    seen: set[str] = set()
    async with r.pipeline(transaction=False) as pipe:
        for participant in participants:
            try:
                puuid = _queue_participant(
                    pipe,
                    match_id,
                    game_start,
                    participant,
                    cfg.match_data_ttl_seconds,
                )
            except (KeyError, TypeError) as exc:
                log.warning(
                    "skipping participant with missing data",
                    extra={"match_id": match_id, "error": str(exc)},
                )
                continue
            seen.add(puuid)
        # P10-CR-6: Cap player:matches per player to prevent unbounded growth.
        # Merged into same pipeline (2 RTTs -> 1).
        _queue_player_matches_trim(pipe, seen, cfg)
        if seen:
            await pipe.execute()
    return seen


async def _write_bans(
    r: aioredis.Redis,
    match_id: str,
    info: dict[str, Any],
    patch: str,
    cfg: Config,
    log: logging.Logger,
) -> None:
    """Extract and store ban data from match teams."""
    if not cfg.track_bans:
        return
    queue_id = str(info.get("queueId", ""))
    if queue_id != _RANKED_QUEUE_ID:
        return
    teams = info.get("teams", [])
    ban_key = f"champion:bans:{patch}"
    async with r.pipeline(transaction=False) as pipe:
        for team in teams:
            for ban in team.get("bans", []):
                champ_id = ban.get("championId", 0)
                if champ_id > 0:  # -1 means no ban
                    pipe.hincrby(ban_key, str(champ_id), 1)
        pipe.hincrby(ban_key, "_total_games", 1)
        pipe.expire(ban_key, CHAMPION_STATS_TTL_SECONDS)
        await pipe.execute()
    log.debug("wrote bans", extra={"match_id": match_id, "patch": patch})


async def _write_matchups(
    r: aioredis.Redis,
    match_id: str,
    info: dict[str, Any],
    patch: str,
    cfg: Config,
    log: logging.Logger,
) -> None:
    """Compute and store lane matchup data from match participants."""
    if not cfg.track_matchups:
        return
    if str(info.get("queueId", "")) != _RANKED_QUEUE_ID:
        return

    team_positions = _group_by_team_position(info.get("participants", []))
    result = _find_shared_positions(team_positions)
    if result is None:
        return
    team_a, team_b, shared = result

    async with r.pipeline(transaction=False) as pipe:
        _queue_matchup_cmds(pipe, team_positions, team_a, team_b, shared, patch)
        await pipe.execute()
    log.debug("wrote matchups", extra={"match_id": match_id, "patch": patch})


async def _store_timeline_data(
    r: aioredis.Redis,
    match_id: str,
    info: dict[str, Any],
    cfg: Config,
    log: logging.Logger,
) -> None:
    """Store build orders, skill orders, gold timelines, and kill events."""
    frames = info.get("frames", [])
    build_orders, skill_orders = _extract_timeline_events(frames)

    pid_to_puuid, pid_to_champ = _build_pid_mappings(info.get("participants", []))

    gold_timelines = _extract_gold_timelines(frames)
    kill_events = _extract_kill_events(frames, pid_to_champ)
    _warn_non_monotonic_gold(gold_timelines, match_id, log)

    ttl = cfg.match_data_ttl_seconds
    async with r.pipeline(transaction=False) as pipe:
        _queue_pid_json(pipe, build_orders, pid_to_puuid, "build", match_id, ttl)
        _queue_pid_json(pipe, skill_orders, pid_to_puuid, "skills", match_id, ttl)
        _queue_pid_json(pipe, gold_timelines, pid_to_puuid, "gold_timeline", match_id, ttl)
        pipe.set(f"kill_events:{match_id}", json.dumps(kill_events), ex=ttl)
        await pipe.execute()


async def _parse_timeline(
    r: aioredis.Redis,
    match_id: str,
    cfg: Config,
    log: logging.Logger,
) -> None:
    """Parse stored match timeline for build/skill orders, gold timelines, kill events."""
    if not cfg.fetch_timeline:
        return
    raw = await r.get(f"raw:timeline:{match_id}")
    if not raw:
        return
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("invalid timeline JSON", extra={"match_id": match_id})
        return

    await _store_timeline_data(r, match_id, data.get("info", {}), cfg, log)
    log.debug("parsed timeline", extra={"match_id": match_id})


async def _discover_co_players(
    r: aioredis.Redis,
    cfg: Config,
    seen_puuids: set[str],
    region: str,
    game_start: int,
    log: logging.Logger,
) -> None:
    """Queue unseeded co-players for discovery."""
    puuid_list = sorted(seen_puuids)
    async with r.pipeline(transaction=False) as pipe:
        for puuid in puuid_list:
            await pipe.hexists(_key_player(puuid), "seeded_at")  # type: ignore[misc]
        seeded_results: list[bool] = await pipe.execute()
    discover_scores: dict[str, float] = {}
    for puuid, already_seeded in zip(
        puuid_list,
        seeded_results,
        strict=True,
    ):
        if not already_seeded:
            discover_scores[f"{puuid}:{region}"] = float(game_start)
    if discover_scores:
        await r.zadd(_DISCOVER_KEY, discover_scores, gt=True)
        await r.zremrangebyrank(
            _DISCOVER_KEY,
            0,
            -(cfg.max_discover_players + 1),
        )
        # RDB-4: Safety-net TTL — only set when no TTL exists (-1).
        # Prevents stale data if Discovery is disabled for 30+ days.
        if await r.ttl(_DISCOVER_KEY) == -1:
            await r.expire(_DISCOVER_KEY, PLAYER_DATA_TTL_SECONDS)
        log.debug(
            "queued for discovery",
            extra={"count": len(discover_scores)},
        )


async def _load_and_validate(
    r: aioredis.Redis,
    raw_store: RawStore,
    match_id: str,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
) -> dict[str, Any] | None:
    """Load raw JSON and validate. Return info dict, or None after DLQ nack."""
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
        return None

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
        return None
    return info


async def _write_match_metadata(
    r: aioredis.Redis,
    match_id: str,
    info: dict[str, Any],
    region: str,
    cfg: Config,
) -> int:
    """Write match-level fields and return first_parse flag (1 = first, 0 = re-parse)."""
    match_key = f"match:{match_id}"
    first_parse: int = await r.hsetnx(match_key, "status", "parsed")
    game_start = info["gameStartTimestamp"]
    match_fields: dict[str, str] = {
        "queue_id": str(info.get("queueId", "")),
        "game_mode": info.get("gameMode", ""),
        "game_type": info.get("gameType", ""),
        "game_version": info.get("gameVersion", ""),
        "patch": _normalize_patch(info.get("gameVersion", "")),
        "game_duration": str(info.get("gameDuration", "")),
        "game_start": str(game_start),
        "platform_id": info.get("platformId", ""),
        "region": region,
        "status": "parsed",
    }
    match_fields.update(_extract_team_objectives(info))
    async with r.pipeline(transaction=True) as pipe:
        pipe.hset(match_key, mapping=match_fields)
        pipe.expire(match_key, cfg.match_data_ttl_seconds)
        await pipe.execute()
    return first_parse


async def _publish_analyze_batch(
    r: aioredis.Redis,
    seen_puuids: set[str],
    cfg: Config,
    envelope: MessageEnvelope,
) -> None:
    """Batch-publish analyze messages for all seen participants."""
    if not seen_puuids:
        return
    async with r.pipeline(transaction=False) as pub_pipe:
        for puuid in seen_puuids:
            out = MessageEnvelope(
                source_stream=_OUT_STREAM,
                type="analyze",
                payload={"puuid": puuid},
                max_attempts=cfg.max_attempts,
                priority=envelope.priority,
                correlation_id=envelope.correlation_id,
            )
            pub_pipe.xadd(
                _OUT_STREAM,
                out.to_redis_fields(),  # type: ignore[arg-type]
                maxlen=ANALYZE_STREAM_MAXLEN,
                approximate=True,
            )
        await pub_pipe.execute()


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

    info = await _load_and_validate(r, raw_store, match_id, msg_id, envelope, log)
    if info is None:
        return

    game_start: int = info["gameStartTimestamp"]
    first_parse = await _write_match_metadata(r, match_id, info, region, cfg)
    seen_puuids = await _write_participants(
        r,
        match_id,
        game_start,
        info["participants"],
        log,
        cfg,
    )

    # Ban/matchup tracking + timeline parsing run concurrently.
    # Bans/matchups only on first parse — HINCRBY is not idempotent.
    if first_parse:
        patch = _normalize_patch(info.get("gameVersion", ""))
        await asyncio.gather(
            _write_bans(r, match_id, info, patch, cfg, log),
            _write_matchups(r, match_id, info, patch, cfg, log),
            _parse_timeline(r, match_id, cfg, log),
        )
    else:
        await _parse_timeline(r, match_id, cfg, log)

    await _publish_analyze_batch(r, seen_puuids, cfg, envelope)
    await _discover_co_players(r, cfg, seen_puuids, region, game_start, log)

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
    consumer = consumer_id()

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
