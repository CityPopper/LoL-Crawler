"""Canonical stream and key names used across the pipeline.

Import from here instead of hard-coding string literals.  Services are free to
keep using their own local ``_STREAM_*`` aliases today — this module provides a
single source of truth when they're ready to migrate.
"""

import os

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

# Streams that DLQ replay is allowed to target. Prevents corrupt or malicious
# DLQ entries from replaying to arbitrary Redis streams.
VALID_REPLAY_STREAMS: frozenset[str] = frozenset(
    {STREAM_PUUID, STREAM_MATCH_ID, STREAM_PARSE, STREAM_ANALYZE}
)

# TTL for player-scoped Redis keys (player:{puuid}, player:matches:{puuid}).
# 30 days expressed as seconds.
PLAYER_DATA_TTL_SECONDS: int = 30 * 24 * 3600  # 2592000

# TTL for champion aggregate stats keys. 90 days default; configurable via env.
CHAMPION_STATS_TTL_SECONDS: int = int(os.getenv("CHAMPION_STATS_TTL_SECONDS", str(90 * 24 * 3600)))
