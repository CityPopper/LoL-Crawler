"""Seed service — resolves a Riot ID to PUUID and enqueues it for crawling."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import UTC, datetime

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import set_priority
from lol_pipeline.redis_client import get_redis
from lol_pipeline.resolve import resolve_puuid
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.streams import publish

_STREAM = "stream:puuid"
_MSG_TYPE = "puuid"


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
    """Return True (and log reason) if the player was recently seeded or crawled."""
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
    """Core seed logic. Returns exit code (0 = success, 1 = error)."""
    region = region.lower()
    if await r.get("system:halted"):
        log.critical("system halted — refusing to seed")
        return 1

    log.debug(
        "resolving PUUID",
        extra={"game_name": game_name, "tag_line": tag_line, "region": region},
    )
    puuid = await resolve_puuid(r, riot, game_name, tag_line, region, log)
    if puuid:
        log.info(
            "PUUID resolved",
            extra={"game_name": game_name, "tag_line": tag_line, "puuid": puuid[:12]},
        )
    if puuid is None:
        return 1

    if await _within_cooldown(r, puuid, cfg.seed_cooldown_minutes, log):
        return 0

    envelope = MessageEnvelope(
        source_stream=_STREAM,
        type=_MSG_TYPE,
        payload={"puuid": puuid, "game_name": game_name, "tag_line": tag_line, "region": region},
        max_attempts=cfg.max_attempts,
        priority="high",
    )
    # Set priority before publishing so clear_priority() by downstream
    # services cannot race against a not-yet-set priority key.
    await set_priority(r, puuid)
    entry_id = await publish(r, _STREAM, envelope)

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
    await r.zadd("players:all", {puuid: time.time()})

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


async def main(argv: list[str]) -> int:
    """Entry point. argv[1] = GameName#TagLine, argv[2] = region (default: na1)."""
    log = get_logger("seed")

    if len(argv) < 2:
        log.error("usage: python -m lol_seed <GameName#TagLine> [region]")
        return 1

    riot_id = argv[1]
    region = argv[2] if len(argv) > 2 else "na1"

    if "#" not in riot_id:
        log.error(
            "invalid Riot ID — expected GameName#TagLine",
            extra={"riot_id": riot_id},
        )
        return 1

    game_name, tag_line = riot_id.split("#", 1)

    cfg = Config()
    r = get_redis(cfg.redis_url)
    riot = RiotClient(cfg.riot_api_key, r=r)

    try:
        return await seed(r, riot, cfg, game_name, tag_line, region, log)
    finally:
        await r.aclose()
        await riot.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv)))
