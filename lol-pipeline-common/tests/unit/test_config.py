"""Unit tests for lol_pipeline.config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestConfig:
    def test_required_fields_missing(self, monkeypatch):
        """Config raises ValidationError when required env vars are missing."""
        monkeypatch.delenv("RIOT_API_KEY", raising=False)
        monkeypatch.delenv("REDIS_URL", raising=False)
        # Force fresh import to avoid pydantic caching
        from lol_pipeline.config import Config

        with pytest.raises(ValidationError):
            Config(
                _env_file=None,  # type: ignore[call-arg]
            )

    def test_valid_config(self, monkeypatch):
        """Config loads successfully with required env vars."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test-key")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.riot_api_key == "RGAPI-test-key"
        assert cfg.redis_url == "redis://localhost:6379"

    def test_defaults(self, monkeypatch):
        """Config uses sane defaults for optional fields."""
        monkeypatch.setenv("RIOT_API_KEY", "test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.max_attempts == 5
        assert cfg.seed_cooldown_minutes == 30
        assert cfg.stream_ack_timeout == 60
        assert cfg.dlq_max_attempts == 3
        assert cfg.api_rate_limit_per_second == 20
        assert cfg.analyzer_lock_ttl_seconds == 300

    def test_override_from_env(self, monkeypatch):
        """Config can be overridden via env vars."""
        monkeypatch.setenv("RIOT_API_KEY", "test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("MAX_ATTEMPTS", "10")
        monkeypatch.setenv("SEED_COOLDOWN_MINUTES", "60")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.max_attempts == 10
        assert cfg.seed_cooldown_minutes == 60

    def test_empty_riot_api_key_rejected(self, monkeypatch):
        """Config rejects empty string for riot_api_key."""
        monkeypatch.setenv("RIOT_API_KEY", "")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        from lol_pipeline.config import Config

        with pytest.raises(ValidationError):
            Config(_env_file=None)  # type: ignore[call-arg]

    def test_empty_redis_url_rejected(self, monkeypatch):
        """Config rejects empty string for redis_url."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "")
        from lol_pipeline.config import Config

        with pytest.raises(ValidationError):
            Config(_env_file=None)  # type: ignore[call-arg]

    def test_extra_env_vars_ignored(self, monkeypatch):
        """Config ignores unknown env vars (extra='ignore')."""
        monkeypatch.setenv("RIOT_API_KEY", "test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv("TOTALLY_UNKNOWN_VAR", "whatever")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.riot_api_key == "test"

    @pytest.mark.parametrize(
        "field_name",
        [
            "SEED_COOLDOWN_MINUTES",
            "STREAM_ACK_TIMEOUT",
            "MAX_ATTEMPTS",
            "DLQ_MAX_ATTEMPTS",
            "DELAY_SCHEDULER_INTERVAL_MS",
            "ANALYZER_LOCK_TTL_SECONDS",
            "API_RATE_LIMIT_PER_SECOND",
            "DISCOVERY_POLL_INTERVAL_MS",
            "DISCOVERY_BATCH_SIZE",
        ],
    )
    def test_numeric_fields_reject_zero(self, monkeypatch, field_name):
        """CQ-6: numeric config fields must be >= 1."""
        monkeypatch.setenv("RIOT_API_KEY", "test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        monkeypatch.setenv(field_name, "0")
        from lol_pipeline.config import Config

        with pytest.raises(ValidationError):
            Config(_env_file=None)  # type: ignore[call-arg]
