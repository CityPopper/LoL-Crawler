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
    seed_cooldown_minutes: int = 30
    stream_ack_timeout: int = 60
    max_attempts: int = 5
    dlq_max_attempts: int = 3
    delay_scheduler_interval_ms: int = 500
    analyzer_lock_ttl_seconds: int = 300
    api_rate_limit_per_second: int = 20
    match_data_dir: str = ""  # if set, raw match JSON is also persisted to disk
    discovery_poll_interval_ms: int = 5000  # how often discovery checks for idle pipeline
    discovery_batch_size: int = 10  # players promoted per idle poll
