"""Parser service — parses raw match JSON and writes structured Redis data."""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.constants import CHAMPION_STATS_TTL_SECONDS, PLAYER_DATA_TTL_SECONDS
from lol_pipeline.helpers import is_system_halted
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.redis_client import get_redis
from lol_pipeline.service import run_consumer
from lol_pipeline.streams import ANALYZE_STREAM_MAXLEN, ack, nack_to_dlq

from lol_parser._data import (
    _DISCOVER_KEY,
    _GOLD_TIMELINE_MAX_FRAMES,
    _GROUP,
    _IN_STREAM,
    _ITEM_KEYS,
    _KILL_EVENTS_MAX,
    _OUT_STREAM,
    _RANKED_QUEUE_ID,
    _STATUS_TTL,
)


def _normalize_patch(game_version: str) -> str:
    """Extract major.minor from game version (e.g. '13.24.1' -> '13.24')."""
    parts = game_version.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return game_version


def _extract_perks(p: dict[str, Any]) -> tuple[int, int, int]:
    """Return (keystone_id, primary_style_id, sub_style_id) from participant perks."""
    perks = p.get("perks", {})
    styles = perks.get("styles", [])
    primary_style = styles[0] if styles else {}
    sub_style = styles[1] if len(styles) > 1 else {}
    selections = primary_style.get("selections", [])
    keystone = selections[0].get("perk", 0) if selections else 0
    return keystone, primary_style.get("style", 0), sub_style.get("style", 0)


def _extract_full_perks(
    p: dict[str, Any],
) -> tuple[list[int], list[int], list[int]]:
    """Return (primary_selections, sub_selections, stat_shards) from participant perks.

    Extracts available elements without asserting exact array lengths.
    Returns empty lists for any missing data.
    """
    perks = p.get("perks", {})
    styles = perks.get("styles", [])
    primary_style = styles[0] if styles else {}
    sub_style = styles[1] if len(styles) > 1 else {}
    primary_sel = [s.get("perk", 0) for s in primary_style.get("selections", [])]
    sub_sel = [s.get("perk", 0) for s in sub_style.get("selections", [])]
    stat_perks = perks.get("statPerks", {})
    stat_shards: list[int] = []
    for key in ("offense", "flex", "defense"):
        if key in stat_perks:
            stat_shards.append(stat_perks[key])
    return primary_sel, sub_sel, stat_shards


