"""Module-level constants extracted from main.py."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from lol_pipeline.constants import VALID_REPLAY_STREAMS

# ---------------------------------------------------------------------------
# Stream / validation
# ---------------------------------------------------------------------------

_STREAM_PUUID = "stream:puuid"
_VALID_REPLAY_STREAMS = VALID_REPLAY_STREAMS
_PUUID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
_MATCH_ID_RE = re.compile(r"^[A-Z]{2,4}\d?_\d{1,15}$")
_CHAMPION_NAME_RE = re.compile(r"^[a-zA-Z0-9 '.&-]{1,50}$")
_PATCH_RE = re.compile(r"^\d{1,2}\.\d{1,2}$")
_MATCHUP_ROLES = frozenset({"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"})
_STREAM_ENTRY_ID_RE = re.compile(r"^\d+-\d+$")

# ---------------------------------------------------------------------------
# Name cache
# ---------------------------------------------------------------------------

_NAME_CACHE_INDEX = "name_cache:index"
_NAME_CACHE_MAX = 10_000
_AUTOSEED_COOLDOWN_S = 300  # 5 minutes

# ---------------------------------------------------------------------------
# Halt banner
# ---------------------------------------------------------------------------

_HALT_BANNER = (
    '<div class="banner banner--error" role="alert">&#9888; Pipeline is HALTED '
    "&mdash; all consumers are stopped. "
    "To recover: fix the API key in <code>.env</code>, restart with "
    "<code>just up</code>, then run <code>just admin system-resume</code> to resume.</div>"
)

# ---------------------------------------------------------------------------
# Message / DLQ
# ---------------------------------------------------------------------------

_VALID_MSG_CLASSES = frozenset({"", "success", "warning", "error"})

_DLQ_DEFAULT_PER_PAGE = 25
_DLQ_MAX_PER_PAGE = 50

# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------

_REGIONS = [
    "na1",
    "br1",
    "la1",
    "la2",
    "euw1",
    "eun1",
    "tr1",
    "ru",
    "kr",
    "jp1",
    "oc1",
    "ph2",
    "sg2",
    "th2",
    "tw2",
    "vn2",
]

_REGIONS_SET = frozenset(_REGIONS)

# ---------------------------------------------------------------------------
# Badge / stats
# ---------------------------------------------------------------------------

_BADGE_VARIANTS = frozenset({"success", "error", "warning", "info", "muted"})

# Stream depth badge thresholds
_DEPTH_BADGE_BUSY_THRESHOLD = 100
_DEPTH_BADGE_BACKLOG_THRESHOLD = 1000

# KDA ratio display threshold (ratio >= this value gets "good" styling)
_KDA_RATIO_GOOD_THRESHOLD = 3.0

# Time-ago display thresholds (seconds)
_TIME_AGO_HOUR_S = 3600
_TIME_AGO_DAY_S = 86400

_STATS_ORDER = [
    "total_games",
    "total_wins",
    "win_rate",
    "total_kills",
    "total_deaths",
    "total_assists",
    "kda",
    "avg_kills",
    "avg_deaths",
    "avg_assists",
]

_STATS_ORDER_SET = frozenset(_STATS_ORDER)

# ---------------------------------------------------------------------------
# Champion diversity
# ---------------------------------------------------------------------------

_DIVERSITY_MIN_GAMES = 20

_DIVERSITY_LABELS: list[tuple[float, str]] = [
    (20.0, "OTP"),
    (40.0, "Focused"),
    (60.0, "Moderate"),
    (80.0, "Diverse"),
    (100.1, "Flex"),
]

# ---------------------------------------------------------------------------
# Ranked splits
# ---------------------------------------------------------------------------

# League of Legends ranked split start dates (UTC).
# Each split is ~4 months. Update when Riot announces new dates.
_RANKED_SPLIT_STARTS: list[tuple[str, datetime]] = [
    ("2025 Split 1", datetime(2025, 1, 8, tzinfo=UTC)),
    ("2025 Split 2", datetime(2025, 5, 14, tzinfo=UTC)),
    ("2025 Split 3", datetime(2025, 9, 17, tzinfo=UTC)),
    ("2026 Split 1", datetime(2026, 1, 8, tzinfo=UTC)),
    ("2026 Split 2", datetime(2026, 5, 6, tzinfo=UTC)),
    ("2026 Split 3", datetime(2026, 9, 2, tzinfo=UTC)),
]

_SPLIT_MATCH_LIMIT = 200

# ---------------------------------------------------------------------------
# Breakdown
# ---------------------------------------------------------------------------

_BREAKDOWN_MATCH_COUNT = 50

# ---------------------------------------------------------------------------
# Playstyle
# ---------------------------------------------------------------------------

_PLAYSTYLE_MIN_GAMES = 5

# Each rule: (tag_name, css_color, field, operator, threshold)
# "or" rules are expressed as two entries with the same tag name; the caller
# de-duplicates.  Operator semantics: "ge" = >=, "le" = <=.
_PLAYSTYLE_RULES: list[tuple[str, str, str, str, float]] = [
    ("Aggressive", "#e84057", "avg_kills", "ge", 8.0),
    ("Aggressive", "#e84057", "ka_sum", "ge", 15.0),
    ("Team Fighter", "#5383e8", "avg_assists", "ge", 10.0),
    ("Deathless", "#2daf6f", "avg_deaths", "le", 3.0),
    ("KDA King", "#ffdc00", "kda", "ge", 4.0),
    ("Slayer", "#ff6b35", "avg_kills", "ge", 10.0),
    ("Winning Machine", "#9b59b6", "win_rate", "ge", 0.6),
]

# ---------------------------------------------------------------------------
# Tilt / streak
# ---------------------------------------------------------------------------

_TILT_RECENT_COUNT = 20
_TILT_RECENT_KDA_COUNT = 5
_TILT_KDA_THRESHOLD = 0.20
_TILT_MIN_STREAK_DISPLAY = 3

# ---------------------------------------------------------------------------
# Match history
# ---------------------------------------------------------------------------

_MATCH_PAGE_SIZE = 20

# Badge color definitions (CSS color, text color)
_MATCH_BADGE_COLORS: dict[str, tuple[str, str]] = {
    "gold": ("#ffd700", "#111"),
    "red": ("#ff4136", "#fff"),
    "green": ("#2daf6f", "#111"),
    "blue": ("#4fc3f7", "#111"),
}

# Match badge achievement thresholds
_BADGE_PENTA_MIN = 1
_BADGE_KDA_THRESHOLD = 5.0
_BADGE_CS_PER_MIN_THRESHOLD = 8.0
_BADGE_CS_MIN_TIME_PLAYED = 60

# ---------------------------------------------------------------------------
# Players page
# ---------------------------------------------------------------------------

_PLAYERS_PAGE_SIZE = 25

_PLAYERS_SORT_OPTIONS = frozenset({"date", "name", "region"})

_PlayerRow = tuple[str, str, str, str]  # (game_name, tag_line, region, seeded_at)

# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------

_STREAM_KEYS = [
    "stream:puuid",
    "stream:match_id",
    "stream:parse",
    "stream:analyze",
    "stream:dlq",
    "stream:dlq:archive",
]

# ---------------------------------------------------------------------------
# Log viewer
# ---------------------------------------------------------------------------

_LOG_LINES = 50
_LOG_LEVEL_CSS = {
    "CRITICAL": "log-critical",
    "ERROR": "log-error",
    "WARNING": "log-warning",
    "DEBUG": "log-debug",
}

_EST_BYTES_PER_LOG_LINE = 600  # heuristic for JSON structured log lines

# ---------------------------------------------------------------------------
# Champions / tier list
# ---------------------------------------------------------------------------

_CHAMPION_ROLES = ["TOP", "JUNGLE", "MID", "BOTTOM", "UTILITY"]
_CHAMPION_ROLES_SET = frozenset(_CHAMPION_ROLES)
_CHAMPION_ROLE_LABELS: dict[str, str] = {
    "": "ALL",
    "TOP": "TOP",
    "JUNGLE": "JGL",
    "MID": "MID",
    "BOTTOM": "BOT",
    "UTILITY": "SUP",
}

_PBI_MIN_GAMES = 20
_DELTA_MIN_GAMES = 10
_DELTA_DISPLAY_THRESHOLD = 0.005

# Tier percentile cutoffs: (max_percentile, tier_letter)
# Percentile 0.0 = best. Rows beyond the last cutoff get tier "D".
_TIER_PERCENTILE_CUTOFFS: list[tuple[float, str]] = [
    (0.05, "S"),
    (0.20, "A"),
    (0.50, "B"),
    (0.80, "C"),
]

# Win-rate color thresholds for champion tier table
_WR_COLOR_HIGH_THRESHOLD = 52
_WR_COLOR_MID_THRESHOLD = 48

# Win-rate threshold for rank card (>= means positive, < means negative)
_RANK_WR_THRESHOLD = 50

_TIER_COLORS: dict[str, str] = {
    "S": "#d4a017",
    "A": "#2d8a4e",
    "B": "#3b82f6",
    "C": "#888",
    "D": "#c0392b",
}
