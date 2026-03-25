"""Tests for RDB-3: Paginate players:all reads in UI."""

from __future__ import annotations

from unittest.mock import MagicMock

import fakeredis.aioredis
import pytest

from lol_ui.constants import _PLAYERS_PAGE_SIZE
from lol_ui.routes.players import show_players


async def _seed_players(r: fakeredis.aioredis.FakeRedis, count: int) -> None:
    """Seed `count` players into fakeredis."""
    now = 1700000000.0
    for i in range(count):
        puuid = f"puuid-{i}"
        await r.zadd("players:all", {puuid: now - i})
        await r.hset(
            f"player:{puuid}",
            mapping={
                "game_name": f"Player{i}",
                "tag_line": "NA1",
                "region": "na1",
                "seeded_at": "2026-03-19T00:00:00",
            },
        )


class TestPlayersZrevrangePagination:
    """RDB-3: ZREVRANGE should use bounded start/stop, not 0 -1."""

    @pytest.mark.asyncio
    async def test_default_page__calls_zrevrange_with_bounded_range(self) -> None:
        """/players with no page param calls ZREVRANGE 0 (_PLAYERS_PAGE_SIZE - 1)."""
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await _seed_players(r, _PLAYERS_PAGE_SIZE + 5)

        original_zrevrange = r.zrevrange
        calls: list[tuple[object, ...]] = []

        async def spy_zrevrange(*args: object, **kwargs: object) -> object:
            calls.append(args)
            return await original_zrevrange(*args, **kwargs)

        r.zrevrange = spy_zrevrange  # type: ignore[assignment]

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        await show_players(request)

        # Find the call to zrevrange for "players:all"
        players_all_calls = [c for c in calls if c[0] == "players:all"]
        assert len(players_all_calls) == 1, f"Expected 1 zrevrange call, got {players_all_calls}"
        _, start, stop = players_all_calls[0][:3]
        assert start == 0
        assert stop == _PLAYERS_PAGE_SIZE - 1, (
            f"Expected stop={_PLAYERS_PAGE_SIZE - 1}, got {stop} "
            "(should not be -1, which fetches ALL)"
        )
        await r.aclose()

    @pytest.mark.asyncio
    async def test_page_1__calls_zrevrange_with_offset(self) -> None:
        """/players?page=1 calls ZREVRANGE with start=PAGE_SIZE, stop=2*PAGE_SIZE-1."""
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await _seed_players(r, _PLAYERS_PAGE_SIZE * 2 + 5)

        original_zrevrange = r.zrevrange
        calls: list[tuple[object, ...]] = []

        async def spy_zrevrange(*args: object, **kwargs: object) -> object:
            calls.append(args)
            return await original_zrevrange(*args, **kwargs)

        r.zrevrange = spy_zrevrange  # type: ignore[assignment]

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "1"}

        await show_players(request)

        players_all_calls = [c for c in calls if c[0] == "players:all"]
        assert len(players_all_calls) == 1
        _, start, stop = players_all_calls[0][:3]
        assert start == _PLAYERS_PAGE_SIZE
        assert stop == _PLAYERS_PAGE_SIZE * 2 - 1
        await r.aclose()


class TestPlayersPaginationPipelinesOnlyPage:
    """RDB-3: Only pipeline Redis calls for players on the current page."""

    @pytest.mark.asyncio
    async def test_page_0__renders_only_page_size_rows(self) -> None:
        """With 60 players, page 0 should only render PAGE_SIZE table rows."""
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        total = _PLAYERS_PAGE_SIZE * 2 + 10
        await _seed_players(r, total)

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_players(request)
        body = resp.body.decode()

        # Count actual table rows (each player produces one <tr> in tbody)
        row_count = body.count("<tr><td>")
        assert row_count == _PLAYERS_PAGE_SIZE, (
            f"Expected {_PLAYERS_PAGE_SIZE} table rows on page 0, got {row_count}"
        )
        await r.aclose()


class TestPlayersPaginationControls:
    """RDB-3: Pagination controls show total from ZCARD."""

    @pytest.mark.asyncio
    async def test_total_count__uses_zcard(self) -> None:
        """Total player count comes from ZCARD, shown in pagination."""
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        total = _PLAYERS_PAGE_SIZE * 3
        await _seed_players(r, total)

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {}

        resp = await show_players(request)
        body = resp.body.decode()

        total_pages = (total + _PLAYERS_PAGE_SIZE - 1) // _PLAYERS_PAGE_SIZE
        assert f"of {total_pages}" in body or f"/ {total_pages}" in body
        await r.aclose()

    @pytest.mark.asyncio
    async def test_prev_next_links__present_on_middle_page(self) -> None:
        """Middle page shows both prev and next links."""
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        total = _PLAYERS_PAGE_SIZE * 3
        await _seed_players(r, total)

        request = MagicMock()
        request.app.state.r = r
        request.query_params = {"page": "1"}

        resp = await show_players(request)
        body = resp.body.decode()

        # Should have both prev and next
        assert "page=0" in body  # prev link
        assert "page=2" in body  # next link
        await r.aclose()
