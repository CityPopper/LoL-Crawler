"""IMP-008: Config repr must not leak secret values."""

from __future__ import annotations

import pytest

# Env vars that may leak from other tests and pollute Config instantiation.
_CLEAN_ENV_VARS = ("RIOT_API_KEY", "REDIS_URL")


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove known config env vars so each test starts from a clean slate."""
    for var in _CLEAN_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestConfigReprSecrets:
    def test_repr_does_not_contain_riot_api_key(self, monkeypatch):
        """repr(Config()) must not contain the actual RIOT_API_KEY value."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-super-secret-key-12345")
        monkeypatch.setenv("REDIS_URL", "redis://secret-host:6379/0")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        r = repr(cfg)
        assert "RGAPI-super-secret-key-12345" not in r
        assert "secret-host" not in r

    def test_repr_does_not_contain_redis_url(self, monkeypatch):
        """repr(Config()) must not contain the actual REDIS_URL value."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://my-secret-redis:6379")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        r = repr(cfg)
        assert "my-secret-redis" not in r

    def test_non_secret_fields_still_in_repr(self, monkeypatch):
        """Non-secret config fields should remain in repr."""
        _clean_env(monkeypatch)
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        from lol_pipeline.config import Config

        cfg = Config(_env_file=None)  # type: ignore[call-arg]
        r = repr(cfg)
        # max_attempts and other non-secret fields should be present
        assert "max_attempts" in r
