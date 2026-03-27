"""Configuration for the rate-limiter service — env vars only."""

from __future__ import annotations

import os


class Config:
    """Rate-limiter configuration sourced from environment variables."""

    def __init__(self) -> None:
        self.redis_url: str = os.environ.get("RATE_LIMITER_REDIS_URL", "redis://redis:6379")
        self.short_limit: int = int(os.environ.get("RATELIMIT_RIOT_SHORT_LIMIT", "20"))
        self.short_window_ms: int = int(os.environ.get("RATELIMIT_RIOT_SHORT_WINDOW_MS", "1000"))
        self.long_limit: int = int(os.environ.get("RATELIMIT_RIOT_LONG_LIMIT", "100"))
        self.long_window_ms: int = int(os.environ.get("RATELIMIT_RIOT_LONG_WINDOW_MS", "120000"))
        raw_sources = os.environ.get(
            "RATELIMIT_KNOWN_SOURCES",
            "riot,riot:americas,riot:europe,riot:asia,riot:sea,fetcher,crawler,discovery,opgg,opgg:ui,riot:ui",
        )
        self.known_sources: list[str] = [s.strip() for s in raw_sources.split(",") if s.strip()]
        self.opgg_short_limit: int = int(os.environ.get("RATELIMIT_OPGG_SHORT_LIMIT", "2"))
        self.opgg_long_limit: int = int(os.environ.get("RATELIMIT_OPGG_LONG_LIMIT", "100"))
        self.opgg_ui_short_limit: int = int(os.environ.get("RATELIMIT_OPGG_UI_SHORT_LIMIT", "2"))
        self.opgg_ui_long_limit: int = int(os.environ.get("RATELIMIT_OPGG_UI_LONG_LIMIT", "20"))
        self.riot_ui_short_limit: int = int(os.environ.get("RATELIMIT_RIOT_UI_SHORT_LIMIT", "5"))
        self.riot_ui_long_limit: int = int(os.environ.get("RATELIMIT_RIOT_UI_LONG_LIMIT", "10"))
        # IMP-048: Shared secret for authenticating internal callers.
        # Empty string means auth is disabled (dev/test convenience).
        self.secret: str = os.environ.get("RATE_LIMITER_SECRET", "")
