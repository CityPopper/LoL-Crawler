"""Discovery constants — extracted from main.py."""

from __future__ import annotations

_STREAM_PUUID = "stream:puuid"
_DISCOVER_KEY = "discover:players"
_DELAYED_KEY = "delayed:messages"
_PIPELINE_STREAMS = (
    "stream:puuid",
    "stream:match_id",
    "stream:parse",
    "stream:analyze",
    "stream:dlq",
)
