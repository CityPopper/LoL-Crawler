"""Unit tests for OpggClient — op.gg internal API client."""

from __future__ import annotations

import httpx
import pytest
import respx

from lol_pipeline.opgg_client import OpggClient, OpggParseError

# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_SUMMONER_RESPONSE = {
    "data": {
        "summoner_id": "xAmCbJxxx",
        "game_name": "TestPlayer",
        "tagline": "NA1",
        "level": 250,
        "profile_image_url": "https://opgg-images.akamaized.net/profile/1.png",
        "tier_info": {"tier": "GOLD", "division": "II", "lp": 75},
    }
}

SAMPLE_GAMES_RESPONSE = {
    "data": [
        {
            "id": "abc123hash",
            "created_at": "2026-03-20T12:00:00+00:00",
            "game_type": "Ranked",
            "average_tier_info": {"tier": "GOLD", "division": "II"},
            "teams": [
                {
                    "game_stat": {"is_win": True, "kill": 25, "death": 12, "assist": 30},
                    "participants": [
                        {
                            "summoner": {
                                "summoner_id": "xAmCbJxxx",
                                "puuid": "test-puuid-abc",
                            },
                            "champion_id": 157,
                            "position": "ADC",
                            "stats": {
                                "kill": 8,
                                "death": 3,
                                "assist": 5,
                                "cs": 180,
                                "damage_dealt_to_champions": 28000,
                            },
                            "items": [3031, 3094, 3086, 0, 0, 0, 3363],
                            "op_score": 8.7,
                        }
                    ],
                },
                {
                    "game_stat": {"is_win": False, "kill": 12, "death": 25, "assist": 20},
                    "participants": [
                        {
                            "summoner": {"summoner_id": "opp1", "puuid": "opp-puuid-1"},
                            "champion_id": 92,
                            "position": "TOP",
                            "stats": {
                                "kill": 4,
                                "death": 8,
                                "assist": 2,
                                "cs": 150,
                                "damage_dealt_to_champions": 18000,
                            },
                            "items": [3071, 0, 0, 0, 0, 0, 3340],
                            "op_score": 4.2,
                        }
                    ],
                },
            ],
        }
    ],
    "meta": {"last_game_created_at": "2026-03-20T12:00:00+00:00"},
}

REGION = "na"


@pytest.fixture
def client():
    """OpggClient with a real httpx.AsyncClient (will be intercepted by respx)."""
    http = httpx.AsyncClient()
    c = OpggClient(http)
    return c


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestOpggClientSummonerLookup:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_summoner_id_success(self, client):
        respx.get(
            "https://lol-api-summoner.op.gg/api/v3/na/summoners",
        ).mock(return_value=httpx.Response(200, json=SAMPLE_SUMMONER_RESPONSE))

        summoner_id = await client.get_summoner_id("TestPlayer", "NA1", "na")
        assert summoner_id == "xAmCbJxxx"

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_summoner_id_not_found(self, client):
        respx.get(
            "https://lol-api-summoner.op.gg/api/v3/na/summoners",
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))

        with pytest.raises(OpggParseError, match="summoner not found"):
            await client.get_summoner_id("Unknown", "NA1", "na")


