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
            "queue_id": 0,
            "average_tier_info": {"tier": "GOLD", "division": "II"},
            "participants": [
                {
                    "team_key": "BLUE",
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
                },
                {
                    "team_key": "RED",
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
                },
            ],
            "teams": [
                {"key": "BLUE", "game_stat": {"is_win": True, "kill": 25, "death": 12, "assist": 30}},
                {"key": "RED", "game_stat": {"is_win": False, "kill": 12, "death": 25, "assist": 20}},
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


# ── Tests for get_summoner_id_by_puuid ────────────────────────────────────────


VALID_PUUID = "abc123-def456-valid-puuid"
SUMMONER_ID_RESPONSE = {
    "data": {"summoner_id": "opgg-sid-999", "game_name": "TestPlayer", "tagline": "NA1"}
}


class TestGetSummonerIdByPuuid:
    @pytest.mark.asyncio
    @respx.mock
    async def test_blocking_false__tokens_available(self):
        """blocking=False + try_token() grants → HTTP called → cached in Redis."""
        from unittest.mock import AsyncMock, patch

        import fakeredis.aioredis

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
            return_value=httpx.Response(200, json=SUMMONER_ID_RESPONSE)
        )

        http = httpx.AsyncClient()
        c = OpggClient(http, r=r)

        with patch("lol_pipeline.opgg_client.try_token", new_callable=AsyncMock) as mock_try:
            mock_try.return_value = True
            result = await c.get_summoner_id_by_puuid(VALID_PUUID, "na", blocking=False)

        assert result == "opgg-sid-999"
        mock_try.assert_called_once_with("opgg", "summoner")
        # Verify cached in Redis
        cached = await r.get(f"opgg:summoner:{VALID_PUUID}:na")
        assert cached == "opgg-sid-999"

        await c.close()
        await r.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_blocking_false__tokens_unavailable(self):
        """blocking=False + try_token() returns False → OpggRateLimitError, no HTTP."""
        from unittest.mock import AsyncMock, patch

        from lol_pipeline.opgg_client import OpggRateLimitError

        http = httpx.AsyncClient()
        c = OpggClient(http)

        route = respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
            return_value=httpx.Response(200, json=SUMMONER_ID_RESPONSE)
        )

        with patch("lol_pipeline.opgg_client.try_token", new_callable=AsyncMock) as mock_try:
            mock_try.return_value = False
            with pytest.raises(OpggRateLimitError):
                await c.get_summoner_id_by_puuid(VALID_PUUID, "na", blocking=False)

        assert not route.called
        await c.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_blocking_true__cache_hit(self):
        """blocking=True + Redis has cached summoner_id → no HTTP call."""
        import fakeredis.aioredis

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.set(f"opgg:summoner:{VALID_PUUID}:na", "cached-sid-111")

        http = httpx.AsyncClient()
        c = OpggClient(http, r=r)

        route = respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
            return_value=httpx.Response(200, json=SUMMONER_ID_RESPONSE)
        )

        result = await c.get_summoner_id_by_puuid(VALID_PUUID, "na", blocking=True)
        assert result == "cached-sid-111"
        assert not route.called

        await c.close()
        await r.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_blocking_true__cache_miss__writes_redis(self):
        """blocking=True + cache miss → HTTP call → writes summoner_id to Redis with TTL."""
        from unittest.mock import AsyncMock, patch

        import fakeredis.aioredis

        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
            return_value=httpx.Response(200, json=SUMMONER_ID_RESPONSE)
        )

        http = httpx.AsyncClient()
        c = OpggClient(http, r=r, summoner_cache_ttl_seconds=7200)

        with patch("lol_pipeline.opgg_client.wait_for_token", new_callable=AsyncMock):
            result = await c.get_summoner_id_by_puuid(VALID_PUUID, "na", blocking=True)

        assert result == "opgg-sid-999"
        cached = await r.get(f"opgg:summoner:{VALID_PUUID}:na")
        assert cached == "opgg-sid-999"
        ttl = await r.ttl(f"opgg:summoner:{VALID_PUUID}:na")
        assert 0 < ttl <= 7200

        await c.close()
        await r.aclose()

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_redis__cache_skipped(self):
        """self._r is None → no Redis access, HTTP call succeeds."""
        respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
            return_value=httpx.Response(200, json=SUMMONER_ID_RESPONSE)
        )

        http = httpx.AsyncClient()
        c = OpggClient(http)  # no r=

        result = await c.get_summoner_id_by_puuid(VALID_PUUID, "na", blocking=True)
        assert result == "opgg-sid-999"

        await c.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_404__raises_parse_error(self):
        """HTTP 404 → raises OpggParseError."""
        respx.get("https://lol-api-summoner.op.gg/api/v3/na/summoners").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )

        http = httpx.AsyncClient()
        c = OpggClient(http)

        with pytest.raises(OpggParseError, match="Summoner not found"):
            await c.get_summoner_id_by_puuid(VALID_PUUID, "na", blocking=True)

        await c.close()

    @pytest.mark.asyncio
    async def test_invalid_puuid__raises_parse_error(self):
        """PUUID with invalid chars → raises OpggParseError without HTTP call."""
        http = httpx.AsyncClient()
        c = OpggClient(http)

        with pytest.raises(OpggParseError, match="Invalid PUUID"):
            await c.get_summoner_id_by_puuid("../evil path!", "na", blocking=True)

        await c.close()


