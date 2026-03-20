"""Unit tests for lol_pipeline.riot_api — rate limit header parsing and storage."""

from __future__ import annotations

import json

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

    def test_unrecognised_windows(self) -> None:
        # Input:  "500:1,30000:600"  (1-second window present, but 120-second absent)
        # Output: None — 600-second window is not the 2-minute window
        assert _parse_app_rate_limit("500:1,30000:600") is None


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
        """B15: Each successful API call refreshes the TTL on limit keys."""
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
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.close()

        # After second call, TTL should be refreshed back to ~3600
        short_ttl = await fake_redis.ttl("ratelimit:limits:short")
        assert short_ttl > 100  # Was refreshed, not still at 100
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
