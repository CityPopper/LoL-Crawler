"""Unit tests for lol_pipeline.rate_limiter — dynamic stored limits.

Requires `lupa` for Lua scripting in fakeredis.
"""

from __future__ import annotations

import pytest

try:
    import lupa  # noqa: F401

    _LUPA_AVAILABLE = True
except ImportError:
    _LUPA_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _LUPA_AVAILABLE, reason="lupa not installed — Lua scripting unavailable"
)

from unittest.mock import AsyncMock, patch

import fakeredis.aioredis

from lol_pipeline.rate_limiter import acquire_token, wait_for_token


@pytest.fixture
async def r() -> fakeredis.aioredis.FakeRedis:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestStoredLimits:
    @pytest.mark.asyncio
    async def test_stored_short_limit_is_used(self, r: fakeredis.aioredis.FakeRedis) -> None:
        # Input:  ratelimit:limits:short = "5" pre-set in Redis
        #         config limit_per_second = 20 (higher than stored)
        #         6 sequential acquire_token() calls
        # Output: first 5 return True, 6th returns False
        #         (stored value 5 wins over config value 20)
        await r.set("ratelimit:limits:short", "5")

        results = [await acquire_token(r, limit_per_second=20) for _ in range(6)]

        assert results[:5] == [True, True, True, True, True]
        assert results[5] is False

    @pytest.mark.asyncio
    async def test_fallback_to_config_when_no_stored_limits(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        # Input:  no stored limits in Redis
        #         config limit_per_second = 3
        #         4 sequential acquire_token() calls
        # Output: first 3 return True, 4th returns False
        #         (config value used as fallback)
        results = [await acquire_token(r, limit_per_second=3) for _ in range(4)]

        assert results[:3] == [True, True, True]
        assert results[3] is False

    @pytest.mark.asyncio
    async def test_stored_limit_overrides_higher_config(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        # Input:  ratelimit:limits:short = "3" pre-set in Redis
        #         config limit_per_second = 20
        #         4 sequential acquire_token() calls
        # Output: first 3 return True, 4th returns False
        #         (stored value 3 is lower than config 20 — stored wins)
        await r.set("ratelimit:limits:short", "3")

        results = [await acquire_token(r, limit_per_second=20) for _ in range(4)]

        assert results[:3] == [True, True, True]
        assert results[3] is False

    @pytest.mark.asyncio
    async def test_stored_limit_higher_than_config_uses_stored(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        # Input:  ratelimit:limits:short = "10" pre-set in Redis
        #         config limit_per_second = 3 (lower than stored)
        #         11 sequential acquire_token() calls
        # Output: first 10 return True, 11th returns False
        #         (stored value 10 is used, not config 3)
        await r.set("ratelimit:limits:short", "10")

        results = [await acquire_token(r, limit_per_second=3) for _ in range(11)]

        assert results[:10] == [True] * 10
        assert results[10] is False

    @pytest.mark.asyncio
    async def test_stored_long_limit_is_enforced(self, r: fakeredis.aioredis.FakeRedis) -> None:
        # Input:  ratelimit:limits:long = "2" pre-set in Redis (very small — for test speed)
        #         ratelimit:limits:short is NOT set (fallback to config)
        #         config limit_per_second = 100 (much larger than long limit)
        #         3 sequential acquire_token() calls
        # Output: first 2 return True, 3rd returns False
        #         (stored long window limit of 2 is the binding constraint)
        await r.set("ratelimit:limits:long", "2")

        results = [await acquire_token(r, limit_per_second=100) for _ in range(3)]

        assert results[:2] == [True, True]
        assert results[2] is False


class TestRateLimiterBoundary:
    """Tier 3 — Rate limiter boundary conditions."""

    @pytest.mark.asyncio
    async def test_exactly_at_limit_returns_false(self, r: fakeredis.aioredis.FakeRedis) -> None:
        """When exactly at the limit, next request returns False."""
        # Use limit of 5 for test speed
        await r.set("ratelimit:limits:short", "5")
        for _ in range(5):
            assert await acquire_token(r, limit_per_second=20) is True
        # 6th should fail
        assert await acquire_token(r, limit_per_second=20) is False

    @pytest.mark.asyncio
    async def test_just_after_window_expires_returns_true(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        """After window expires, new tokens should be available."""
        # Fill up the window with limit 2
        await r.set("ratelimit:limits:short", "2")
        assert await acquire_token(r, limit_per_second=20) is True
        assert await acquire_token(r, limit_per_second=20) is True
        assert await acquire_token(r, limit_per_second=20) is False

        # Manually expire the short window entries
        await r.delete("ratelimit:short")

        # Should now allow again
        assert await acquire_token(r, limit_per_second=20) is True

    @pytest.mark.asyncio
    async def test_lua_script_error_propagates(self, r: fakeredis.aioredis.FakeRedis) -> None:
        """Redis errors during Lua eval should propagate to caller."""
        original_eval = r.eval

        async def failing_eval(*args, **kwargs):
            raise ConnectionError("redis down")

        r.eval = failing_eval  # type: ignore[assignment]

        with pytest.raises(ConnectionError, match="redis down"):
            await acquire_token(r, limit_per_second=20)

        r.eval = original_eval  # type: ignore[assignment]


class TestWaitForToken:
    @pytest.mark.asyncio
    async def test_immediate_acquire_no_sleep(self, r):
        """If token is available immediately, no sleep occurs."""
        with patch("lol_pipeline.rate_limiter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await wait_for_token(r, limit_per_second=20)
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_until_acquired(self, r):
        """Polls until a token becomes available."""
        call_count = 0
        original_acquire = acquire_token

        async def limited_acquire(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return False
            return await original_acquire(*args, **kwargs)

        with patch("lol_pipeline.rate_limiter.acquire_token", side_effect=limited_acquire):
            with patch("lol_pipeline.rate_limiter.asyncio.sleep", new_callable=AsyncMock):
                await wait_for_token(r, limit_per_second=20)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_sleep_interval_is_50ms(self, r):
        """Each retry sleeps for 0.05 seconds."""

        async def deny_then_allow(*args, **kwargs):
            deny_then_allow.calls += 1
            return deny_then_allow.calls > 1

        deny_then_allow.calls = 0

        with patch("lol_pipeline.rate_limiter.acquire_token", side_effect=deny_then_allow):
            with patch(
                "lol_pipeline.rate_limiter.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep:
                await wait_for_token(r)
                mock_sleep.assert_called_once_with(0.05)
