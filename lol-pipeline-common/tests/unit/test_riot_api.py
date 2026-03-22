"""Unit tests for lol_pipeline.riot_api — rate limit header parsing and storage."""

from __future__ import annotations

import json
import logging

import fakeredis.aioredis
import httpx
import pytest
import respx

from lol_pipeline.riot_api import (
    PLATFORM_TO_REGION,
    AuthError,
    NotFoundError,
    RateLimitError,
    RiotClient,
    ServerError,
    _parse_app_rate_limit,
)

# ---------------------------------------------------------------------------
# _parse_app_rate_limit
# ---------------------------------------------------------------------------


class TestParseAppRateLimit:
    def test_valid_dev_key_header(self) -> None:
        # Input:  "20:1,100:120"  (dev key: 20 req/s, 100 req/2min)
        # Output: (20, 100)
        assert _parse_app_rate_limit("20:1,100:120") == (20, 100)

    def test_valid_production_key_header(self) -> None:
        # Input:  "100:1,1000:120"  (production key: higher limits, same windows)
        # Output: (100, 1000)
        assert _parse_app_rate_limit("100:1,1000:120") == (100, 1000)

    def test_entry_order_does_not_matter(self) -> None:
        # Input:  "100:120,20:1"  (long window listed first)
        # Output: (20, 100) — order-independent by window size
        assert _parse_app_rate_limit("100:120,20:1") == (20, 100)

    def test_empty_string(self) -> None:
        # Input:  ""  (header absent / empty)
        # Output: None
        assert _parse_app_rate_limit("") is None

    def test_malformed_string(self) -> None:
        # Input:  "bad"
        # Output: None
        assert _parse_app_rate_limit("bad") is None

    def test_missing_long_window(self) -> None:
        # Input:  "20:1"  (only 1-second window, no 120-second window)
        # Output: None — both windows are required
        assert _parse_app_rate_limit("20:1") is None

    def test_missing_short_window(self) -> None:
        # Input:  "100:120"  (only 120-second window, no 1-second window)
        # Output: None
        assert _parse_app_rate_limit("100:120") is None

    def test_unrecognised_windows__default_config(self) -> None:
        # Input:  "500:10,30000:600" — neither 1s nor 120s window present
        # Output: None when using default windows (1s, 120s)
        assert _parse_app_rate_limit("500:10,30000:600") is None

    def test_prod_key_windows__configured(self) -> None:
        # Input:  "500:10,30000:600" — production key with 10s and 600s windows
        #         short_window_s=10, long_window_s=600
        # Output: (500, 30000)
        result = _parse_app_rate_limit("500:10,30000:600", short_window_s=10, long_window_s=600)
        assert result == (500, 30000)

    def test_dev_key_windows__default_config(self) -> None:
        # Input:  "20:1,100:120" — dev key with 1s and 120s windows
        #         using default short_window_s=1, long_window_s=120
        # Output: (20, 100)
        result = _parse_app_rate_limit("20:1,100:120")
        assert result == (20, 100)

    def test_mismatched_windows__falls_back_to_found_window(self) -> None:
        # Input:  "500:10,30000:600" — header has 10s window
        #         short_window_s=1 (configured for 1s, not 10s)
        #         long_window_s=600 (matches)
        # Output: None — short window not found for configured duration
        result = _parse_app_rate_limit("500:10,30000:600", short_window_s=1, long_window_s=600)
        assert result is None

    def test_prod_key_with_extra_windows(self) -> None:
        # Input:  "500:10,30000:600,50:1" — prod key with additional 1s window
        #         short_window_s=10, long_window_s=600
        # Output: (500, 30000) — extracts the configured windows correctly
        result = _parse_app_rate_limit(
            "500:10,30000:600,50:1", short_window_s=10, long_window_s=600
        )
        assert result == (500, 30000)


# ---------------------------------------------------------------------------
# RiotClient — stores limits in Redis on successful responses
# ---------------------------------------------------------------------------


class TestRiotClientHeaders:
    @pytest.mark.asyncio
    async def test_sends_user_agent_header(self) -> None:
        # Input:  any request via RiotClient
        # Output: request contains User-Agent: lol-pipeline/1.0
        with respx.mock:
            route = respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Test", "tagLine": "NA1"},
                )
            )
            client = RiotClient("RGAPI-test")
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.close()

        assert route.calls[0].request.headers["user-agent"] == "lol-pipeline/1.0"


