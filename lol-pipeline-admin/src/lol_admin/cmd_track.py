"""Admin CLI: track sub-command — seed a player directly from admin."""

from __future__ import annotations

import argparse
import logging
import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from lol_pipeline._helpers import is_system_halted, register_player
from lol_pipeline.config import Config
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import PRIORITY_MANUAL_20, set_priority
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.streams import publish

from lol_admin._constants import _STREAM_PUUID
from lol_admin._helpers import (
    _get_log,
    _print_error,
    _print_ok,
    _resolve_puuid,
    _sanitize,
)


def _parse_epoch(value: str | None) -> float:
    """Parse an ISO 8601 timestamp to epoch seconds; return 0.0 on any failure."""
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


async def _within_cooldown(
    r: aioredis.Redis,
    puuid: str,
    cooldown_minutes: int,
    log: logging.Logger,
) -> bool:
    """Return True if the player was recently seeded or crawled."""
    fields: list[str | None] = await r.hmget(  # type: ignore[misc]
        f"player:{puuid}", ["seeded_at", "last_crawled_at"]
    )
    seeded_at_str, crawled_at_str = fields[0], fields[1]
    seeded_epoch = _parse_epoch(seeded_at_str)
    crawled_epoch = _parse_epoch(crawled_at_str)

    trigger = "seeded_at" if seeded_epoch >= crawled_epoch else "last_crawled_at"
    last_activity = max(seeded_epoch, crawled_epoch)

    if last_activity == 0.0:
        return False

    age_minutes = (datetime.now(tz=UTC).timestamp() - last_activity) / 60
    if age_minutes < cooldown_minutes:
        log.info(
            "skipping re-seed — within cooldown",
            extra={
                "trigger_field": trigger,
                "cooldown_minutes": cooldown_minutes,
                "age_minutes": round(age_minutes, 2),
                "puuid": puuid,
            },
        )
        return True
    return False


async def seed(
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    game_name: str,
    tag_line: str,
    region: str,
    log: logging.Logger,
) -> int:
    """Core seed logic. Returns exit code (0 = success, 1 = error).

    This is the programmatic API used by integration tests and the ``track``
    CLI sub-command.  The signature intentionally mirrors the former
    ``lol_seed.main.seed`` so that callers can migrate with a one-line
    import change.
    """
    region = region.lower()
    if await is_system_halted(r):
        log.critical("system halted — refusing to seed")
        return 1

    log.debug(
        "resolving PUUID",
        extra={"game_name": game_name, "tag_line": tag_line, "region": region},
    )
    riot_id = f"{game_name}#{tag_line}"
    puuid = await _resolve_puuid(riot, riot_id, region, r)
    if puuid is None:
        return 1

    if await _within_cooldown(r, puuid, cfg.seed_cooldown_minutes, log):
        return 0

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
        priority=PRIORITY_MANUAL_20,
        correlation_id=str(uuid.uuid4()),
    )
    await set_priority(r, puuid)
    entry_id = await publish(r, _STREAM_PUUID, envelope)

    await register_player(
        r,
        puuid=puuid,
        region=region,
        game_name=game_name,
        tag_line=tag_line,
        players_all_max=cfg.players_all_max,
    )

    log.info(
        "player seeded",
        extra={
            "puuid": puuid,
            "game_name": game_name,
            "tag_line": tag_line,
            "region": region,
            "stream_entry": entry_id,
        },
    )
    return 0


async def cmd_track(
    r: aioredis.Redis,
    riot: RiotClient,
    cfg: Config,
    args: argparse.Namespace,
) -> int:
    """Track a player: resolve Riot ID, check cooldown, seed to stream:puuid."""
    riot_id: str = args.riot_id
    if "#" not in riot_id:
        _print_error(f"invalid Riot ID — expected GameName#TagLine: {_sanitize(riot_id)}")
        return 1

    game_name, tag_line = riot_id.split("#", 1)
    region: str = args.region
    log = _get_log()

    rc = await seed(r, riot, cfg, game_name, tag_line, region, log)
    if rc == 0:
        _print_ok(f"tracking {_sanitize(riot_id)} → {_STREAM_PUUID}")
    return rc
