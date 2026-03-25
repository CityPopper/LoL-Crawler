"""Configuration: env var loading and validation via pydantic-settings."""

import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """All pipeline configuration sourced exclusively from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    riot_api_key: str = Field(min_length=1)
    redis_url: str = Field(min_length=1)
    raw_store_backend: str = "redis"
    raw_store_url: str = ""
    seed_cooldown_minutes: int = Field(default=30, ge=1)
    stream_ack_timeout: int = Field(default=60, ge=1)
    max_attempts: int = Field(default=5, ge=1)
    dlq_max_attempts: int = Field(default=3, ge=1)
    delay_scheduler_interval_ms: int = Field(default=500, ge=1)
    analyzer_lock_ttl_seconds: int = Field(default=300, ge=1)
    api_rate_limit_per_second: int = Field(default=20, ge=1)
    match_data_dir: str = ""  # if set, raw match JSON is also persisted to disk
    discovery_poll_interval_ms: int = Field(default=5000, ge=1)
    discovery_batch_size: int = Field(default=10, ge=1)
    match_data_ttl_seconds: int = Field(default=604800, ge=1)
    max_discover_players: int = Field(default=50000, ge=1)
    players_all_max: int = Field(default=50000, ge=1)
    player_matches_max: int = Field(default=500, ge=1)
    match_id_backpressure_threshold: int = Field(default=5000, ge=0)
    log_dir: str = ""
    # Rank data
    fetch_rank_on_crawl: bool = True  # fetch league-v4 rank after crawling
    # Timeline
    fetch_timeline: bool = False  # fetch match timeline (doubles API usage)
    # Matchup / ban tracking
    track_matchups: bool = True  # compute head-to-head lane matchup stats
    track_bans: bool = True  # track champion ban rates
    # Dedup
    seen_matches_ttl_seconds: int = Field(default=604800, ge=1)  # 7 days
    # Activity-rate discovery
    # Weight for activity rate in discovery scoring
    activity_rate_weight: float = Field(default=1.0, ge=0.0)
    # op.gg integration
    opgg_enabled: bool = False  # try op.gg first, fall back to Riot on failure
    opgg_rate_limit_per_second: int = Field(default=2, ge=1)
    opgg_rate_limit_long: int = Field(default=30, ge=1)
    opgg_match_data_dir: str = ""
    opgg_api_key: str | None = None
    # PRIN-COM-1: formerly raw os.getenv() / hardcoded literals
    player_data_ttl_seconds: int = Field(default=2592000, ge=1)  # 30 days
    champion_stats_ttl_seconds: int = Field(default=7776000, ge=1)  # 90 days
    raw_store_ttl_seconds: int = Field(default=86400, ge=1)  # 24h
    redis_socket_timeout: float = Field(default=30.0, gt=0)
    redis_connect_timeout: float = Field(default=10.0, gt=0)
    log_level: str = "INFO"
    max_handler_retries: int = Field(default=3, ge=1)
    max_nack_attempts: int = Field(default=3, ge=1)
    retry_key_ttl: int = Field(default=604800, ge=1)  # 7 days
    priority_key_ttl_seconds: int = Field(default=86400, ge=1)  # 24h
    # PRIN-CRL-1: Crawler operational params (formerly hardcoded in _constants.py)
    crawler_page_size: int = Field(default=100, ge=1)
    crawler_rank_ttl: int = Field(default=86400, ge=1)  # 24h
    crawler_cursor_ttl: int = Field(default=600, ge=1)  # 10 min
    crawler_rank_history_max: int = Field(default=500, ge=1)
    crawler_cooldown_high_rate: int = Field(default=5, ge=1)
    crawler_cooldown_high_hours: int = Field(default=2, ge=1)
    crawler_cooldown_mid_rate: int = Field(default=1, ge=0)
    crawler_cooldown_mid_hours: int = Field(default=6, ge=1)
    crawler_cooldown_low_hours: int = Field(default=24, ge=1)
    # PRIN-SCH-2: Delay-scheduler operational params (formerly hardcoded constants)
    delay_scheduler_batch_size: int = Field(default=100, ge=1)
    delay_scheduler_max_member_failures: int = Field(default=10, ge=1)
    delay_scheduler_circuit_open_ttl_s: int = Field(default=300, ge=1)
    # PRIN-DIS-1: Discovery operational params (formerly hardcoded constants)
    discovery_idle_cutoff_days: int = Field(default=3, ge=1)
    default_region: str = "na1"
    # PRIN-REC-1: Recovery operational params (formerly hardcoded constants)
    recovery_claim_idle_ms: int = Field(default=60_000, ge=1)
    recovery_backoff_ms: list[int] = Field(
        default=[5_000, 15_000, 60_000, 300_000],
    )
    recovery_halt_sleep_s: float = Field(default=5.0, gt=0)
    recovery_archive_maxlen: int = Field(default=50_000, ge=1)
    recovery_count: int = Field(default=10, ge=1)
    recovery_block_ms: int = Field(default=5000, ge=1)
    # PRIN-UI-1: UI operational params (formerly hardcoded)
    ddragon_timeout_s: float = Field(default=5.0, gt=0)
    stats_fragment_cache_ttl_s: int = Field(default=21600, ge=1)  # 6 hours
    port: int = Field(default=8080, ge=1)

    @model_validator(mode="after")
    def _derive_opgg_match_data_dir(self) -> "Config":
        """Fall back to match_data_dir/opgg when opgg_match_data_dir is unset."""
        if not self.opgg_match_data_dir and self.match_data_dir:
            self.opgg_match_data_dir = os.path.join(self.match_data_dir, "opgg")
        return self
