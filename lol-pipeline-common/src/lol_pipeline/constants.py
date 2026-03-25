"""Canonical stream and key names used across the pipeline.

Import from here instead of hard-coding string literals.  Services are free to
keep using their own local ``_STREAM_*`` aliases today — this module provides a
single source of truth when they're ready to migrate.

TTL constants are read from :class:`lol_pipeline.config.Config` on first access
via a lazy helper so that ``Config()`` is only constructed once.
"""

from __future__ import annotations

import functools
import logging

from lol_pipeline.config import Config

_log = logging.getLogger(__name__)

# Game constants
RANKED_SOLO_QUEUE_ID: str = "420"

# Redis Streams
STREAM_PUUID: str = "stream:puuid"
STREAM_MATCH_ID: str = "stream:match_id"
STREAM_PARSE: str = "stream:parse"
STREAM_ANALYZE: str = "stream:analyze"
STREAM_DLQ: str = "stream:dlq"
STREAM_DLQ_ARCHIVE: str = "stream:dlq:archive"

# Redis keys
DISCOVER_PLAYERS_KEY: str = "discover:players"
SYSTEM_HALTED_KEY: str = "system:halted"
DELAYED_MESSAGES_KEY: str = "delayed:messages"
PLAYERS_ALL_KEY: str = "players:all"

# Streams that DLQ replay is allowed to target. Prevents corrupt or malicious
# DLQ entries from replaying to arbitrary Redis streams.
VALID_REPLAY_STREAMS: frozenset[str] = frozenset(
    {STREAM_PUUID, STREAM_MATCH_ID, STREAM_PARSE, STREAM_ANALYZE}
)


@functools.cache
def _config() -> Config | None:
    """Try to load Config; return None when required env vars are absent (e.g. tests)."""
    try:
        return Config()
    except Exception:
        _log.debug("Config() unavailable — using built-in defaults for TTL constants")
        return None


def _cfg_or_default(attr: str, default: int) -> int:
    """Read *attr* from Config if available, otherwise return *default*."""
    cfg = _config()
    return getattr(cfg, attr, default) if cfg is not None else default


# TTL for player-scoped Redis keys (player:{puuid}, player:matches:{puuid}).
# Default: 30 days (2592000s).
PLAYER_DATA_TTL_SECONDS: int = _cfg_or_default("player_data_ttl_seconds", 2592000)

# TTL for champion aggregate stats keys.
# Default: 90 days (7776000s).
CHAMPION_STATS_TTL_SECONDS: int = _cfg_or_default("champion_stats_ttl_seconds", 7776000)
