"""Crawler constants — extracted from main.py."""

from __future__ import annotations

_IN_STREAM = "stream:puuid"
_OUT_STREAM = "stream:match_id"
_GROUP = "crawlers"
_PAGE_SIZE = 100
_RANK_HISTORY_MAX = 500

# Rank data TTL: 24 hours.
_RANK_TTL = 86400

# Crawl cursor TTL: 10 minutes.
_CURSOR_TTL = 600

# Activity-based recrawl cooldown thresholds.
# rate > 5 games/day  -> 2 hours
# rate > 1 game/day   -> 6 hours
# else                -> 24 hours
_COOLDOWN_HIGH_RATE = 5
_COOLDOWN_HIGH_HOURS = 2
_COOLDOWN_MID_RATE = 1
_COOLDOWN_MID_HOURS = 6
_COOLDOWN_LOW_HOURS = 24
