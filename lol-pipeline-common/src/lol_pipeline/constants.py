"""Canonical stream and key names used across the pipeline.

Import from here instead of hard-coding string literals.  Services are free to
keep using their own local ``_STREAM_*`` aliases today — this module provides a
single source of truth when they're ready to migrate.
"""

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

# TTL for player-scoped Redis keys (player:{puuid}, player:matches:{puuid}).
# 30 days expressed as seconds.
PLAYER_DATA_TTL_SECONDS: int = 30 * 24 * 3600  # 2592000