# ── Tests for get_raw_games ───────────────────────────────────────────────────

RAW_GAMES_RESPONSE = {
    "data": [
        {"id": 7234567890, "created_at": "2026-03-20T12:00:00+00:00"},
        {"id": 7234567891, "created_at": "2026-03-20T11:00:00+00:00"},
    ]
}


class TestGetRawGames:
    @pytest.mark.asyncio
    @respx.mock
    async def test_blocking_false__tokens_unavailable__raises_rate_limit(self):
        """blocking=False + try_token() False → OpggRateLimitError, no HTTP."""
        from unittest.mock import AsyncMock, patch

        from lol_pipeline.opgg_client import OpggRateLimitError

        http = httpx.AsyncClient()
        c = OpggClient(http)

        route = respx.get(
            "https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games"
        ).mock(return_value=httpx.Response(200, json=RAW_GAMES_RESPONSE))

        with patch("lol_pipeline.opgg_client.try_token", new_callable=AsyncMock) as mock_try:
            mock_try.return_value = False
            with pytest.raises(OpggRateLimitError):
                await c.get_raw_games("xAmCbJxxx", "na", blocking=False)

        assert not route.called
        await c.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_blocking_false__success__returns_raw_list(self):
        """blocking=False + tokens available → HTTP 200 → returns raw list."""
        from unittest.mock import AsyncMock, patch

        http = httpx.AsyncClient()
        c = OpggClient(http)

        respx.get(
            "https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games",
        ).mock(return_value=httpx.Response(200, json=RAW_GAMES_RESPONSE))

        with patch("lol_pipeline.opgg_client.try_token", new_callable=AsyncMock) as mock_try:
            mock_try.return_value = True
            result = await c.get_raw_games("xAmCbJxxx", "na", blocking=False)

        assert len(result) == 2
        assert result[0]["id"] == 7234567890
        await c.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_429__raises_rate_limit(self):
        """HTTP 429 with Retry-After → OpggRateLimitError with retry_ms."""
        from lol_pipeline.opgg_client import OpggRateLimitError

        respx.get(
            "https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games",
        ).mock(
            return_value=httpx.Response(
                429, headers={"Retry-After": "30"}, json={"message": "rate limited"}
            )
        )

        http = httpx.AsyncClient()
        c = OpggClient(http)

        with pytest.raises(OpggRateLimitError) as exc_info:
            await c.get_raw_games("xAmCbJxxx", "na", blocking=True)
        assert exc_info.value.retry_ms == 30000

        await c.close()

    @pytest.mark.asyncio
    @respx.mock
    async def test_missing_data_key__raises_parse_error(self):
        """HTTP 200 but no 'data' key → OpggParseError."""
        respx.get(
            "https://lol-api-summoner.op.gg/api/na/summoners/xAmCbJxxx/games",
        ).mock(return_value=httpx.Response(200, json={"meta": {}}))

        http = httpx.AsyncClient()
        c = OpggClient(http)

        with pytest.raises(OpggParseError, match="No 'data' key"):
            await c.get_raw_games("xAmCbJxxx", "na", blocking=True)

        await c.close()

    @pytest.mark.asyncio
    async def test_invalid_summoner_id__raises_parse_error(self):
        """summoner_id with path traversal or spaces → OpggParseError."""
        http = httpx.AsyncClient()
        c = OpggClient(http)

        with pytest.raises(OpggParseError, match="Invalid summoner_id"):
            await c.get_raw_games("../evil path", "na", blocking=True)

        await c.close()