class TestRiotClientRateLimitStorage:
    @pytest.fixture
    def fake_redis(self) -> fakeredis.aioredis.FakeRedis:
        return fakeredis.aioredis.FakeRedis(decode_responses=True)

    @pytest.mark.asyncio
    async def test_stores_limits_on_200(self, fake_redis: fakeredis.aioredis.FakeRedis) -> None:
        # Input:  200 response with "X-App-Rate-Limit: 20:1,100:120" header
        #         RiotClient constructed with r=<redis>
        # Output: ratelimit:limits:short == "20"
        #         ratelimit:limits:long  == "100"
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        assert await fake_redis.get("ratelimit:limits:short") == "20"
        assert await fake_redis.get("ratelimit:limits:long") == "100"
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_stores_limits_on_200__prod_key_windows(
        self, fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Input:  200 response with "X-App-Rate-Limit: 500:10,30000:600" header
        #         RATE_LIMIT_SHORT_WINDOW_S=10, RATE_LIMIT_LONG_WINDOW_S=600
        # Output: ratelimit:limits:short == "500"
        #         ratelimit:limits:long  == "30000"
        monkeypatch.setenv("RATE_LIMIT_SHORT_WINDOW_S", "10")
        monkeypatch.setenv("RATE_LIMIT_LONG_WINDOW_S", "600")
        # Force re-read of module-level config
        import lol_pipeline.riot_api as riot_api_mod

        monkeypatch.setattr(riot_api_mod, "_SHORT_WINDOW_S", 10)
        monkeypatch.setattr(riot_api_mod, "_LONG_WINDOW_S", 600)

        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                    headers={"X-App-Rate-Limit": "500:10,30000:600"},
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        assert await fake_redis.get("ratelimit:limits:short") == "500"
        assert await fake_redis.get("ratelimit:limits:long") == "30000"
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_no_write_when_header_absent(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        # Input:  200 response WITHOUT X-App-Rate-Limit header
        #         RiotClient constructed with r=<redis>
        # Output: ratelimit:limits:short and :long are NOT set (remain None)
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        assert await fake_redis.get("ratelimit:limits:short") is None
        assert await fake_redis.get("ratelimit:limits:long") is None
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_no_write_on_non_200(self, fake_redis: fakeredis.aioredis.FakeRedis) -> None:
        # Input:  404 response (raises NotFoundError)
        #         RiotClient constructed with r=<redis>
        # Output: ratelimit:limits:* keys are NOT set
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Nobody/NA1"
            ).mock(return_value=httpx.Response(404))
            client = RiotClient("RGAPI-test", r=fake_redis)
            with pytest.raises(NotFoundError):
                await client.get_account_by_riot_id("Nobody", "NA1", "na1")
            await client.close()

        assert await fake_redis.get("ratelimit:limits:short") is None
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_no_crash_without_redis(self) -> None:
        # Input:  200 response with X-App-Rate-Limit header
        #         RiotClient constructed WITHOUT r (r=None, the default)
        # Output: returns data dict normally, no exception raised
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                )
            )
            client = RiotClient("RGAPI-test")  # no r — default None
            result = await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.close()

        assert result["puuid"] == "test-puuid"

    @pytest.mark.asyncio
    async def test_updates_limits_on_subsequent_calls(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        # Input:  first call returns X-App-Rate-Limit: 20:1,100:120
        #         second call returns X-App-Rate-Limit: 100:1,1000:120 (key rotated/upgraded)
        # Output: after second call, ratelimit:limits:short == "100"
        #         (stored value is always the most recent)
        with respx.mock:
            route = respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            )
            route.side_effect = [
                httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                ),
                httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "100:1,1000:120"},
                ),
            ]
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.close()

        assert await fake_redis.get("ratelimit:limits:short") == "100"
        assert await fake_redis.get("ratelimit:limits:long") == "1000"
        await fake_redis.aclose()


