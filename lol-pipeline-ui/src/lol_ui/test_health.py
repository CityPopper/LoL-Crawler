"""Tests for health.py — health status dict builder."""

from __future__ import annotations

import pytest

from lol_ui.constants import _STREAM_KEYS
from lol_ui.health import _health_status


class TestHealthStatus:
    """_health_status returns a dict with Redis health info."""

    @pytest.mark.asyncio
    async def test_returns_ok_status(self, r):
        result = await _health_status(r)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_returns_redis_connected(self, r):
        result = await _health_status(r)
        assert result["redis"] == "connected"

    @pytest.mark.asyncio
    async def test_system_halted_false_when_not_set(self, r):
        result = await _health_status(r)
        assert result["system_halted"] is False

    @pytest.mark.asyncio
    async def test_system_halted_true_when_set(self, r):
        await r.set("system:halted", "1")
        result = await _health_status(r)
        assert result["system_halted"] is True

    @pytest.mark.asyncio
    async def test_streams_dict_has_all_stream_keys(self, r):
        result = await _health_status(r)
        streams = result["streams"]
        assert isinstance(streams, dict)
        for key in _STREAM_KEYS:
            assert key in streams

    @pytest.mark.asyncio
    async def test_streams_dict_has_delayed_messages(self, r):
        result = await _health_status(r)
        streams: dict[str, int] = result["streams"]  # type: ignore[assignment]
        assert "delayed:messages" in streams

    @pytest.mark.asyncio
    async def test_streams_empty_by_default(self, r):
        result = await _health_status(r)
        streams: dict[str, int] = result["streams"]  # type: ignore[assignment]
        for key in _STREAM_KEYS:
            assert streams[key] == 0

    @pytest.mark.asyncio
    async def test_dlq_depth_reflects_stream_dlq(self, r):
        await r.xadd("stream:dlq", {"data": "test"})
        result = await _health_status(r)
        assert result["dlq_depth"] == 1

    @pytest.mark.asyncio
    async def test_redis_memory_mb_is_float(self, r):
        result = await _health_status(r)
        assert isinstance(result["redis_memory_mb"], float)

    @pytest.mark.asyncio
    async def test_redis_memory_mb_non_negative(self, r):
        result = await _health_status(r)
        mem = float(result["redis_memory_mb"])  # type: ignore[arg-type]
        assert mem >= 0.0

    @pytest.mark.asyncio
    async def test_stream_depth_reflects_xadd(self, r):
        await r.xadd("stream:puuid", {"data": "test1"})
        await r.xadd("stream:puuid", {"data": "test2"})
        result = await _health_status(r)
        streams: dict[str, int] = result["streams"]  # type: ignore[assignment]
        assert streams["stream:puuid"] == 2

    @pytest.mark.asyncio
    async def test_delayed_messages_reflects_zadd(self, r):
        await r.zadd("delayed:messages", {"msg1": 1.0, "msg2": 2.0})
        result = await _health_status(r)
        streams: dict[str, int] = result["streams"]  # type: ignore[assignment]
        assert streams["delayed:messages"] == 2
