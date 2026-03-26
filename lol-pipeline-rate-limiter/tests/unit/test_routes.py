"""Unit tests for rate-limiter HTTP routes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

try:
    import lupa  # noqa: F401

    _LUPA_AVAILABLE = True
except ImportError:
    _LUPA_AVAILABLE = False

from lol_rate_limiter.config import Config
from lol_rate_limiter.main import app


@pytest.fixture
def mock_redis():
    """Create a mock Redis instance with default behaviors."""
    r = AsyncMock()
    r.zcard = AsyncMock(return_value=0)
    r.eval = AsyncMock(return_value=1)
    r.set = AsyncMock(return_value=True)
    r.aclose = AsyncMock()
    return r


@pytest.fixture
async def client(mock_redis):
    """AsyncClient wired to the FastAPI app with mocked Redis.

    Directly sets app.state to bypass the lifespan (which would try to
    connect to a real Redis).  ASGITransport does not invoke lifespans.
    """
    app.state.cfg = Config()
    app.state.r = mock_redis
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_unknown_source_returns_404(client):
    resp = await client.post(
        "/token/acquire",
        json={"source": "unknown", "endpoint": "match"},
    )
    assert resp.status_code == 404
    assert resp.json() == {"error": "unknown source"}


@pytest.mark.asyncio
async def test_known_source_granted_when_empty(client, mock_redis):
    mock_redis.eval = AsyncMock(return_value=1)
    resp = await client.post(
        "/token/acquire",
        json={"source": "fetcher", "endpoint": "match"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["granted"] is True
    assert data["retry_after_ms"] is None


@pytest.mark.asyncio
async def test_headers_route_accepted(client):
    resp = await client.post(
        "/headers",
        json={
            "source": "fetcher",
            "rate_limit": "20:1,100:120",
            "rate_limit_count": "15:1,42:120",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["updated"] is True


@pytest.mark.asyncio
async def test_fail_open_on_redis_error(client, mock_redis):
    mock_redis.eval = AsyncMock(side_effect=ConnectionError("Redis down"))
    resp = await client.post(
        "/token/acquire",
        json={"source": "fetcher", "endpoint": "match"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["granted"] is True
    assert data["retry_after_ms"] is None


@pytest.mark.asyncio
@pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa not installed — Lua scripting unavailable")
async def test_21_concurrent_requests__20_granted_1_denied():
    """21 concurrent /token/acquire calls: exactly 20 granted, 1 denied.

    Uses fakeredis with Lua support so the dual-window Lua script executes
    atomically — no real Redis needed.
    """
    import fakeredis.aioredis

    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        cfg = Config()
        # Ensure short_limit=20 (default) is the binding constraint
        assert cfg.short_limit == 20

        app.state.cfg = cfg
        app.state.r = fake_r

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:

            async def _acquire():
                return await ac.post(
                    "/token/acquire",
                    json={"source": "fetcher", "endpoint": "match"},
                )

            responses = await asyncio.gather(*[_acquire() for _ in range(21)])

        results = [r.json() for r in responses]
        granted = [r for r in results if r["granted"] is True]
        denied = [r for r in results if r["granted"] is False]

        assert len(granted) == 20, f"Expected 20 granted, got {len(granted)}"
        assert len(denied) == 1, f"Expected 1 denied, got {len(denied)}"
        assert denied[0]["retry_after_ms"] is not None
        assert denied[0]["retry_after_ms"] > 0
    finally:
        await fake_r.aclose()
