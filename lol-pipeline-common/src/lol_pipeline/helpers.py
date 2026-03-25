"""Shared helpers — DRY utilities used by multiple services."""

from __future__ import annotations

import collections.abc
import logging
import re
import time
from datetime import UTC, datetime

import redis.asyncio as aioredis

from lol_pipeline.constants import PLAYER_DATA_TTL_SECONDS
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.riot_api import AuthError, NotFoundError, RateLimitError, ServerError
from lol_pipeline.streams import ack, nack_to_dlq

# Riot API enforced limits (per their developer docs):
#   game_name: max 16 characters (Riot ID game name)
#   tag_line:  max 5 characters (Riot ID tagline, e.g. "NA1")
# We use generous upper bounds for validation to reject clearly abusive input
# while still allowing edge-cases from Riot's API.
_MAX_GAME_NAME_LEN = 64  # generous upper bound; Riot display name ≤16 chars
_MAX_TAG_LINE_LEN = 16  # generous upper bound; Riot tagline ≤5 chars
_MAX_SANITIZED_LEN = 16  # truncation limit after sanitization
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize(value: str, max_len: int = _MAX_SANITIZED_LEN) -> str:
    """Strip control characters and truncate to *max_len*."""
    cleaned = _CONTROL_CHAR_RE.sub("", value)
    return cleaned[:max_len]


def validate_name_lengths(game_name: str, tag_line: str) -> None:
    """Raise ValueError if game_name or tag_line exceeds Riot's length limits.

    This guards against Redis key injection via unbounded user input.
    Call this at service boundaries (seed, admin, UI) before building cache keys.
    """
    if len(game_name) > _MAX_GAME_NAME_LEN:
        msg = f"game_name exceeds maximum length ({len(game_name)} > {_MAX_GAME_NAME_LEN})"
        raise ValueError(msg)
    if len(tag_line) > _MAX_TAG_LINE_LEN:
        msg = f"tag_line exceeds maximum length ({len(tag_line)} > {_MAX_TAG_LINE_LEN})"
        raise ValueError(msg)


def name_cache_key(game_name: str, tag_line: str) -> str:
    """Build the Redis key for the player name->PUUID cache.

    Used by seed, admin, and UI services.
    Validates length limits, strips control/null bytes, truncates to 16 chars each.

    Raises:
        ValueError: if game_name > 64 chars or tag_line > 16 chars.
    """
    validate_name_lengths(game_name, tag_line)
    safe_name = _sanitize(game_name).lower()
    safe_tag = _sanitize(tag_line).lower()
    return f"player:name:{safe_name}#{safe_tag}"


async def is_system_halted(r: aioredis.Redis) -> bool:
    """Return True if the global halt flag is set.

    Used by crawler, fetcher, parser, analyzer handlers as a pre-check.
    """
    return bool(await r.get("system:halted"))


async def register_player(  # noqa: PLR0913
    r: aioredis.Redis,
    *,
    puuid: str,
    region: str,
    game_name: str,
    tag_line: str,
    players_all_max: int,
    transaction: bool = False,
    extra_ops: collections.abc.Callable[[aioredis.client.Pipeline], None] | None = None,
) -> None:
    """Write player hash, set TTL, add to players:all ZSET, and trim.

    This is the shared 4-step registration sequence used by seed, discovery,
    and any other service that introduces a player into the pipeline.

    When *transaction* is True, the pipeline executes as a MULTI/EXEC block.
    When *extra_ops* is provided, it is called with the pipeline object so
    callers (e.g. discovery) can append service-specific operations that
    execute atomically within the same pipeline.
    """
    now_iso = datetime.now(tz=UTC).isoformat()
    player_key = f"player:{puuid}"
    async with r.pipeline(transaction=transaction) as pipe:
        pipe.hset(
            player_key,
            mapping={
                "game_name": game_name,
                "tag_line": tag_line,
                "region": region,
                "seeded_at": now_iso,
            },
        )
        pipe.expire(player_key, PLAYER_DATA_TTL_SECONDS)
        pipe.zadd("players:all", {puuid: time.time()})
        pipe.zremrangebyrank("players:all", 0, -(players_all_max + 1))
        if extra_ops is not None:
            extra_ops(pipe)
        await pipe.execute()


async def handle_riot_api_error(
    r: aioredis.Redis,
    *,
    exc: NotFoundError | AuthError | RateLimitError | ServerError,
    envelope: MessageEnvelope,
    msg_id: str,
    failed_by: str,
    in_stream: str,
    group: str,
    log: logging.Logger | None = None,
) -> str:
    """Route a Riot API error to the correct outcome.

    Returns a string tag indicating the action taken:
    - ``"discarded"`` — 404, message ACKed
    - ``"halted"`` — 403, system:halted set, message NOT ACKed
    - ``"dlq"`` — 429/5xx, nacked to DLQ and ACKed
    """
    if isinstance(exc, NotFoundError):
        if log:
            log.info("not found (404) — discarding", extra={"msg_id": msg_id})
        await ack(r, in_stream, group, msg_id)
        return "discarded"

    if isinstance(exc, AuthError):
        await r.set("system:halted", "1")
        if log:
            log.critical("API key rejected (403) — system halted")
        return "halted"

    # RateLimitError | ServerError
    fc = "http_429" if isinstance(exc, RateLimitError) else "http_5xx"
    ram = exc.retry_after_ms if isinstance(exc, RateLimitError) else None
    if log:
        log.error(
            "Riot API error \u2014 routing to DLQ for retry",
            extra={"error": str(exc), "failure_code": fc},
        )
    await nack_to_dlq(
        r,
        envelope,
        failure_code=fc,
        failed_by=failed_by,
        original_message_id=msg_id,
        retry_after_ms=ram,
    )
    await ack(r, in_stream, group, msg_id)
    return "dlq"