def _extract_team_objectives(info: dict[str, Any]) -> dict[str, str]:
    """Extract team objective fields from info.teams[], keyed by teamId (100/200).

    Maps via explicit teamId comparison (100=blue, 200=red), NOT array index.
    Returns a flat dict of string fields ready for HSET on match:{match_id}.
    """
    teams = info.get("teams", [])
    team_map: dict[int, dict[str, Any]] = {}
    for team in teams:
        tid = team.get("teamId", 0)
        if tid in (100, 200):
            team_map[tid] = team.get("objectives", {})

    result: dict[str, str] = {}
    for tid, prefix in ((100, "team_blue"), (200, "team_red")):
        obj = team_map.get(tid, {})
        result[f"{prefix}_dragons"] = str(obj.get("dragon", {}).get("kills", 0))
        result[f"{prefix}_barons"] = str(obj.get("baron", {}).get("kills", 0))
        result[f"{prefix}_towers"] = str(obj.get("tower", {}).get("kills", 0))
        result[f"{prefix}_inhibitors"] = str(obj.get("inhibitor", {}).get("kills", 0))
        result[f"{prefix}_heralds"] = str(obj.get("riftHerald", {}).get("kills", 0))
        champion_obj = obj.get("champion", {})
        result[f"{prefix}_first_blood"] = "1" if champion_obj.get("first") else "0"
    return result


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
    match_data_ttl: int,
) -> str:
    """Queue all Redis commands for one participant onto *pipe* (no execute).

    Returns the participant's puuid.
    """
    puuid: str = p["puuid"]
    keystone, primary_style_id, sub_style_id = _extract_perks(p)
    primary_sel, sub_sel, stat_shards = _extract_full_perks(p)
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
            "summoner1_id": str(p.get("summoner1Id", 0)),
            "summoner2_id": str(p.get("summoner2Id", 0)),
            "champion_level": str(p.get("champLevel", 0)),
            "gold_spent": str(p.get("goldSpent", 0)),
            "physical_damage": str(p.get("physicalDamageDealtToChampions", 0)),
            "magic_damage": str(p.get("magicDamageDealtToChampions", 0)),
            "true_damage": str(p.get("trueDamageDealtToChampions", 0)),
            "damage_taken": str(p.get("totalDamageTaken", 0)),
            "damage_mitigated": str(p.get("damageSelfMitigated", 0)),
            "healing_done": str(p.get("totalHeal", 0)),
            "wards_placed": str(p.get("wardsPlaced", 0)),
            "wards_killed": str(p.get("wardsKilled", 0)),
            "detector_wards": str(p.get("detectorWardsPlaced", 0)),
            "neutral_minions": str(p.get("neutralMinionsKilled", 0)),
            "turret_kills": str(p.get("turretKills", 0)),
            "double_kills": str(p.get("doubleKills", 0)),
            "triple_kills": str(p.get("tripleKills", 0)),
            "quadra_kills": str(p.get("quadraKills", 0)),
            "penta_kills": str(p.get("pentaKills", 0)),
            "time_played": str(p.get("timePlayed", 0)),
            "perk_keystone": str(keystone),
            "perk_primary_style": str(primary_style_id),
            "perk_sub_style": str(sub_style_id),
            "perk_primary_selections": json.dumps(primary_sel),
            "perk_sub_selections": json.dumps(sub_sel),
            "perk_stat_shards": json.dumps(stat_shards),
        },
    )
    pipe.expire(participant_key, match_data_ttl)
    pipe.sadd(f"match:participants:{match_id}", puuid)
    pipe.expire(f"match:participants:{match_id}", match_data_ttl)
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
    cfg: Config,
) -> set[str]:
    """Batch all participant writes into a single pipeline round-trip."""
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
        if seen:
            await pipe.execute()
    # P10-CR-6: Cap player:matches per player to prevent unbounded growth.
    # P13-OPT-6: Batch all trim + expire ops into one pipeline round-trip.
    if seen:
        async with r.pipeline(transaction=False) as trim_pipe:
            for puuid in seen:
                trim_pipe.zremrangebyrank(
                    f"player:matches:{puuid}",
                    0,
                    -(cfg.player_matches_max + 1),
                )
                trim_pipe.expire(f"player:matches:{puuid}", PLAYER_DATA_TTL_SECONDS)  # 30 days
            await trim_pipe.execute()
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
    queue_id = str(info.get("queueId", ""))
    if queue_id != _RANKED_QUEUE_ID:
        return

    participants = info.get("participants", [])
    # Group by team and position
    team_positions: dict[int, dict[str, dict[str, Any]]] = {}
    for p in participants:
        team_id = p.get("teamId", 0)
        position = p.get("teamPosition", "")
        if not position or not team_id:
            continue
        if team_id not in team_positions:
            team_positions[team_id] = {}
        team_positions[team_id][position] = p

    teams = sorted(team_positions.keys())
    if len(teams) != 2:
        return

    team_a, team_b = teams[0], teams[1]
    # Match positions that exist in both teams
    shared_positions = set(team_positions[team_a]) & set(team_positions[team_b])
    if not shared_positions:
        return

    async with r.pipeline(transaction=False) as pipe:
        for position in sorted(shared_positions):
            a = team_positions[team_a][position]
            b = team_positions[team_b][position]
            champ_a = a.get("championName", "")
            champ_b = b.get("championName", "")
            if not champ_a or not champ_b:
                continue
            win_a = 1 if a.get("win") else 0
            win_b = 1 - win_a

            # Store matchup: champion A's perspective vs champion B
            key_ab = f"matchup:{champ_a}:{champ_b}:{position}:{patch}"
            pipe.hincrby(key_ab, "games", 1)
            pipe.hincrby(key_ab, "wins", win_a)
            pipe.expire(key_ab, CHAMPION_STATS_TTL_SECONDS)

            # Store reverse: champion B's perspective vs champion A
            key_ba = f"matchup:{champ_b}:{champ_a}:{position}:{patch}"
            pipe.hincrby(key_ba, "games", 1)
            pipe.hincrby(key_ba, "wins", win_b)
            pipe.expire(key_ba, CHAMPION_STATS_TTL_SECONDS)

            # Index: track all matchups for a champion
            idx_a = f"matchup:index:{champ_a}:{position}:{patch}"
            pipe.sadd(idx_a, champ_b)
            pipe.expire(idx_a, CHAMPION_STATS_TTL_SECONDS)
            idx_b = f"matchup:index:{champ_b}:{position}:{patch}"
            pipe.sadd(idx_b, champ_a)
            pipe.expire(idx_b, CHAMPION_STATS_TTL_SECONDS)

        await pipe.execute()
    log.debug("wrote matchups", extra={"match_id": match_id, "patch": patch})


def _extract_timeline_events(
    frames: list[dict[str, Any]],
) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    """Extract build and skill orders from timeline frames."""
    build_orders: dict[int, list[int]] = {}
    skill_orders: dict[int, list[int]] = {}
    for frame in frames:
        for event in frame.get("events", []):
            event_type = event.get("type", "")
            pid = event.get("participantId", 0)
            if not pid:
                continue
            if event_type == "ITEM_PURCHASED":
                build_orders.setdefault(pid, []).append(
                    event.get("itemId", 0),
                )
            elif event_type == "SKILL_LEVEL_UP" and event.get("levelUpType") == "NORMAL":
                skill_orders.setdefault(pid, []).append(
                    event.get("skillSlot", 0),
                )
    return build_orders, skill_orders