class TestRateLimitKeyTTL:
    """B15: ratelimit:limits:short/long keys have a TTL to expire stale limits."""

    @pytest.fixture
    def fake_redis(self) -> fakeredis.aioredis.FakeRedis:
        return fakeredis.aioredis.FakeRedis(decode_responses=True)

    @pytest.mark.asyncio
    async def test_stored_limits_have_ttl(self, fake_redis: fakeredis.aioredis.FakeRedis) -> None:
        """B15: ratelimit:limits:short and :long have a 1-hour TTL after being set."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        short_ttl = await fake_redis.ttl("ratelimit:limits:short")
        long_ttl = await fake_redis.ttl("ratelimit:limits:long")
        # TTL should be close to 3600 (1 hour), allowing tolerance for execution
        assert 3590 <= short_ttl <= 3600
        assert 3590 <= long_ttl <= 3600
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_ttl_refreshed_on_subsequent_calls(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        """B15: Each successful API call refreshes the TTL on limit keys
        once the write-interval has elapsed (rate limit values are cached
        in-process to avoid redundant Redis writes)."""
        with respx.mock:
            route = respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            )
            route.side_effect = [
                httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                ),
                httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                ),
            ]
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            # Manually reduce TTL to simulate time passing
            await fake_redis.expire("ratelimit:limits:short", 100)
            # Simulate that the write interval has elapsed so the client
            # will re-write on the next call and refresh the TTL
            client._limits_last_written_at = 0.0
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.close()

        # After second call, TTL should be refreshed back to ~3600
        short_ttl = await fake_redis.ttl("ratelimit:limits:short")
        assert short_ttl > 100  # Was refreshed, not still at 100
        await fake_redis.aclose()


class TestRateLimitWriteCaching:
    """Rate limit values are cached in-process to avoid redundant Redis writes."""

    @pytest.fixture
    def fake_redis(self) -> fakeredis.aioredis.FakeRedis:
        return fakeredis.aioredis.FakeRedis(decode_responses=True)

    @pytest.mark.asyncio
    async def test_skips_redis_write_when_values_unchanged(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        """Second call with identical limits does NOT write to Redis again."""
        with respx.mock:
            route = respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            )
            route.side_effect = [
                httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                ),
                httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                ),
            ]
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            # Overwrite the key with a marker value after first write
            await fake_redis.set("ratelimit:limits:short", "MARKER")
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.close()

        # MARKER should remain — proving the second call did NOT overwrite
        assert await fake_redis.get("ratelimit:limits:short") == "MARKER"
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_writes_redis_when_values_change(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        """When API returns different limits (key tier upgrade), Redis is updated."""
        with respx.mock:
            route = respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            )
            route.side_effect = [
                httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                ),
                httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "100:1,1000:120"},
                ),
            ]
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            assert await fake_redis.get("ratelimit:limits:short") == "20"
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.close()

        # Values changed, so Redis should have the new values
        assert await fake_redis.get("ratelimit:limits:short") == "100"
        assert await fake_redis.get("ratelimit:limits:long") == "1000"
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_first_call_always_writes(self, fake_redis: fakeredis.aioredis.FakeRedis) -> None:
        """First call writes to Redis even though cache starts as None."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            assert client._cached_short_limit is None
            assert client._cached_long_limit is None
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.close()

        assert await fake_redis.get("ratelimit:limits:short") == "20"
        assert await fake_redis.get("ratelimit:limits:long") == "100"
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_rewrites_after_write_interval_elapsed(
        self, fake_redis: fakeredis.aioredis.FakeRedis
    ) -> None:
        """After the write interval elapses, same values are re-written to refresh TTL."""
        with respx.mock:
            route = respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            )
            route.side_effect = [
                httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                ),
                httpx.Response(
                    200,
                    json={"puuid": "puuid1", "gameName": "Test", "tagLine": "NA1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                ),
            ]
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            # Overwrite with marker, then simulate write interval elapsed
            await fake_redis.set("ratelimit:limits:short", "MARKER")
            client._limits_last_written_at = 0.0
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.close()

        # MARKER should be overwritten because the write interval elapsed
        assert await fake_redis.get("ratelimit:limits:short") == "20"
        await fake_redis.aclose()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestRiotClientErrors:
    @pytest.mark.asyncio
    async def test_404_raises_not_found(self) -> None:
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(return_value=httpx.Response(404))
            client = RiotClient("RGAPI-test")
            with pytest.raises(NotFoundError):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()

    @pytest.mark.asyncio
    async def test_403_raises_auth_error(self) -> None:
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(return_value=httpx.Response(403))
            client = RiotClient("RGAPI-test")
            with pytest.raises(AuthError):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(return_value=httpx.Response(401))
            client = RiotClient("RGAPI-test")
            with pytest.raises(AuthError):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()

    @pytest.mark.asyncio
    async def test_429_raises_rate_limit_with_retry_after(self) -> None:
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(return_value=httpx.Response(429, headers={"Retry-After": "30"}))
            client = RiotClient("RGAPI-test")
            with pytest.raises(RateLimitError) as exc_info:
                await client.get_account_by_riot_id("X", "Y", "na1")
            # (30 + 1) * 1000 = 31000
            assert exc_info.value.retry_after_ms == 31000
            await client.close()

    @pytest.mark.asyncio
    async def test_500_raises_server_error(self) -> None:
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(return_value=httpx.Response(500, text="Internal Server Error"))
            client = RiotClient("RGAPI-test")
            with pytest.raises(ServerError):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()


# ---------------------------------------------------------------------------
# Platform → Region routing
# ---------------------------------------------------------------------------


class TestGetAccountByPuuid:
    @pytest.mark.asyncio
    async def test_resolves_puuid_to_account(self) -> None:
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/test-puuid-abc"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid-abc", "gameName": "TestPlayer", "tagLine": "NA1"},
                )
            )
            client = RiotClient("RGAPI-test")
            result = await client.get_account_by_puuid("test-puuid-abc", "na1")
            await client.close()

        assert result["gameName"] == "TestPlayer"
        assert result["tagLine"] == "NA1"

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self) -> None:
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-puuid/unknown"
            ).mock(return_value=httpx.Response(404))
            client = RiotClient("RGAPI-test")
            with pytest.raises(NotFoundError):
                await client.get_account_by_puuid("unknown", "na1")
            await client.close()


