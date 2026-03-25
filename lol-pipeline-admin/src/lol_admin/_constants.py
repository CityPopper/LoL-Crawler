"""Shared constants for admin CLI commands."""

from __future__ import annotations

from lol_pipeline.constants import (
    DELAYED_MESSAGES_KEY,
    STREAM_ANALYZE,
    STREAM_DLQ,
    STREAM_MATCH_ID,
    STREAM_PARSE,
    STREAM_PUUID,
)
from lol_pipeline.streams import DEFAULT_STREAM_MAXLEN

# Re-export canonical constants so existing intra-service imports keep working.
_STREAM_PUUID = STREAM_PUUID
_STREAM_MATCH_ID = STREAM_MATCH_ID
_STREAM_PARSE = STREAM_PARSE
_STREAM_ANALYZE = STREAM_ANALYZE
_STREAM_DLQ = STREAM_DLQ
_DELAYED_MESSAGES = DELAYED_MESSAGES_KEY
_DEFAULT_MAXLEN = DEFAULT_STREAM_MAXLEN