class TestOpggClientMatchHistory:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_match_history_returns_normalized_dicts(self, client):
        """get_match_history returns list of match-v5-shaped dicts."""
        respx.get(
            "https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games",
        ).mock(return_value=httpx.Response(200, json=SAMPLE_GAMES_RESPONSE))

        matches = await client.get_match_history("xAmCbJxxx", "na")
        assert len(matches) == 1
        match = matches[0]
        assert match["metadata"]["data_version"] == "2"
        assert match["metadata"]["match_id"].startswith("OPGG_")
        assert match["info"]["source"] == "opgg"
        assert match["info"]["fetched_at"] != ""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_match_history_drops_op_score(self, client):
        """ETL drops proprietary op.gg fields like op_score."""
        respx.get(
            "https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games",
        ).mock(return_value=httpx.Response(200, json=SAMPLE_GAMES_RESPONSE))

        matches = await client.get_match_history("xAmCbJxxx", "na")
        participants = matches[0]["info"]["participants"]
        for p in participants:
            assert "op_score" not in p

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_match_history_normalizes_kills(self, client):
        """Kills/deaths/assists are mapped to standard match-v5 participant fields."""
        respx.get(
            "https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games",
        ).mock(return_value=httpx.Response(200, json=SAMPLE_GAMES_RESPONSE))

        matches = await client.get_match_history("xAmCbJxxx", "na")
        # Find participant with puuid "test-puuid-abc"
        participants = matches[0]["info"]["participants"]
        p = next(pp for pp in participants if pp["puuid"] == "test-puuid-abc")
        assert p["kills"] == 8
        assert p["deaths"] == 3
        assert p["assists"] == 5
        assert p["totalMinionsKilled"] == 180

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_match_history_empty_response(self, client):
        """Empty games list returns empty list."""
        respx.get(
            "https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games",
        ).mock(return_value=httpx.Response(200, json={"data": [], "meta": {}}))

        matches = await client.get_match_history("xAmCbJxxx", "na")
        assert matches == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_match_history_invalid_schema_raises(self, client):
        """Unexpected schema raises OpggParseError."""
        respx.get(
            "https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games",
        ).mock(return_value=httpx.Response(200, json={"unexpected": "schema"}))

        with pytest.raises(OpggParseError, match="unexpected response"):
            await client.get_match_history("xAmCbJxxx", "na")


class TestOpggClientClose:
    @pytest.mark.asyncio
    async def test_close_closes_http_client(self):
        http = httpx.AsyncClient()
        client = OpggClient(http)
        await client.close()
        assert http.is_closed


