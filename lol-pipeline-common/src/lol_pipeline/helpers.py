"""Shared helpers — DRY utilities used by multiple services."""

from __future__ import annotations

import re

import redis.asyncio as aioredis

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