class TestPlatformRouting:
    @pytest.mark.parametrize(
        "platform,expected_region",
        [
            ("na1", "americas"),
            ("br1", "americas"),
            ("euw1", "europe"),
            ("eun1", "europe"),
            ("kr", "asia"),
            ("jp1", "asia"),
            ("oc1", "sea"),
        ],
    )
    def test_platform_to_region_mapping(self, platform: str, expected_region: str) -> None:
        assert PLATFORM_TO_REGION[platform] == expected_region


class TestRiotClientNetworkErrors:
    @pytest.mark.asyncio
    async def test_network_error_raises_server_error(self) -> None:
        """Connection errors are wrapped as ServerError."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(side_effect=httpx.ConnectError("connection refused"))
            client = RiotClient("RGAPI-test")
            with pytest.raises(ServerError, match="network error"):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()

    @pytest.mark.asyncio
    async def test_timeout_raises_server_error(self) -> None:
        """Timeout errors are wrapped as ServerError."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(side_effect=httpx.ReadTimeout("read timed out"))
            client = RiotClient("RGAPI-test")
            with pytest.raises(ServerError, match="network error"):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()


class TestRateLimitErrorDetails:
    @pytest.mark.asyncio
    async def test_429_without_retry_after_header(self) -> None:
        """429 without Retry-After header gives retry_after_ms=None."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(return_value=httpx.Response(429))
            client = RiotClient("RGAPI-test")
            with pytest.raises(RateLimitError) as exc_info:
                await client.get_account_by_riot_id("X", "Y", "na1")
            assert exc_info.value.retry_after_ms is None
            await client.close()

    @pytest.mark.asyncio
    async def test_429_with_http_date_retry_after__uses_default(self) -> None:
        """429 with HTTP-date Retry-After (non-integer) falls back to default retry_after_ms."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(
                return_value=httpx.Response(
                    429,
                    headers={"Retry-After": "Thu, 19 Mar 2026 12:00:00 GMT"},
                )
            )
            client = RiotClient("RGAPI-test")
            with pytest.raises(RateLimitError) as exc_info:
                await client.get_account_by_riot_id("X", "Y", "na1")
            # Should not crash; should use a sensible default (1000ms)
            assert exc_info.value.retry_after_ms == 1000
            await client.close()

    @pytest.mark.asyncio
    async def test_429_with_float_retry_after__parses_correctly(self) -> None:
        """429 with float-like Retry-After (e.g. '1.5') parses to integer ms."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(
                return_value=httpx.Response(
                    429,
                    headers={"Retry-After": "1.5"},
                )
            )
            client = RiotClient("RGAPI-test")
            with pytest.raises(RateLimitError) as exc_info:
                await client.get_account_by_riot_id("X", "Y", "na1")
            # int(float("1.5")) = 1 → 1*1000 + 1000 = 2000
            assert exc_info.value.retry_after_ms == 2000
            await client.close()

    @pytest.mark.asyncio
    async def test_502_raises_server_error(self) -> None:
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(return_value=httpx.Response(502, text="Bad Gateway"))
            client = RiotClient("RGAPI-test")
            with pytest.raises(ServerError):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()


class TestRiotClientUrlEncoding:
    """SEC-2: game_name/tag_line must be URL-encoded in API URLs."""

    @pytest.mark.asyncio
    async def test_special_chars_in_riot_id_are_url_encoded(self) -> None:
        """Names with spaces/unicode/special chars must be percent-encoded."""
        with respx.mock:
            # The encoded URL that should be called
            route = respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/"
                "by-riot-id/Hello%20World/Tag%23Line"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Hello World", "tagLine": "Tag#Line"},
                )
            )
            client = RiotClient("RGAPI-test")
            result = await client.get_account_by_riot_id("Hello World", "Tag#Line", "na1")
            await client.close()

        assert result["puuid"] == "test-puuid"
        assert route.called


class TestCircuitBreaker:
    """R4: Circuit breaker opens after consecutive 5xx errors."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_consecutive_5xx(self) -> None:
        """After 5 consecutive 5xx responses, circuit breaker opens and rejects requests."""
        with respx.mock:
            url = "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            respx.get(url).mock(return_value=httpx.Response(500, text="Server Error"))
            client = RiotClient("RGAPI-test")

            # First 5 calls should raise ServerError from the API
            for _ in range(5):
                with pytest.raises(ServerError):
                    await client.get_account_by_riot_id("X", "Y", "na1")

            # 6th call should raise circuit breaker error without hitting API
            with pytest.raises(ServerError, match="circuit breaker open"):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()

    @pytest.mark.asyncio
    async def test_circuit_breaker_resets_on_success(self) -> None:
        """A successful response resets the consecutive 5xx counter."""
        with respx.mock:
            url = "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            route = respx.get(url)
            # 4 failures, then 1 success, then 4 more failures
            route.side_effect = [
                httpx.Response(500, text="Error"),
                httpx.Response(500, text="Error"),
                httpx.Response(500, text="Error"),
                httpx.Response(500, text="Error"),
                httpx.Response(
                    200,
                    json={"puuid": "p", "gameName": "X", "tagLine": "Y"},
                ),
                httpx.Response(500, text="Error"),
                httpx.Response(500, text="Error"),
                httpx.Response(500, text="Error"),
                httpx.Response(500, text="Error"),
            ]
            client = RiotClient("RGAPI-test")

            # 4 failures
            for _ in range(4):
                with pytest.raises(ServerError):
                    await client.get_account_by_riot_id("X", "Y", "na1")

            # 1 success resets the counter
            await client.get_account_by_riot_id("X", "Y", "na1")
            assert client._consecutive_5xx == 0

            # 4 more failures — circuit should NOT be open (counter reset at 0)
            for _ in range(4):
                with pytest.raises(ServerError):
                    await client.get_account_by_riot_id("X", "Y", "na1")
            assert client._consecutive_5xx == 4
            assert client._circuit_open_until == 0.0  # not tripped
            await client.close()

    @pytest.mark.asyncio
    async def test_circuit_breaker_auto_closes_after_timeout(self) -> None:
        """After the circuit open duration expires, requests go through again."""
        import time as _time

        with respx.mock:
            url = "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            route = respx.get(url)
            route.side_effect = [
                httpx.Response(500, text="Error"),
                httpx.Response(500, text="Error"),
                httpx.Response(500, text="Error"),
                httpx.Response(500, text="Error"),
                httpx.Response(500, text="Error"),
                # After timeout, this response goes through
                httpx.Response(
                    200,
                    json={"puuid": "p", "gameName": "X", "tagLine": "Y"},
                ),
            ]
            client = RiotClient("RGAPI-test")

            # Trigger circuit breaker
            for _ in range(5):
                with pytest.raises(ServerError):
                    await client.get_account_by_riot_id("X", "Y", "na1")

            # Circuit is open — verify it rejects
            with pytest.raises(ServerError, match="circuit breaker open"):
                await client.get_account_by_riot_id("X", "Y", "na1")

            # Fast-forward time past the circuit open duration
            client._circuit_open_until = _time.monotonic() - 1

            # Circuit should be closed now — request goes through
            result = await client.get_account_by_riot_id("X", "Y", "na1")
            assert result["puuid"] == "p"
            assert client._consecutive_5xx == 0  # reset on success
            await client.close()

    @pytest.mark.asyncio
    async def test_circuit_breaker_counts_network_errors(self) -> None:
        """Network errors (RequestError) also increment the 5xx counter."""
        with respx.mock:
            url = "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            respx.get(url).mock(side_effect=httpx.ConnectError("connection refused"))
            client = RiotClient("RGAPI-test")

            for _ in range(5):
                with pytest.raises(ServerError, match="network error"):
                    await client.get_account_by_riot_id("X", "Y", "na1")

            # Circuit should now be open
            with pytest.raises(ServerError, match="circuit breaker open"):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()