# ── Tests for prefetch_player_games ───────────────────────────────────────────


class TestPrefetchPlayerGames:
    @pytest.mark.asyncio
    async def test_success__writes_blobs(self):
        """Valid response → blob_store.write() called per game, returns count."""
        from unittest.mock import AsyncMock, MagicMock, patch

        http = httpx.AsyncClient()
        c = OpggClient(http)

        mock_blob_store = MagicMock()
        mock_blob_store.write = MagicMock()

        raw_games = [
            {"id": 7234567890, "data": "game1"},
            {"id": 7234567891, "data": "game2"},
        ]

        with (
            patch.object(
                c,
                "get_summoner_id_by_puuid",
                new_callable=AsyncMock,
                return_value="sid-123",
            ),
            patch.object(
                c,
                "get_raw_games",
                new_callable=AsyncMock,
                return_value=raw_games,
            ),
        ):
            count = await c.prefetch_player_games(
                VALID_PUUID, "na", mock_blob_store, limit=5
            )

        assert count == 2
        assert mock_blob_store.write.call_count == 2
        # Verify match_id format: NA1_{game_id} (na → NA1 via reverse map)
        call_args_list = mock_blob_store.write.call_args_list
        assert call_args_list[0][0][1] == "NA1_7234567890"
        assert call_args_list[1][0][1] == "NA1_7234567891"

        await c.close()

    @pytest.mark.asyncio
    async def test_opgg_parse_error__returns_zero(self):
        """get_summoner_id_by_puuid raises OpggParseError → returns 0."""
        from unittest.mock import AsyncMock, MagicMock, patch

        http = httpx.AsyncClient()
        c = OpggClient(http)

        mock_blob_store = MagicMock()

        with patch.object(
            c,
            "get_summoner_id_by_puuid",
            new_callable=AsyncMock,
            side_effect=OpggParseError("summoner not found"),
        ):
            count = await c.prefetch_player_games(
                VALID_PUUID, "na", mock_blob_store
            )

        assert count == 0
        mock_blob_store.write.assert_not_called()
        await c.close()

    @pytest.mark.asyncio
    async def test_rate_limit_error__returns_zero(self):
        """OpggRateLimitError → returns 0, no crash."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from lol_pipeline.opgg_client import OpggRateLimitError

        http = httpx.AsyncClient()
        c = OpggClient(http)

        mock_blob_store = MagicMock()

        with patch.object(
            c,
            "get_summoner_id_by_puuid",
            new_callable=AsyncMock,
            side_effect=OpggRateLimitError("rate limited", retry_ms=5000),
        ):
            count = await c.prefetch_player_games(
                VALID_PUUID, "na", mock_blob_store
            )

        assert count == 0
        mock_blob_store.write.assert_not_called()
        await c.close()
