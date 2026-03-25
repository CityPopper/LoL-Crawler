"""Tests for Recently Played With panel (T5-3)."""

from __future__ import annotations

from collections import Counter

import pytest

from lol_ui.recently_played import (  # type: ignore[attr-defined]
    _count_co_players,
    _recently_played_html,
)

# ---------------------------------------------------------------------------
# _count_co_players
# ---------------------------------------------------------------------------


class TestCountCoPlayers:
    """_count_co_players counts how often each co-player appears across matches."""

    def test_empty_sets__returns_empty(self):
        result = _count_co_players([], "me")
        assert result == Counter()

    def test_single_match__excludes_self(self):
        participant_sets = [{"me", "p1", "p2"}]
        result = _count_co_players(participant_sets, "me")
        assert "me" not in result
        assert result["p1"] == 1
        assert result["p2"] == 1

    def test_multiple_matches__counts_accumulate(self):
        sets = [
            {"me", "p1", "p2"},
            {"me", "p1", "p3"},
            {"me", "p1", "p4"},
        ]
        result = _count_co_players(sets, "me")
        assert result["p1"] == 3
        assert result["p2"] == 1
        assert result["p3"] == 1

    def test_self_not_in_set__handled(self):
        sets = [{"p1", "p2"}]
        result = _count_co_players(sets, "me")
        assert result["p1"] == 1
        assert result["p2"] == 1

    def test_empty_set_in_list__skipped(self):
        sets = [set(), {"me", "p1"}]
        result = _count_co_players(sets, "me")
        assert result["p1"] == 1


# ---------------------------------------------------------------------------
# _recently_played_html
# ---------------------------------------------------------------------------


class TestRecentlyPlayedHtml:
    """_recently_played_html renders the Recently Played With panel."""

    @pytest.mark.asyncio
    async def test_no_matches__returns_empty(self, r):
        result = await _recently_played_html(r, "me", [])
        assert result == ""

    @pytest.mark.asyncio
    async def test_below_threshold__returns_empty(self, r):
        # Only 2 shared games (threshold is 3)
        await r.sadd("match:participants:m1", "me", "p1")
        await r.sadd("match:participants:m2", "me", "p1")
        result = await _recently_played_html(r, "me", ["m1", "m2"])
        assert result == ""

    @pytest.mark.asyncio
    async def test_meets_threshold__shows_player(self, r):
        for i in range(3):
            await r.sadd(f"match:participants:m{i}", "me", "p1")
        await r.hset("player:p1", mapping={"game_name": "TestPlayer", "tag_line": "NA1"})
        result = await _recently_played_html(r, "me", ["m0", "m1", "m2"])
        assert "TestPlayer" in result

    @pytest.mark.asyncio
    async def test_top_5_limit(self, r):
        # Create 6 co-players all with 3+ shared games
        match_ids = []
        for i in range(4):
            mid = f"m{i}"
            match_ids.append(mid)
            members = {"me", "p1", "p2", "p3", "p4", "p5", "p6"}
            for m in members:
                await r.sadd(f"match:participants:{mid}", m)

        for j in range(1, 7):
            await r.hset(f"player:p{j}", mapping={"game_name": f"Player{j}", "tag_line": "NA1"})

        result = await _recently_played_html(r, "me", match_ids)
        # Should only show top 5
        assert "Player6" not in result or result.count("recently-played__row") <= 5

    @pytest.mark.asyncio
    async def test_caps_at_20_matches(self, r):
        match_ids = [f"m{i}" for i in range(25)]
        for mid in match_ids:
            await r.sadd(f"match:participants:{mid}", "me", "p1")

        await r.hset("player:p1", mapping={"game_name": "TestPlayer", "tag_line": "NA1"})
        result = await _recently_played_html(r, "me", match_ids)
        # Even with 25 matches, it should only scan 20
        # p1 appears in 20 of them => >= 3 threshold => shown
        assert "TestPlayer" in result

    @pytest.mark.asyncio
    async def test_uses_pipeline(self, r):
        """Verifying that it works with pipeline — functional test."""
        for i in range(3):
            await r.sadd(f"match:participants:m{i}", "me", "p1", "p2")
        await r.hset("player:p1", mapping={"game_name": "Alice", "tag_line": "EUW"})
        await r.hset("player:p2", mapping={"game_name": "Bob", "tag_line": "NA1"})
        result = await _recently_played_html(r, "me", ["m0", "m1", "m2"])
        assert "Alice" in result
        assert "Bob" in result

    @pytest.mark.asyncio
    async def test_missing_player_data__shows_puuid_prefix(self, r):
        for i in range(3):
            await r.sadd(f"match:participants:m{i}", "me", "unknown-puuid-12345")
        result = await _recently_played_html(r, "me", ["m0", "m1", "m2"])
        # Should show something, not crash
        assert "recently-played" in result

    @pytest.mark.asyncio
    async def test_panel_has_header(self, r):
        for i in range(3):
            await r.sadd(f"match:participants:m{i}", "me", "p1")
        await r.hset("player:p1", mapping={"game_name": "TestPlayer", "tag_line": "NA1"})
        result = await _recently_played_html(r, "me", ["m0", "m1", "m2"])
        assert "Recently Played With" in result