class TestRateLimitCountParsing:
    """R5: X-App-Rate-Limit-Count header is parsed for near-limit warnings."""

    @pytest.fixture
    def fake_redis(self) -> fakeredis.aioredis.FakeRedis:
        return fakeredis.aioredis.FakeRedis(decode_responses=True)

    @pytest.mark.asyncio
    async def test_rate_limit_count_header_parsed(
        self,
        fake_redis: fakeredis.aioredis.FakeRedis,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Near-limit usage triggers a warning log."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                    headers={
                        "X-App-Rate-Limit": "20:1,100:120",
                        # 19/20 and 95/100 -- both near limit
                        "X-App-Rate-Limit-Count": "19:1,95:120",
                    },
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            with caplog.at_level(logging.WARNING, logger="riot_api"):
                await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        # Should have logged warnings about near capacity
        warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("near capacity" in msg for msg in warn_msgs), (
            f"Expected near-capacity warning, got: {warn_msgs}"
        )
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_rate_limit_count_no_warning_when_under_threshold(
        self,
        fake_redis: fakeredis.aioredis.FakeRedis,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Low usage does not trigger a warning."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                    headers={
                        "X-App-Rate-Limit": "20:1,100:120",
                        "X-App-Rate-Limit-Count": "5:1,30:120",  # well under 90%
                    },
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            with caplog.at_level(logging.WARNING, logger="riot_api"):
                await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not any("near capacity" in msg for msg in warn_msgs), (
            f"Did not expect near-capacity warning, got: {warn_msgs}"
        )
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_rate_limit_count_missing_header_no_crash(
        self,
        fake_redis: fakeredis.aioredis.FakeRedis,
    ) -> None:
        """Missing X-App-Rate-Limit-Count does not crash."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                    # No X-App-Rate-Limit-Count header
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            result = await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        assert result["puuid"] == "test-puuid"
        await fake_redis.aclose()


class TestParseRateLimitCount:
    """R5: Unit tests for _parse_rate_limit_count."""

    def test_valid_count_header(self) -> None:
        from lol_pipeline.riot_api import _parse_rate_limit_count

        assert _parse_rate_limit_count("19:1,85:120") == (19, 85)

    def test_empty_string(self) -> None:
        from lol_pipeline.riot_api import _parse_rate_limit_count

        assert _parse_rate_limit_count("") is None

    def test_malformed_string(self) -> None:
        from lol_pipeline.riot_api import _parse_rate_limit_count

        assert _parse_rate_limit_count("garbage") is None

    def test_missing_window(self) -> None:
        from lol_pipeline.riot_api import _parse_rate_limit_count

        assert _parse_rate_limit_count("19:1") is None  # no 120s window


class TestRiotClientMalformedResponse:
    @pytest.mark.asyncio
    async def test_malformed_json_response__raises(self) -> None:
        """HTTP 200 with non-JSON body raises JSONDecodeError."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
            client = RiotClient("RGAPI-test")
            with pytest.raises(json.JSONDecodeError):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()

    @pytest.mark.asyncio
    async def test_empty_response_body__raises(self) -> None:
        """HTTP 200 with empty body raises JSONDecodeError."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(return_value=httpx.Response(200, text=""))
            client = RiotClient("RGAPI-test")
            with pytest.raises(json.JSONDecodeError):
                await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()

    @pytest.mark.asyncio
    async def test_missing_puuid_in_response__returns_dict_without_key(self) -> None:
        """API returns 200 but no 'puuid' field — dict returned as-is (no validation)."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/X/Y"
            ).mock(return_value=httpx.Response(200, json={"gameName": "X", "tagLine": "Y"}))
            client = RiotClient("RGAPI-test")
            result = await client.get_account_by_riot_id("X", "Y", "na1")
            await client.close()
        assert "puuid" not in result


