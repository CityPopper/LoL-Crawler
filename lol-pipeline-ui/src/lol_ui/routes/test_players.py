"""Tests for players route — rank sort and region filter."""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

from lol_ui.main import app


@pytest.fixture
async def client():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.state.r = r
    # Seed 3 players with different ranks and regions
    now = 1700000000.0
    await r.zadd(  # type: ignore[misc]
        "players:all", {"puuid-a": now, "puuid-b": now - 1, "puuid-c": now - 2}
    )
    await r.hset(  # type: ignore[misc]
        "player:puuid-a",
        mapping={
            "game_name": "Alice",
            "tag_line": "NA1",
            "region": "na1",
            "seeded_at": "2024-01-01T00:00:00",
        },
    )
    await r.hset(  # type: ignore[misc]
        "player:puuid-b",
        mapping={
            "game_name": "Bob",
            "tag_line": "EUW",
            "region": "euw1",
            "seeded_at": "2024-01-02T00:00:00",
        },
    )
    await r.hset(  # type: ignore[misc]
        "player:puuid-c",
        mapping={
            "game_name": "Charlie",
            "tag_line": "KR",
            "region": "kr",
            "seeded_at": "2024-01-03T00:00:00",
        },
    )
    # Rank data: Charlie > Alice > Bob
    await r.hset(  # type: ignore[misc]
        "player:rank:puuid-a",
        mapping={"tier": "GOLD", "division": "II", "lp": "50", "wins": "30", "losses": "20"},
    )
    await r.hset(  # type: ignore[misc]
        "player:rank:puuid-b",
        mapping={"tier": "SILVER", "division": "I", "lp": "75", "wins": "20", "losses": "30"},
    )
    await r.hset(  # type: ignore[misc]
        "player:rank:puuid-c",
        mapping={"tier": "DIAMOND", "division": "IV", "lp": "10", "wins": "100", "losses": "50"},
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await r.aclose()


class TestPlayersRankSort:
    """Players page should sort by rank (tier > division > LP) by default."""

    @pytest.mark.asyncio
    async def test_default_sort__is_rank(self, client: AsyncClient) -> None:
        resp = await client.get("/players")
        body = bytes(resp.content).decode()
        # Diamond should appear before Gold, Gold before Silver
        diamond_pos = body.index("Charlie")
        gold_pos = body.index("Alice")
        silver_pos = body.index("Bob")
        assert diamond_pos < gold_pos < silver_pos

    @pytest.mark.asyncio
    async def test_sort_rank__in_sort_controls(self, client: AsyncClient) -> None:
        resp = await client.get("/players")
        body = bytes(resp.content).decode()
        assert "Rank" in body


class TestPlayersRegionFilter:
    """Players page should support region query param filter."""

    @pytest.mark.asyncio
    async def test_region_filter__shows_only_matching(self, client: AsyncClient) -> None:
        resp = await client.get("/players?region=euw1")
        body = bytes(resp.content).decode()
        assert "Bob" in body
        assert "Alice" not in body
        assert "Charlie" not in body

    @pytest.mark.asyncio
    async def test_region_filter__empty__shows_all(self, client: AsyncClient) -> None:
        resp = await client.get("/players?region=")
        body = bytes(resp.content).decode()
        assert "Alice" in body
        assert "Bob" in body
        assert "Charlie" in body

    @pytest.mark.asyncio
    async def test_region_dropdown__present(self, client: AsyncClient) -> None:
        resp = await client.get("/players")
        body = bytes(resp.content).decode()
        assert "region" in body.lower()
        assert "<select" in body
