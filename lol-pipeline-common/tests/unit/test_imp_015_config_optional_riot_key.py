"""IMP-015: Config can be instantiated without RIOT_API_KEY."""

from __future__ import annotations

import pytest

# Env vars that may leak from other tests and pollute Config instantiation.
_CLEAN_ENV_VARS = ("RIOT_API_KEY", "REDIS_URL")


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove known config env vars so each test starts from a clean slate."""
    for var in _CLEAN_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestConfigOptionalRiotKey:
    def test_config_without_riot_api_key(self, monkeypatch):
        """Config can be instantiated with RIOT_API_KEY unset."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.riot_api_key == ""

    def test_config_with_riot_api_key(self, monkeypatch):
        """Config still accepts RIOT_API_KEY when provided."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        assert cfg.riot_api_key == "RGAPI-test"

    def test_validate_for_riot_api_raises_when_unset(self, monkeypatch):
        """validate_for_riot_api() raises ValueError when key is empty."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        with pytest.raises(ValueError, match="RIOT_API_KEY is required"):
            cfg.validate_for_riot_api()

    def test_validate_for_riot_api_passes_when_set(self, monkeypatch):
        """validate_for_riot_api() does not raise when key is present."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-valid-key")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        cfg.validate_for_riot_api()  # should not raise