# ---------------------------------------------------------------------------
# New API methods
# ---------------------------------------------------------------------------


class TestGetSummonerByPuuid:
    @pytest.mark.asyncio
    async def test_resolves_puuid_to_summoner(self) -> None:
        """get_summoner_by_puuid uses platform routing (na1) not regional (americas)."""
        with respx.mock:
            route = respx.get(
                "https://na1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/test-puuid"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "id": "encrypted-summoner-id",
                        "puuid": "test-puuid",
                        "profileIconId": 4567,
                        "summonerLevel": 250,
                    },
                )
            )
            client = RiotClient("RGAPI-test")
            result = await client.get_summoner_by_puuid("test-puuid", "na1")
            await client.close()

        assert route.called
        assert result["id"] == "encrypted-summoner-id"
        assert result["summonerLevel"] == 250

    @pytest.mark.asyncio
    async def test_uses_kr_platform_routing(self) -> None:
        """KR region routes to kr platform, not asia."""
        with respx.mock:
            route = respx.get(
                "https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/kr-puuid"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"id": "kr-id", "puuid": "kr-puuid", "summonerLevel": 100},
                )
            )
            client = RiotClient("RGAPI-test")
            result = await client.get_summoner_by_puuid("kr-puuid", "kr")
            await client.close()

        assert route.called
        assert result["id"] == "kr-id"

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self) -> None:
        with respx.mock:
            respx.get(
                "https://na1.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/unknown"
            ).mock(return_value=httpx.Response(404))
            client = RiotClient("RGAPI-test")
            with pytest.raises(NotFoundError):
                await client.get_summoner_by_puuid("unknown", "na1")
            await client.close()


