"""Unit tests for rate-limiter HTTP routes."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

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


# ---------------------------------------------------------------------------
# op.gg source-specific budget tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opgg_ui_in_known_sources():
    """opgg:ui must appear in the default known sources."""
    cfg = Config()
    assert "opgg:ui" in cfg.known_sources
    assert "opgg" in cfg.known_sources


@pytest.mark.asyncio
async def test_opgg_source_uses_pipeline_budget(client, mock_redis):
    """The 'opgg' source should use opgg_long_limit minus opgg_ui_long_limit."""
    cfg = app.state.cfg
    expected_long = cfg.opgg_long_limit - cfg.opgg_ui_long_limit

    with patch(
        "lol_rate_limiter.main.acquire_token",
        new_callable=AsyncMock,
        return_value=(True, None),
    ) as mock_acquire:
        resp = await client.post(
            "/token/acquire",
            json={"source": "opgg"},
        )
        assert resp.status_code == 200
        assert resp.json()["granted"] is True

        mock_acquire.assert_called_once()
        _, kwargs = mock_acquire.call_args
        assert kwargs["short_limit"] == cfg.opgg_short_limit
        assert kwargs["long_limit"] == expected_long


@pytest.mark.asyncio
async def test_opgg_ui_source_uses_reserved_budget(client, mock_redis):
    """The 'opgg:ui' source should use opgg_ui_long_limit as its long limit."""
    cfg = app.state.cfg

    with patch(
        "lol_rate_limiter.main.acquire_token",
        new_callable=AsyncMock,
        return_value=(True, None),
    ) as mock_acquire:
        resp = await client.post(
            "/token/acquire",
            json={"source": "opgg:ui"},
        )
        assert resp.status_code == 200
        assert resp.json()["granted"] is True

        mock_acquire.assert_called_once()
        _, kwargs = mock_acquire.call_args
        assert kwargs["short_limit"] == cfg.opgg_ui_short_limit
        assert kwargs["long_limit"] == cfg.opgg_ui_long_limit


@pytest.mark.asyncio
async def test_non_opgg_source_uses_default_limits(client, mock_redis):
    """Non-opgg sources should not pass short_limit/long_limit overrides."""
    with patch(
        "lol_rate_limiter.main.acquire_token",
        new_callable=AsyncMock,
        return_value=(True, None),
    ) as mock_acquire:
        resp = await client.post(
            "/token/acquire",
            json={"source": "fetcher"},
        )
        assert resp.status_code == 200
        assert resp.json()["granted"] is True

        mock_acquire.assert_called_once()
        _, kwargs = mock_acquire.call_args
        assert "short_limit" not in kwargs
        assert "long_limit" not in kwargs


# ---------------------------------------------------------------------------
# Cooling-off endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooling_off__unknown_source__404(client):
    resp = await client.post(
        "/cooling-off",
        json={"source": "nonexistent", "delay_ms": 5000},
    )
    assert resp.status_code == 404
    assert resp.json() == {"error": "unknown source"}


@pytest.mark.asyncio
async def test_cooling_off__known_source__sets_key(client, mock_redis):
    # pttl returns -2 (key does not exist) before cooling-off is set
    mock_redis.pttl = AsyncMock(return_value=-2)

    # Step 1: Acquire should succeed (no cooling-off active)
    mock_redis.eval = AsyncMock(return_value=1)
    resp = await client.post(
        "/token/acquire",
        json={"source": "fetcher", "endpoint": "match"},
    )
    assert resp.status_code == 200
    assert resp.json()["granted"] is True

    # Step 2: Set cooling-off via the endpoint
    resp = await client.post(
        "/cooling-off",
        json={"source": "fetcher", "delay_ms": 30000},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_redis.set.assert_called_with("ratelimit:fetcher:cooling_off", "1", px=30000)

    # Step 3: Simulate pttl returning the remaining cooling-off TTL
    mock_redis.pttl = AsyncMock(return_value=29500)

    resp = await client.post(
        "/token/acquire",
        json={"source": "fetcher", "endpoint": "match"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["granted"] is False
    assert data["retry_after_ms"] == 29500


# ---------------------------------------------------------------------------
# RL-PROXY-1b: Time-spread test (requires Lua via fakeredis+lupa)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa not installed — Lua scripting unavailable")
async def test_time_spread__denies_burst_at_window_start():
    """Burst of requests early in a long window should be throttled by
    the time-spread check before the hard cap is reached.

    Strategy: use a custom Lua eval that lets us control "now" timestamps.
    With limit_l=100, win_l=120_000ms, after 2400ms elapsed (2% of window),
    ideal = floor(2400/120000 * 100) = 2. So after 2 granted tokens, the
    3rd should be denied by time-spread (count_l=2 >= ideal(2) + 1 = 3
    is false, but if we grant a 3rd: count_l=3 >= ideal(2) + 1 = 3 is true).
    We consume 3 tokens in the first 2400ms, then the 4th is denied.
    """
    import fakeredis.aioredis

    from lol_rate_limiter._lua import LUA_RATE_LIMIT_SCRIPT

    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        cfg = Config()
        prefix = "ratelimit:spread"
        # Use direct eval with controlled timestamps instead of acquire_token
        # so we can simulate time passing within the test.
        base_ts = 1_000_000_000_000  # arbitrary base time in ms

        # Grant 3 tokens at t=base (within the same millisecond)
        for i in range(3):
            result = await fake_r.eval(
                LUA_RATE_LIMIT_SCRIPT,
                4,
                f"{prefix}:short",
                f"{prefix}:long",
                f"{prefix}:limits:short",
                f"{prefix}:limits:long",
                base_ts,
                cfg.short_limit,
                cfg.long_limit,
                cfg.short_window_ms,
                cfg.long_window_ms,
                f"uid-{i}",
            )
            # All 3 should be granted (elapsed=0, ideal=0, spread skipped)
            assert int(result) == 1, f"Token {i} should be granted at t=0"

        # Now advance time by 2400ms (2% of 120s window).
        # ideal = floor(2400 / 120000 * 100) = 2
        # count_l = 3, ideal + 1 = 3 => count_l(3) >= 3 => DENIED
        elapsed_ms = 2400
        result = await fake_r.eval(
            LUA_RATE_LIMIT_SCRIPT,
            4,
            f"{prefix}:short",
            f"{prefix}:long",
            f"{prefix}:limits:short",
            f"{prefix}:limits:long",
            base_ts + elapsed_ms,
            cfg.short_limit,
            cfg.long_limit,
            cfg.short_window_ms,
            cfg.long_window_ms,
            "uid-denied",
        )
        result_int = int(result)
        assert result_int < 0, f"Should be denied by time-spread, got {result_int}"
        retry_after_ms = abs(result_int)
        assert retry_after_ms > 0
    finally:
        await fake_r.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa not installed — Lua scripting unavailable")
async def test_time_spread__allows_initial_burst():
    """Grant 5 tokens at t=0 in a 120s/20-limit long window.

    When elapsed ≈ 0, ideal = floor(0 / 120000 * 20) = 0.  The spread
    condition ``ideal > 0 and count_l > ideal`` is never true while ideal=0,
    so all 5 tokens should be granted (no spread denial).
    """
    import fakeredis.aioredis

    from lol_rate_limiter._lua import LUA_RATE_LIMIT_SCRIPT

    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        base_ts = 1_000_000_000_000  # arbitrary base time in ms
        short_limit = 20
        long_limit = 20
        short_window_ms = 1000
        long_window_ms = 120_000
        prefix = "ratelimit:burst"

        for i in range(5):
            result = await fake_r.eval(
                LUA_RATE_LIMIT_SCRIPT,
                4,
                f"{prefix}:short",
                f"{prefix}:long",
                f"{prefix}:limits:short",
                f"{prefix}:limits:long",
                base_ts,
                short_limit,
                long_limit,
                short_window_ms,
                long_window_ms,
                f"uid-{i}",
            )
            assert int(result) == 1, (
                f"Token {i} should be granted at t=0 (ideal=0, spread inactive)"
            )
    finally:
        await fake_r.aclose()


# ---------------------------------------------------------------------------
# RL-PROXY-1a: Method-level rate limiting tests (requires Lua)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa not installed — Lua scripting unavailable")
async def test_method_level__endpoint_bucket_exhausted__still_denied():
    """Exhaust the endpoint-specific (method) bucket but leave the app-level
    bucket with plenty of capacity. Acquisition should still be denied.
    """
    import fakeredis.aioredis

    from lol_rate_limiter._token import acquire_token_with_method

    fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        cfg = Config()
        # App-level: generous (20 short, 100 long)
        # Method-level: tiny (2 short, 5 long) — the binding constraint
        method_short = 2
        method_long = 5

        granted_count = 0
        for _ in range(method_short):
            granted, _ = await acquire_token_with_method(
                fake_r,
                cfg,
                "ratelimit:riot",
                "match-v5",
                method_short_limit=method_short,
                method_long_limit=method_long,
            )
            if granted:
                granted_count += 1

        assert granted_count == method_short

        # Next request should be denied: method short bucket is full
        granted, retry_after_ms = await acquire_token_with_method(
            fake_r,
            cfg,
            "ratelimit:riot",
            "match-v5",
            method_short_limit=method_short,
            method_long_limit=method_long,
        )
        assert granted is False
        assert retry_after_ms is not None
        assert retry_after_ms > 0
    finally:
        await fake_r.aclose()


@pytest.mark.asyncio
@pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa not installed — Lua scripting unavailable")
async def test_method_level__riot_source_uses_method_acquire(client):
    """Riot sources with an endpoint should use acquire_token_with_method."""
    with patch(
        "lol_rate_limiter.main.acquire_token_with_method",
        new_callable=AsyncMock,
        return_value=(True, None),
    ) as mock_method:
        resp = await client.post(
            "/token/acquire",
            json={"source": "riot", "endpoint": "match-v5"},
        )
        assert resp.status_code == 200
        assert resp.json()["granted"] is True
        mock_method.assert_called_once()
