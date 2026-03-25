"""Discovery constants — extracted from main.py."""

from __future__ import annotations

from lol_pipeline.constants import DISCOVER_PLAYERS_KEY

_STREAM_PUUID = "stream:puuid"
_DISCOVER_KEY = DISCOVER_PLAYERS_KEY
_DELAYED_KEY = "delayed:messages"
_PIPELINE_STREAMS = (
    "stream:puuid",
    "stream:match_id",
    "stream:parse",
    "stream:analyze",
    "stream:dlq",
)