class TestGetLeagueEntries:
    @pytest.mark.asyncio
    async def test_returns_ranked_entries(self) -> None:
        """get_league_entries uses platform routing and returns a list."""
        with respx.mock:
            route = respx.get(
                "https://na1.api.riotgames.com/lol/league/v4/entries/by-summoner/enc-id-123"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json=[
                        {
                            "queueType": "RANKED_SOLO_5x5",
                            "tier": "DIAMOND",
                            "rank": "II",
                            "leaguePoints": 45,
                        }
                    ],
                )
            )
            client = RiotClient("RGAPI-test")
            result = await client.get_league_entries("enc-id-123", "na1")
            await client.close()

        assert route.called
        assert isinstance(result, list)
        assert result[0]["tier"] == "DIAMOND"

    @pytest.mark.asyncio
    async def test_uses_euw1_platform_routing(self) -> None:
        """EUW1 region routes to euw1 platform, not europe."""
        with respx.mock:
            route = respx.get(
                "https://euw1.api.riotgames.com/lol/league/v4/entries/by-summoner/euw-id"
            ).mock(return_value=httpx.Response(200, json=[]))
            client = RiotClient("RGAPI-test")
            result = await client.get_league_entries("euw-id", "euw1")
            await client.close()

        assert route.called
        assert result == []

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self) -> None:
        with respx.mock:
            respx.get(
                "https://na1.api.riotgames.com/lol/league/v4/entries/by-summoner/bad-id"
            ).mock(return_value=httpx.Response(404))
            client = RiotClient("RGAPI-test")
            with pytest.raises(NotFoundError):
                await client.get_league_entries("bad-id", "na1")
            await client.close()


