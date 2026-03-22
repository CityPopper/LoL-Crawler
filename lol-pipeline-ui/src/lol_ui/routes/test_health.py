"""Tests for the /health route — enhanced health endpoint."""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

from lol_ui.main import app


@pytest.fixture
async def client():
    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.r = fake_r
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await fake_r.aclose()


class TestHealthRoute:
    """GET /health returns detailed system health."""

    @pytest.mark.asyncio
    async def test_returns_200(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_json(self, client):
        resp = await client.get("/health")
        assert resp.headers["content-type"].startswith("application/json")

    @pytest.mark.asyncio
    async def test_contains_status_ok(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_contains_redis_connected(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert data["redis"] == "connected"

    @pytest.mark.asyncio
    async def test_contains_system_halted(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert "system_halted" in data
        assert isinstance(data["system_halted"], bool)

    @pytest.mark.asyncio
    async def test_contains_streams_dict(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert "streams" in data
        assert isinstance(data["streams"], dict)

    @pytest.mark.asyncio
    async def test_contains_dlq_depth(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert "dlq_depth" in data
        assert isinstance(data["dlq_depth"], int)

    @pytest.mark.asyncio
    async def test_contains_redis_memory_mb(self, client):
        resp = await client.get("/health")
        data = resp.json()
        assert "redis_memory_mb" in data
        assert isinstance(data["redis_memory_mb"], float)

    @pytest.mark.asyncio
    async def test_backward_compatible__always_has_status(self, client):
        """Old consumers that check status=="ok" still work."""
        resp = await client.get("/health")
        data = resp.json()
        assert data["status"] == "ok"
