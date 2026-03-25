"""Parser constants — extracted from main.py."""

from __future__ import annotations

from lol_pipeline.constants import DISCOVER_PLAYERS_KEY

_IN_STREAM = "stream:parse"
_OUT_STREAM = "stream:analyze"
_GROUP = "parsers"
_DISCOVER_KEY = DISCOVER_PLAYERS_KEY
_ITEM_KEYS = [f"item{i}" for i in range(7)]

# Ranked solo queue ID.
_RANKED_QUEUE_ID = "420"

_GOLD_TIMELINE_MAX_FRAMES = 120
_KILL_EVENTS_MAX = 200
