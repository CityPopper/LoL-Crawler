"""Unit tests for lol_pipeline.riot_api — rate limit header parsing and HTTP service calls."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

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
    """RL-4: _persist_rate_limits forwards headers to POST /headers on the rate-limiter service."""

    @pytest.mark.asyncio
    async def test_persist_rate_limits__calls_post_headers(self) -> None:
        # Input:  200 response with X-App-Rate-Limit and X-App-Rate-Limit-Count headers
        # Output: POST /headers called on rate-limiter service (not Redis SET)
        mock_rl_response = httpx.Response(200, json={"updated": True, "throttle": False})
        mock_rl_client = AsyncMock()
        mock_rl_client.post = AsyncMock(return_value=mock_rl_response)

        with (
            respx.mock,
            patch("lol_pipeline.riot_api._get_rl_client", return_value=mock_rl_client),
        ):
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                    headers={
                        "X-App-Rate-Limit": "20:1,100:120",
                        "X-App-Rate-Limit-Count": "15:1,42:120",
                    },
                )
            )
            client = RiotClient("RGAPI-test")
            await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        mock_rl_client.post.assert_called_once_with(
            "/headers",
            json={
                "domain": "riot:asia",
                "rate_limit": "20:1,100:120",
                "rate_limit_count": "15:1,42:120",
            },
        )

    @pytest.mark.asyncio
    async def test_no_post_when_header_absent(self) -> None:
        # Input:  200 response WITHOUT X-App-Rate-Limit header
        # Output: POST /headers is NOT called
        mock_rl_client = AsyncMock()
        mock_rl_client.post = AsyncMock()

        with (
            respx.mock,
            patch("lol_pipeline.riot_api._get_rl_client", return_value=mock_rl_client),
        ):
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                )
            )
            client = RiotClient("RGAPI-test")
            await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        mock_rl_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_post_on_non_200(self) -> None:
        # Input:  404 response (raises NotFoundError)
        # Output: POST /headers is NOT called (_persist_rate_limits only runs after 200)
        mock_rl_client = AsyncMock()
        mock_rl_client.post = AsyncMock()

        with (
            respx.mock,
            patch("lol_pipeline.riot_api._get_rl_client", return_value=mock_rl_client),
        ):
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Nobody/NA1"
            ).mock(return_value=httpx.Response(404))
            client = RiotClient("RGAPI-test")
            with pytest.raises(NotFoundError):
                await client.get_account_by_riot_id("Nobody", "NA1", "na1")
            await client.close()

        mock_rl_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_crash_without_redis(self) -> None:
        # Input:  200 response with X-App-Rate-Limit header
        #         RiotClient constructed WITHOUT r (r=None, the default)
        # Output: returns data dict normally, no exception raised
        mock_rl_response = httpx.Response(200, json={"updated": True, "throttle": False})
        mock_rl_client = AsyncMock()
        mock_rl_client.post = AsyncMock(return_value=mock_rl_response)

        with (
            respx.mock,
            patch("lol_pipeline.riot_api._get_rl_client", return_value=mock_rl_client),
        ):
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
    async def test_forwards_headers_on_subsequent_calls(self) -> None:
        # Input:  first call returns X-App-Rate-Limit: 20:1,100:120
        #         second call returns X-App-Rate-Limit: 100:1,1000:120
        # Output: POST /headers called twice with the respective raw header strings
        mock_rl_response = httpx.Response(200, json={"updated": True, "throttle": False})
        mock_rl_client = AsyncMock()
        mock_rl_client.post = AsyncMock(return_value=mock_rl_response)

        with (
            respx.mock,
            patch("lol_pipeline.riot_api._get_rl_client", return_value=mock_rl_client),
        ):
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
            client = RiotClient("RGAPI-test")
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.get_account_by_riot_id("Test", "NA1", "na1")
            await client.close()

        assert mock_rl_client.post.call_count == 2


class TestRateLimiterServiceFailOpen:
    """RL-4: _persist_rate_limits fails open when the rate-limiter service is unreachable."""

    @pytest.mark.asyncio
    async def test_fail_open_on_service_error(self) -> None:
        """If POST /headers raises, a warning is logged and the call succeeds."""
        mock_rl_client = AsyncMock()
        mock_rl_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        with (
            respx.mock,
            patch("lol_pipeline.riot_api._get_rl_client", return_value=mock_rl_client),
        ):
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                )
            )
            client = RiotClient("RGAPI-test")
            result = await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        # Should still return the result — fail open
        assert result["puuid"] == "test-puuid"

    @pytest.mark.asyncio
    async def test_fail_open_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """If POST /headers fails, a warning is logged."""
        mock_rl_client = AsyncMock()
        mock_rl_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        with (
            respx.mock,
            patch("lol_pipeline.riot_api._get_rl_client", return_value=mock_rl_client),
        ):
            respx.get(
                "https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "test-puuid", "gameName": "Faker", "tagLine": "KR1"},
                    headers={"X-App-Rate-Limit": "20:1,100:120"},
                )
            )
            client = RiotClient("RGAPI-test")
            with caplog.at_level(logging.WARNING, logger="riot_api"):
                await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("POST /headers failed" in msg for msg in warn_msgs)


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

    @pytest.mark.asyncio
    async def test_rate_limit_count_missing_header_no_crash(self) -> None:
        """Missing X-App-Rate-Limit-Count does not crash."""
        mock_rl_response = httpx.Response(200, json={"updated": True, "throttle": False})
        mock_rl_client = AsyncMock()
        mock_rl_client.post = AsyncMock(return_value=mock_rl_response)

        with (
            respx.mock,
            patch("lol_pipeline.riot_api._get_rl_client", return_value=mock_rl_client),
        ):
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
            client = RiotClient("RGAPI-test")
            result = await client.get_account_by_riot_id("Faker", "KR1", "kr")
            await client.close()

        assert result["puuid"] == "test-puuid"
        # rate_limit_count should be empty string in the payload
        call_json = mock_rl_client.post.call_args[1]["json"]
        assert call_json["rate_limit_count"] == ""


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
    """RL-4: Throttle sleep removed — _persist_rate_limits just POSTs headers, no sleep."""

    @pytest.mark.asyncio
    async def test_no_throttle_sleep_regardless_of_response(self) -> None:
        """POST /headers is called with the correct domain but no sleep is triggered."""
        mock_rl_response = httpx.Response(200, json={"updated": True, "throttle": True})
        mock_rl_client = AsyncMock()
        mock_rl_client.post = AsyncMock(return_value=mock_rl_response)

        with (
            respx.mock,
            patch("lol_pipeline.riot_api._get_rl_client", return_value=mock_rl_client),
        ):
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
            client = RiotClient("RGAPI-test")
            result = await client.get_account_by_riot_id("F", "KR1", "kr")
            await client.close()

        # POST /headers was called with domain derived from hostname
        mock_rl_client.post.assert_called_once_with(
            "/headers",
            json={
                "domain": "riot:asia",
                "rate_limit": "20:1,100:120",
                "rate_limit_count": "19:1,96:120",
            },
        )
        assert result["puuid"] == "p"