class TestGetMatchTimeline:
    @pytest.mark.asyncio
    async def test_returns_timeline_data(self) -> None:
        """get_match_timeline uses regional routing (americas) like get_match."""
        with respx.mock:
            route = respx.get(
                "https://americas.api.riotgames.com/lol/match/v5/matches/NA1_1234/timeline"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "metadata": {"matchId": "NA1_1234"},
                        "info": {"frameInterval": 60000, "frames": []},
                    },
                )
            )
            client = RiotClient("RGAPI-test")
            result = await client.get_match_timeline("NA1_1234", "na1")
            await client.close()

        assert route.called
        assert result["metadata"]["matchId"] == "NA1_1234"

    @pytest.mark.asyncio
    async def test_uses_asia_regional_routing_for_kr(self) -> None:
        """KR platform routes to asia regional routing for match timeline."""
        with respx.mock:
            route = respx.get(
                "https://asia.api.riotgames.com/lol/match/v5/matches/KR_9999/timeline"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"metadata": {"matchId": "KR_9999"}, "info": {"frames": []}},
                )
            )
            client = RiotClient("RGAPI-test")
            result = await client.get_match_timeline("KR_9999", "kr")
            await client.close()

        assert route.called
        assert result["metadata"]["matchId"] == "KR_9999"

    @pytest.mark.asyncio
    async def test_uses_europe_regional_routing_for_euw1(self) -> None:
        """EUW1 platform routes to europe regional routing."""
        with respx.mock:
            route = respx.get(
                "https://europe.api.riotgames.com/lol/match/v5/matches/EUW1_555/timeline"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"metadata": {"matchId": "EUW1_555"}, "info": {"frames": []}},
                )
            )
            client = RiotClient("RGAPI-test")
            await client.get_match_timeline("EUW1_555", "euw1")
            await client.close()

        assert route.called

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self) -> None:
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/lol/match/v5/matches/NA1_0000/timeline"
            ).mock(return_value=httpx.Response(404))
            client = RiotClient("RGAPI-test")
            with pytest.raises(NotFoundError):
                await client.get_match_timeline("NA1_0000", "na1")
            await client.close()


class TestThrottleHint:
    """Proactive throttle hint set when near rate limit capacity."""

    @pytest.fixture
    def fake_redis(self) -> fakeredis.aioredis.FakeRedis:
        return fakeredis.aioredis.FakeRedis(decode_responses=True)

    @pytest.mark.asyncio
    async def test_throttle_hint_set_when_near_capacity(
        self,
        fake_redis: fakeredis.aioredis.FakeRedis,
    ) -> None:
        """When < 5% capacity remains, ratelimit:throttle is set in Redis."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/F/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "p", "gameName": "F", "tagLine": "KR1"},
                    headers={
                        "X-App-Rate-Limit": "20:1,100:120",
                        "X-App-Rate-Limit-Count": "19:1,96:120",  # 96/100 = 4% remaining
                    },
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("F", "KR1", "kr")
            await client.close()

        throttle = await fake_redis.get("ratelimit:throttle")
        assert throttle == "1"
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_throttle_hint_not_set_when_under_threshold(
        self,
        fake_redis: fakeredis.aioredis.FakeRedis,
    ) -> None:
        """When plenty of capacity remains, no throttle hint is set."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/F/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "p", "gameName": "F", "tagLine": "KR1"},
                    headers={
                        "X-App-Rate-Limit": "20:1,100:120",
                        "X-App-Rate-Limit-Count": "5:1,30:120",  # lots of headroom
                    },
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("F", "KR1", "kr")
            await client.close()

        throttle = await fake_redis.get("ratelimit:throttle")
        assert throttle is None
        await fake_redis.aclose()

    @pytest.mark.asyncio
    async def test_throttle_hint_has_ttl(
        self,
        fake_redis: fakeredis.aioredis.FakeRedis,
    ) -> None:
        """Throttle hint key expires after a short TTL."""
        with respx.mock:
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/F/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "p", "gameName": "F", "tagLine": "KR1"},
                    headers={
                        "X-App-Rate-Limit": "20:1,100:120",
                        "X-App-Rate-Limit-Count": "19:1,96:120",
                    },
                )
            )
            client = RiotClient("RGAPI-test", r=fake_redis)
            await client.get_account_by_riot_id("F", "KR1", "kr")
            await client.close()

        ttl = await fake_redis.ttl("ratelimit:throttle")
        assert 0 < ttl <= 2
        await fake_redis.aclose()