def _extract_gold_timelines(
    frames: list[dict[str, Any]],
) -> dict[int, list[int]]:
    """Extract per-participant gold totals from timeline participantFrames.

    Returns a dict mapping participant ID (int) to a list of totalGold values,
    one per frame, capped at 120 frames.
    """
    gold: dict[int, list[int]] = {}
    for frame in frames[:_GOLD_TIMELINE_MAX_FRAMES]:
        pframes = frame.get("participantFrames", {})
        for pid_str, pdata in pframes.items():
            try:
                pid = int(pid_str)
            except (ValueError, TypeError):
                continue
            gold.setdefault(pid, []).append(pdata.get("totalGold", 0))
    return gold


def _extract_kill_events(
    frames: list[dict[str, Any]],
    pid_to_champ: dict[int, str],
) -> list[dict[str, Any]]:
    """Extract CHAMPION_KILL events from timeline, denormalized with champion names.

    Returns a list of kill event dicts sorted by timestamp, capped at 200.
    Unknown participant IDs resolve to "Unknown" (logged at call site).
    """
    kills: list[dict[str, Any]] = []
    for frame in frames:
        for event in frame.get("events", []):
            if event.get("type") != "CHAMPION_KILL":
                continue
            killer_id = event.get("killerId", 0)
            victim_id = event.get("victimId", 0)
            assist_ids: list[int] = event.get("assistingParticipantIds", [])
            pos = event.get("position", {})
            kills.append(
                {
                    "t": event.get("timestamp", 0),
                    "killer": pid_to_champ.get(killer_id, "Unknown"),
                    "victim": pid_to_champ.get(victim_id, "Unknown"),
                    "assists": [pid_to_champ.get(a, "Unknown") for a in assist_ids],
                    "x": pos.get("x", 0),
                    "y": pos.get("y", 0),
                }
            )
    kills.sort(key=lambda e: e["t"])
    return kills[:_KILL_EVENTS_MAX]


def _warn_non_monotonic_gold(
    gold_timelines: dict[int, list[int]],
    match_id: str,
    log: logging.Logger,
) -> None:
    """Log warning for any participant with non-monotonic totalGold sequence."""
    for pid, golds in gold_timelines.items():
        for i in range(1, len(golds)):
            if golds[i] < golds[i - 1]:
                log.warning(
                    "non-monotonic gold timeline",
                    extra={"match_id": match_id, "participant_id": pid},
                )
                break


def _queue_pid_json(
    pipe: aioredis.client.Pipeline,
    pid_data: dict[int, list[int]],
    pid_to_puuid: dict[int, str],
    key_prefix: str,
    match_id: str,
    ttl: int,
) -> None:
    """Queue SET commands for per-participant JSON arrays onto a pipeline."""
    for pid, values in pid_data.items():
        puuid = pid_to_puuid.get(pid, "")
        if puuid:
            pipe.set(f"{key_prefix}:{match_id}:{puuid}", json.dumps(values), ex=ttl)


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

    pid_to_puuid: dict[int, str] = {}
    pid_to_champ: dict[int, str] = {}
    for p in info.get("participants", []):
        pid = p.get("participantId", 0)
        pid_to_puuid[pid] = p.get("puuid", "")
        pid_to_champ[pid] = p.get("championName", "Unknown")

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
            await pipe.hexists(f"player:{puuid}", "seeded_at")  # type: ignore[misc]
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
        log.debug(
            "queued for discovery",
            extra={"count": len(discover_scores)},
        )


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

    # Atomic idempotency guard: SADD returns 1 if the member was newly added
    # (first writer wins) or 0 if it already existed (another worker parsed
    # this match first). This eliminates the TOCTOU race where two workers
    # could both see SISMEMBER=False and double-count HINCRBY in bans/matchups.
    # EXPIRE is idempotent — always refresh TTL (avoids extra TTL check RTT).
    async with r.pipeline(transaction=False) as idem_pipe:
        idem_pipe.sadd("match:status:parsed", match_id)
        idem_pipe.expire("match:status:parsed", _STATUS_TTL)
        idem_results = await idem_pipe.execute()
    first_parse: int = idem_results[0]

    match_key = f"match:{match_id}"
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

    seen_puuids = await _write_participants(r, match_id, game_start, info["participants"], log, cfg)

    # Ban and matchup tracking (ranked solo only).
    # Only on first parse — HINCRBY is not idempotent.
    if first_parse:
        patch = _normalize_patch(info.get("gameVersion", ""))
        await _write_bans(r, match_id, info, patch, cfg, log)
        await _write_matchups(r, match_id, info, patch, cfg, log)

    # Timeline parsing (build order, skill order).
    await _parse_timeline(r, match_id, cfg, log)

    # P13-OPT-7: Batch all analyze publishes into one pipeline round-trip.
    if seen_puuids:
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
