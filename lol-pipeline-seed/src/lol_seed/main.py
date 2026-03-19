"""Seed service — resolves a Riot ID to PUUID and enqueues it for crawling."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import UTC, datetime

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import set_priority
from lol_pipeline.redis_client import get_redis
from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    RiotClient,
    ServerError,
)
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


def _name_cache_key(game_name: str, tag_line: str) -> str:
    return f"player:name:{game_name.lower()}#{tag_line.lower()}"


async def _resolve_puuid(
    riot: RiotClient,
    r: aioredis.Redis,
    game_name: str,
    tag_line: str,
    region: str,
    log: logging.Logger,
) -> str | None:
    """Return PUUID or None on a handled error (already logged).

    Checks local Redis cache first to avoid an unnecessary Riot API call.
    """
    cache_key = _name_cache_key(game_name, tag_line)
    cached: str | None = await r.get(cache_key)
    if cached:
        log.debug("puuid resolved from cache", extra={"game_name": game_name, "tag_line": tag_line})
        return cached

    try:
        account = await riot.get_account_by_riot_id(game_name, tag_line, region)
        puuid = str(account["puuid"])
        await r.set(cache_key, puuid)
        return puuid
    except NotFoundError:
        log.error("player not found", extra={"game_name": game_name, "tag_line": tag_line})
        return None
    except AuthError:
        await r.set("system:halted", "1")
        log.critical(
            "Riot API key rejected (403) — system halted",
            extra={"game_name": game_name},
        )
        return None
    except (RateLimitError, ServerError) as exc:
        log.error(
            "Riot API error — retry later",
            extra={"error": str(exc), "game_name": game_name},
        )
        return None


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
    puuid = await _resolve_puuid(riot, r, game_name, tag_line, region, log)
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
    entry_id = await publish(r, _STREAM, envelope)
    await set_priority(r, puuid)

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
