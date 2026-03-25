"""Configuration: env var loading and validation via pydantic-settings."""

from pydantic import Field
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
