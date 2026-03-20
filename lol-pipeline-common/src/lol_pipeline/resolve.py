"""Unified PUUID resolution — cache check, API fallback, error handling."""

from __future__ import annotations

import logging

import redis.asyncio as aioredis

from lol_pipeline.helpers import name_cache_key
from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    RiotClient,
    ServerError,
)

CACHE_TTL_S = 86400  # 24 hours


async def resolve_puuid(
    r: aioredis.Redis,
    riot: RiotClient,
    game_name: str,
    tag_line: str,
    region: str,
    log: logging.Logger,
) -> str | None:
    """Resolve a Riot ID to a PUUID, using the Redis name cache first.

    Returns the PUUID string on success, or None when resolution fails
    (404 not found, 403 auth error + system halt, 429/5xx transient errors).

    On 403, sets ``system:halted`` to ``"1"`` before returning None.
    """
    cache_key = name_cache_key(game_name, tag_line)
    cached: str | None = await r.get(cache_key)
    if cached:
        log.debug(
            "puuid resolved from cache",
            extra={"game_name": game_name, "tag_line": tag_line},
        )
        return cached

    try:
        account = await riot.get_account_by_riot_id(game_name, tag_line, region)
        puuid = str(account["puuid"])
        await r.set(cache_key, puuid, ex=CACHE_TTL_S)
        return puuid
    except NotFoundError:
        log.error(
            "player not found",
            extra={"game_name": game_name, "tag_line": tag_line},
        )
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
