"""Discovery constants — extracted from main.py."""

from __future__ import annotations

from lol_pipeline.constants import (
    DISCOVER_PLAYERS_KEY,
    STREAM_ANALYZE,
    STREAM_DLQ,
    STREAM_MATCH_ID,
    STREAM_PARSE,
    STREAM_PUUID,
)

_DISCOVER_KEY = DISCOVER_PLAYERS_KEY
_PIPELINE_STREAMS = (
    STREAM_PUUID,
    STREAM_MATCH_ID,
    STREAM_PARSE,
    STREAM_ANALYZE,
    STREAM_DLQ,
)
