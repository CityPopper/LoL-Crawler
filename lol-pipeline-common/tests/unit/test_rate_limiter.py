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

from unittest.mock import AsyncMock, patch  # noqa: E402

import fakeredis.aioredis  # noqa: E402

from lol_pipeline.rate_limiter import acquire_token, wait_for_token  # noqa: E402


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

        assert results[:5] == [1, 1, 1, 1, 1]
        assert results[5] < 1

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

        assert results[:3] == [1, 1, 1]
        assert results[3] < 1

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

        assert results[:3] == [1, 1, 1]
        assert results[3] < 1

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

        assert results[:10] == [1] * 10
        assert results[10] < 1

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

        assert results[:2] == [1, 1]
        assert results[2] < 1


class TestLuaKeysArray:
    """P10-DB-4/FV-2: Lua script must use KEYS[] for all Redis key access (cluster compat)."""

    @pytest.mark.asyncio
    async def test_lua_script_does_not_hardcode_limit_keys(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        """The Lua script must not contain hardcoded 'ratelimit:limits:short/long' strings.

        These must be passed via KEYS[3] and KEYS[4] to avoid CROSSSLOT errors
        in Redis Cluster mode.
        """
        from lol_pipeline.rate_limiter import _LUA_RATE_LIMIT_SCRIPT

        # The Lua body should NOT contain literal key names
        assert '"ratelimit:limits:short"' not in _LUA_RATE_LIMIT_SCRIPT
        assert '"ratelimit:limits:long"' not in _LUA_RATE_LIMIT_SCRIPT

    @pytest.mark.asyncio
    async def test_stored_limits_via_keys_array(self, r: fakeredis.aioredis.FakeRedis) -> None:
        """Stored limits are read via KEYS[3]/KEYS[4], not hardcoded key names."""
        # Pre-set stored limits
        await r.set("ratelimit:limits:short", "3")
        await r.set("ratelimit:limits:long", "100")

        # Should respect the stored short limit of 3
        results = [await acquire_token(r, limit_per_second=20) for _ in range(4)]
        assert results[:3] == [1, 1, 1]
        assert results[3] < 1

    @pytest.mark.asyncio
    async def test_custom_prefix_stored_limits(self, r: fakeredis.aioredis.FakeRedis) -> None:
        """Custom key_prefix reads stored limits from {key_prefix}:limits:*."""
        await r.set("custom_limiter:limits:short", "2")
        await r.set("custom_limiter:limits:long", "100")

        results = [
            await acquire_token(r, key_prefix="custom_limiter", limit_per_second=20)
            for _ in range(3)
        ]
        assert results[:2] == [1, 1]
        assert results[2] < 1

    @pytest.mark.asyncio
    async def test_custom_prefix_reads_from_custom_limit_keys(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        """acquire_token with custom key_prefix reads stored limits from {key_prefix}:limits:*."""
        # Set opgg-specific limits (low)
        await r.set("ratelimit:opgg:limits:short", "2")
        # Default limits (high) — must NOT interfere
        await r.set("ratelimit:limits:short", "20")

        results = [
            await acquire_token(r, key_prefix="ratelimit:opgg", limit_per_second=20)
            for _ in range(3)
        ]
        assert results[:2] == [1, 1]
        assert results[2] < 1  # limited by opgg-specific limit of 2


class TestLuaFloorGuard:
    """P14-DBG-2: Stored limit of '0' falls back to default, preventing deadlock."""

    @pytest.mark.asyncio
    async def test_stored_limit_zero_falls_back_to_default(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        """Stored short limit of '0' falls back to default — tokens still granted."""
        await r.set("ratelimit:limits:short", "0")

        # With limit_per_second=5 as fallback, first 5 should succeed
        results = [await acquire_token(r, limit_per_second=5) for _ in range(6)]

        assert results[:5] == [1, 1, 1, 1, 1]
        assert results[5] < 1

    @pytest.mark.asyncio
    async def test_stored_long_limit_zero_falls_back_to_default(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        """Stored long limit of '0' falls back to default long limit (100)."""
        await r.set("ratelimit:limits:long", "0")

        # Short limit fallback = 200 (high), so long limit of 100 is binding
        results = [await acquire_token(r, limit_per_second=200) for _ in range(101)]

        assert results[:100] == [1] * 100
        assert results[100] < 1


class TestRateLimiterBoundary:
    """Tier 3 — Rate limiter boundary conditions."""

    @pytest.mark.asyncio
    async def test_exactly_at_limit_returns_false(self, r: fakeredis.aioredis.FakeRedis) -> None:
        """When exactly at the limit, next request returns False."""
        # Use limit of 5 for test speed
        await r.set("ratelimit:limits:short", "5")
        for _ in range(5):
            assert await acquire_token(r, limit_per_second=20) == 1
        # 6th should fail
        assert await acquire_token(r, limit_per_second=20) < 1

    @pytest.mark.asyncio
    async def test_just_after_window_expires_returns_true(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        """After window expires, new tokens should be available."""
        # Fill up the window with limit 2
        await r.set("ratelimit:limits:short", "2")
        assert await acquire_token(r, limit_per_second=20) == 1
        assert await acquire_token(r, limit_per_second=20) == 1
        assert await acquire_token(r, limit_per_second=20) < 1

        # Manually expire the short window entries
        await r.delete("ratelimit:short")

        # Should now allow again
        assert await acquire_token(r, limit_per_second=20) == 1

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
                return -50  # denied with 50ms wait hint
            return await original_acquire(*args, **kwargs)

        with patch("lol_pipeline.rate_limiter.acquire_token", side_effect=limited_acquire):
            with patch("lol_pipeline.rate_limiter.asyncio.sleep", new_callable=AsyncMock):
                await wait_for_token(r, limit_per_second=20)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_sleep_uses_wait_hint(self, r):
        """Sleep duration is computed from the wait hint, not fixed 50ms."""

        async def deny_then_allow(*args, **kwargs):
            deny_then_allow.calls += 1
            if deny_then_allow.calls == 1:
                return -100  # 100ms wait hint
            return 1

        deny_then_allow.calls = 0

        with patch("lol_pipeline.rate_limiter.acquire_token", side_effect=deny_then_allow):
            with patch(
                "lol_pipeline.rate_limiter.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep:
                with patch("random.uniform", return_value=0.25):
                    await wait_for_token(r)
                # Should sleep based on wait hint (100ms) + jitter (25%), capped by deadline
                assert mock_sleep.call_count == 1
                slept = mock_sleep.call_args[0][0]
                assert 0.01 <= slept <= 1.0  # reasonable range

    @pytest.mark.asyncio
    async def test_timeout_raises_when_never_acquired(self, r):
        """P14-ARC-2: wait_for_token raises TimeoutError when max_wait_s exceeded."""

        async def always_deny(*args, **kwargs):
            return -50  # denied with 50ms wait hint

        with patch("lol_pipeline.rate_limiter.acquire_token", side_effect=always_deny):
            with patch("lol_pipeline.rate_limiter.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(TimeoutError, match="Rate limiter wait exceeded"):
                    await wait_for_token(r, max_wait_s=0.0)

    @pytest.mark.asyncio
    async def test_timeout_does_not_fire_when_acquired_in_time(self, r):
        """P14-ARC-2: wait_for_token succeeds if token acquired within max_wait_s."""
        call_count = 0

        async def deny_then_allow(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return -50  # denied with 50ms wait hint
            return 1

        with patch("lol_pipeline.rate_limiter.acquire_token", side_effect=deny_then_allow):
            with patch("lol_pipeline.rate_limiter.asyncio.sleep", new_callable=AsyncMock):
                await wait_for_token(r, max_wait_s=60.0)
        assert call_count == 2


class TestWaitForTokenWithRegion:
    @pytest.mark.asyncio
    async def test_region_sets_key_prefix(self, r):
        """When region is set, acquire_token uses ratelimit:{region} as key prefix."""
        # Set a low limit on the region-specific stored limit key
        await r.set("ratelimit:na1:limits:short", "2")

        # Acquire with region="na1" — tokens go to ratelimit:na1:short/long
        results = []
        for _ in range(3):
            got = await acquire_token(r, key_prefix="ratelimit:na1", limit_per_second=20)
            results.append(got)

        assert results[:2] == [1, 1]
        assert results[2] < 1

        # Default prefix tokens should be independent
        got_default = await acquire_token(r, key_prefix="ratelimit", limit_per_second=20)
        assert got_default == 1

    @pytest.mark.asyncio
    async def test_wait_for_token_ignores_region(self, r):
        """wait_for_token(region='na1') ignores region — uses default key_prefix='ratelimit'."""
        call_args_list = []
        original_acquire = acquire_token

        async def capturing_acquire(r, key_prefix="ratelimit", limit_per_second=20, limit_long=100):
            call_args_list.append(key_prefix)
            return await original_acquire(r, key_prefix, limit_per_second, limit_long)

        with patch("lol_pipeline.rate_limiter.acquire_token", side_effect=capturing_acquire):
            await wait_for_token(r, region="na1", limit_per_second=20)

        # region is kept for API compat but ignored; all callers share one window
        assert call_args_list[0] == "ratelimit"


class TestThrottleHintSlowsDown:
    @pytest.mark.asyncio
    async def test_throttle_key_causes_additional_sleep(self, r):
        """When ratelimit:throttle is set, wait_for_token sleeps 0.2s before proceeding."""
        await r.set("ratelimit:throttle", "1", ex=2)

        with patch("lol_pipeline.rate_limiter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await wait_for_token(r, limit_per_second=20)

        # Should have slept 0.2s for throttle hint (token acquired immediately, no 0.05 sleep)
        mock_sleep.assert_called_once_with(0.2)

    @pytest.mark.asyncio
    async def test_no_extra_sleep_without_throttle_key(self, r):
        """Without ratelimit:throttle, no extra sleep occurs."""
        with patch("lol_pipeline.rate_limiter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await wait_for_token(r, limit_per_second=20)

        mock_sleep.assert_not_called()


class TestLuaAtomicity:
    """TCG-12: All 4 KEYS (KEYS[1..4]) are passed to a single Lua eval call."""

    @pytest.mark.asyncio
    async def test_all_four_keys_in_single_eval(self, r: fakeredis.aioredis.FakeRedis) -> None:
        """acquire_token passes KEYS[1..4] in one eval — no split calls."""
        from lol_pipeline.rate_limiter import _LUA_RATE_LIMIT_SCRIPT

        eval_calls: list[tuple[int, list[str]]] = []
        original_eval = r.eval

        async def spy_eval(script: str, numkeys: int, *args: str) -> object:
            # Capture numkeys and the key arguments for this call
            eval_calls.append((numkeys, list(args[:numkeys])))
            return await original_eval(script, numkeys, *args)

        r.eval = spy_eval  # type: ignore[assignment]

        await r.set("ratelimit:limits:short", "5")
        await r.set("ratelimit:limits:long", "50")
        await acquire_token(r, limit_per_second=20)

        r.eval = original_eval  # type: ignore[assignment]

        assert len(eval_calls) == 1, "Expected exactly one eval call"
        numkeys, keys = eval_calls[0]
        assert numkeys == 4, f"Expected 4 KEYS, got {numkeys}"
        assert "ratelimit:short" in keys, "KEYS[1] (short window) not passed"
        assert "ratelimit:long" in keys, "KEYS[2] (long window) not passed"
        assert "ratelimit:limits:short" in keys, "KEYS[3] (stored short limit) not passed"
        assert "ratelimit:limits:long" in keys, "KEYS[4] (stored long limit) not passed"

    @pytest.mark.asyncio
    async def test_short_and_long_windows_both_written_after_grant(
        self, r: fakeredis.aioredis.FakeRedis
    ) -> None:
        """After a successful acquire, both KEYS[1] and KEYS[2] have entries."""
        await acquire_token(r, limit_per_second=20)

        short_count = await r.zcard("ratelimit:short")
        long_count = await r.zcard("ratelimit:long")
        assert short_count == 1, "Short window ZSET must have 1 entry after grant"
        assert long_count == 1, "Long window ZSET must have 1 entry after grant"


class TestThrottleKeyIsolation:
    """OPGG-4.9: throttle_key param isolates per-source throttle signals."""

    @pytest.mark.asyncio
    async def test_custom_throttle_key_used_when_set(self, r):
        """wait_for_token reads from custom throttle_key, not ratelimit:throttle."""
        # Set the op.gg-scoped throttle key
        await r.set("ratelimit:opgg:throttle", "1", ex=5)
        # Do NOT set the default throttle key
        # (ratelimit:throttle is absent)

        sleep_calls: list[float] = []

        async def capture_sleep(duration: float) -> None:
            sleep_calls.append(duration)

        with patch("lol_pipeline.rate_limiter.asyncio.sleep", side_effect=capture_sleep):
            await wait_for_token(
                r,
                key_prefix="ratelimit:opgg:games",
                throttle_key="ratelimit:opgg:throttle",
                limit_per_second=20,
            )

        # Should have slept 0.2s for the custom throttle key
        assert 0.2 in sleep_calls, f"Expected 0.2s throttle sleep, got: {sleep_calls}"

    @pytest.mark.asyncio
    async def test_default_throttle_key_backward_compat(self, r):
        """Default throttle_key='ratelimit:throttle' preserves existing behavior."""
        await r.set("ratelimit:throttle", "1", ex=5)

        sleep_calls: list[float] = []

        async def capture_sleep(duration: float) -> None:
            sleep_calls.append(duration)

        with patch("lol_pipeline.rate_limiter.asyncio.sleep", side_effect=capture_sleep):
            await wait_for_token(r, limit_per_second=20)

        assert 0.2 in sleep_calls

    @pytest.mark.asyncio
    async def test_riot_throttle_does_not_affect_opgg_calls(self, r):
        """Riot throttle key (ratelimit:throttle) does NOT slow op.gg calls."""
        # Set the RIOT throttle key
        await r.set("ratelimit:throttle", "1", ex=5)
        # op.gg throttle key is absent

        sleep_calls: list[float] = []

        async def capture_sleep(duration: float) -> None:
            sleep_calls.append(duration)

        with patch("lol_pipeline.rate_limiter.asyncio.sleep", side_effect=capture_sleep):
            await wait_for_token(
                r,
                key_prefix="ratelimit:opgg:games",
                throttle_key="ratelimit:opgg:throttle",  # scoped to op.gg
                limit_per_second=20,
            )

        # Should NOT have slept 0.2s — the Riot throttle key must be ignored
        assert 0.2 not in sleep_calls, (
            "Riot throttle key should not slow op.gg calls when throttle_key is scoped"
        )