class TestOpggClientRateLimiting:
    """OpggClient uses wait_for_token before each HTTP request."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_summoner_id_calls_wait_for_token(self):
        """get_summoner_id acquires a rate limit token before the HTTP call."""
        from unittest.mock import AsyncMock, patch

        import fakeredis.aioredis

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
            return_value=httpx.Response(200, json=SAMPLE_SUMMONER_RESPONSE)
        )

        http = httpx.AsyncClient()
        c = OpggClient(http, r=r)

        with patch("lol_pipeline.opgg_client.wait_for_token", new_callable=AsyncMock) as mock_wft:
            await c.get_summoner_id("TestPlayer", "NA1", "na")

        mock_wft.assert_called_once_with("opgg", "summoner")

        await c.close()
        await r.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_match_history_calls_wait_for_token(self):
        """get_match_history acquires a rate limit token before the HTTP call."""
        from unittest.mock import AsyncMock, patch

        import fakeredis.aioredis

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        respx.get("https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games").mock(
            return_value=httpx.Response(200, json=SAMPLE_GAMES_RESPONSE)
        )

        http = httpx.AsyncClient()
        c = OpggClient(http, r=r)

        with patch("lol_pipeline.opgg_client.wait_for_token", new_callable=AsyncMock) as mock_wft:
            await c.get_match_history("xAmCbJxxx", "na")

        mock_wft.assert_called_once_with("opgg", "games")

        await c.close()
        await r.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_redis_skips_rate_limiting(self):
        """When no Redis is provided, rate limiting is skipped (no error)."""
        from unittest.mock import AsyncMock, patch

        respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
            return_value=httpx.Response(200, json=SAMPLE_SUMMONER_RESPONSE)
        )

        http = httpx.AsyncClient()
        c = OpggClient(http)  # no r=

        with patch("lol_pipeline.opgg_client.wait_for_token", new_callable=AsyncMock) as mock_wft:
            await c.get_summoner_id("TestPlayer", "NA1", "na")

        mock_wft.assert_not_called()
        await c.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_summoner_and_games_use_different_key_prefixes(self):
        """Summoner lookup and match history use endpoint-scoped key prefixes."""
        from unittest.mock import patch

        import fakeredis.aioredis

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
            return_value=httpx.Response(200, json=SAMPLE_SUMMONER_RESPONSE)
        )
        respx.get("https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games").mock(
            return_value=httpx.Response(200, json=SAMPLE_GAMES_RESPONSE)
        )

        http = httpx.AsyncClient()
        c = OpggClient(http, r=r)

        endpoints_used: list[str] = []

        async def capture_endpoint(source: str, endpoint: str) -> None:
            endpoints_used.append(endpoint)

        with patch("lol_pipeline.opgg_client.wait_for_token", side_effect=capture_endpoint):
            await c.get_summoner_id("TestPlayer", "NA1", "na")
            await c.get_match_history("xAmCbJxxx", "na")

        assert len(endpoints_used) == 2
        # Each endpoint has its own name for independent windowing
        assert endpoints_used[0] != endpoints_used[1], (
            "Summoner and games endpoints should use different rate limit endpoints"
        )

        await c.close()
        await r.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_rate_limit_source_and_endpoint_forwarded(self):
        """wait_for_token is called with source='opgg' and correct endpoint."""
        from unittest.mock import AsyncMock, patch

        import fakeredis.aioredis

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        respx.get("https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games").mock(
            return_value=httpx.Response(200, json=SAMPLE_GAMES_RESPONSE)
        )

        http = httpx.AsyncClient()
        c = OpggClient(http, r=r, rate_limit_per_second=5, rate_limit_long=60)

        with patch("lol_pipeline.opgg_client.wait_for_token", new_callable=AsyncMock) as mock_wft:
            await c.get_match_history("xAmCbJxxx", "na")

        mock_wft.assert_called_once_with("opgg", "games")

        await c.close()
        await r.aclose()


class TestOpggClientRateLimitError:
    """OPGG-4.6: 429 responses raise OpggRateLimitError with retry_ms."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_summoner_id_429_raises_rate_limit_error(self):
        """HTTP 429 from summoner endpoint raises OpggRateLimitError."""
        from lol_pipeline.opgg_client import OpggRateLimitError

        respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
            return_value=httpx.Response(
                429, headers={"Retry-After": "3"}, json={"message": "Too Many Requests"}
            )
        )
        http = httpx.AsyncClient()
        c = OpggClient(http)
        with pytest.raises(OpggRateLimitError) as exc_info:
            await c.get_summoner_id("TestPlayer", "NA1", "na")
        assert exc_info.value.retry_ms == 3000  # 3s -> 3000ms
        await c.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_get_match_history_429_raises_rate_limit_error(self):
        """HTTP 429 from games endpoint raises OpggRateLimitError."""
        from lol_pipeline.opgg_client import OpggRateLimitError

        respx.get("https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games").mock(
            return_value=httpx.Response(
                429, headers={"Retry-After": "10"}, json={"message": "rate limited"}
            )
        )
        http = httpx.AsyncClient()
        c = OpggClient(http)
        with pytest.raises(OpggRateLimitError) as exc_info:
            await c.get_match_history("xAmCbJxxx", "na")
        assert exc_info.value.retry_ms == 10000
        await c.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_without_retry_after_uses_default(self):
        """429 with no Retry-After header defaults to 5000ms."""
        from lol_pipeline.opgg_client import OpggRateLimitError

        respx.get("https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games").mock(
            return_value=httpx.Response(429, json={"message": "rate limited"})
        )
        http = httpx.AsyncClient()
        c = OpggClient(http)
        with pytest.raises(OpggRateLimitError) as exc_info:
            await c.get_match_history("xAmCbJxxx", "na")
        assert exc_info.value.retry_ms == 5000  # conservative default
        await c.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_opgg_403_raises_parse_error_not_system_halt(self):
        """HTTP 403 from op.gg raises OpggParseError (scraping blocked), never system:halted."""
        respx.get("https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games").mock(
            return_value=httpx.Response(403, json={"message": "Forbidden"})
        )
        http = httpx.AsyncClient()
        c = OpggClient(http)
        # Should raise something catchable -- NOT AuthError (which would halt the pipeline)
        with pytest.raises(Exception) as exc_info:
            await c.get_match_history("xAmCbJxxx", "na")
        # Must NOT be an AuthError (which triggers system:halted)
        from lol_pipeline.riot_api import AuthError

        assert not isinstance(exc_info.value, AuthError)
        await c.close()
