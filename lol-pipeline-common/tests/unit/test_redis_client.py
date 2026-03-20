"""Unit tests for lol_pipeline.redis_client."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.redis_client import get_redis, health_check


class TestGetRedis:
    def test_returns_redis_instance(self):
        r = get_redis("redis://localhost:6379")
        assert r is not None

    def test_decode_responses_enabled(self):
        r = get_redis("redis://localhost:6379")
        pool = r.connection_pool
        kwargs = pool.connection_kwargs
        assert kwargs.get("decode_responses") is True

    def test_socket_timeout_set(self):
        """B11: socket_timeout is set to prevent hung connections."""
        r = get_redis("redis://localhost:6379")
        pool = r.connection_pool
        kwargs = pool.connection_kwargs
        assert kwargs.get("socket_timeout") == 30.0

    def test_socket_connect_timeout_set(self):
        """B11: socket_connect_timeout is set to prevent hung connections."""
        r = get_redis("redis://localhost:6379")
        pool = r.connection_pool
        kwargs = pool.connection_kwargs
        assert kwargs.get("socket_connect_timeout") == 10.0

    def test_socket_timeout_env_override(self, monkeypatch):
        """B11: REDIS_SOCKET_TIMEOUT env var overrides the default."""
        monkeypatch.setenv("REDIS_SOCKET_TIMEOUT", "15")
        # Reimport to pick up the new env var
        import importlib

        import lol_pipeline.redis_client as mod

        importlib.reload(mod)
        try:
            r = mod.get_redis("redis://localhost:6379")
            pool = r.connection_pool
            kwargs = pool.connection_kwargs
            assert kwargs.get("socket_timeout") == 15.0
        finally:
            # Restore default
            monkeypatch.delenv("REDIS_SOCKET_TIMEOUT", raising=False)
            importlib.reload(mod)

    def test_connect_timeout_env_override(self, monkeypatch):
        """B11: REDIS_CONNECT_TIMEOUT env var overrides the default."""
        monkeypatch.setenv("REDIS_CONNECT_TIMEOUT", "5")
        import importlib

        import lol_pipeline.redis_client as mod

        importlib.reload(mod)
        try:
            r = mod.get_redis("redis://localhost:6379")
            pool = r.connection_pool
            kwargs = pool.connection_kwargs
            assert kwargs.get("socket_connect_timeout") == 5.0
        finally:
            monkeypatch.delenv("REDIS_CONNECT_TIMEOUT", raising=False)
            importlib.reload(mod)


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_redis(self):
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        assert await health_check(r) is True
        await r.aclose()
