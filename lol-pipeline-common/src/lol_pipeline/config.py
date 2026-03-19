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
