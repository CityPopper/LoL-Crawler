"""Parser constants — extracted from main.py."""

from __future__ import annotations

_IN_STREAM = "stream:parse"
_OUT_STREAM = "stream:analyze"
_GROUP = "parsers"
_DISCOVER_KEY = "discover:players"
_ITEM_KEYS = [f"item{i}" for i in range(7)]

# Ranked solo queue ID.
_RANKED_QUEUE_ID = "420"

_GOLD_TIMELINE_MAX_FRAMES = 120
_KILL_EVENTS_MAX = 200

# Status set TTL: 7 days (aligned with match data TTL).
_STATUS_TTL = 604800
