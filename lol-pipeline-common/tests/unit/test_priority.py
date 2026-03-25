"""Unit tests for lol_pipeline.priority — O(1) SET-based priority detection."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_pipeline.priority import (
    PRIORITY_ACTIVE_SET,
    PRIORITY_ACTIVE_SET_TTL_SECONDS,
    PRIORITY_AUTO_20,
    PRIORITY_AUTO_NEW,
    PRIORITY_DOWNGRADE_THRESHOLD,
    PRIORITY_KEY_TTL_SECONDS,
    PRIORITY_MANUAL_20,
    PRIORITY_MANUAL_20PLUS,
    PRIORITY_ORDER,
    clear_priority,
    downgrade_priority,
    has_priority_players,
    set_priority,
)


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


class TestSetPriority:
    @pytest.mark.asyncio
    async def test_set_priority__creates_key_with_ttl(self, r):
        """set_priority creates player:priority:{puuid} with a TTL (B12)."""
        await set_priority(r, "puuid-abc")

        val = await r.get("player:priority:puuid-abc")
        assert val == "1"
        ttl = await r.ttl("player:priority:puuid-abc")
        assert ttl > 0  # has expiry — prevents orphaned keys blocking Discovery

    @pytest.mark.asyncio
    async def test_set_priority__idempotent_nx(self, r):
        """SET NX means second call does not overwrite."""
        await set_priority(r, "puuid-abc")
        await set_priority(r, "puuid-abc")
        val = await r.get("player:priority:puuid-abc")
        assert val == "1"

    @pytest.mark.asyncio
    async def test_set_priority__no_counter_key(self, r):
        """set_priority must NOT create system:priority_count (counter removed)."""
        await set_priority(r, "puuid-abc")
        assert await r.exists("system:priority_count") == 0

    @pytest.mark.asyncio
    async def test_set_priority__adds_to_active_set(self, r):
        """set_priority adds puuid to priority:active SET for O(1) lookup."""
        await set_priority(r, "puuid-abc")
        assert await r.sismember(PRIORITY_ACTIVE_SET, "puuid-abc")

    @pytest.mark.asyncio
    async def test_set_priority__duplicate__still_in_active_set(self, r):
        """Calling set_priority twice keeps puuid in priority:active (SADD idempotent)."""
        await set_priority(r, "puuid-abc")
        await set_priority(r, "puuid-abc")
        assert await r.scard(PRIORITY_ACTIVE_SET) == 1


class TestClearPriority:
    @pytest.mark.asyncio
    async def test_clear_priority__deletes_key(self, r):
        """clear_priority deletes the player:priority:{puuid} key."""
        await set_priority(r, "puuid-abc")
        assert await r.get("player:priority:puuid-abc") == "1"

        await clear_priority(r, "puuid-abc")
        assert await r.get("player:priority:puuid-abc") is None

    @pytest.mark.asyncio
    async def test_clear_priority__nonexistent_key__no_error(self, r):
        """Clearing a non-existent key does not raise."""
        await clear_priority(r, "puuid-nonexistent")
        # Should succeed silently

    @pytest.mark.asyncio
    async def test_clear_priority__no_counter_key(self, r):
        """clear_priority must NOT touch system:priority_count (counter removed)."""
        await set_priority(r, "puuid-abc")
        await clear_priority(r, "puuid-abc")
        assert await r.exists("system:priority_count") == 0

    @pytest.mark.asyncio
    async def test_clear_priority__removes_from_active_set(self, r):
        """clear_priority removes puuid from priority:active SET."""
        await set_priority(r, "puuid-abc")
        assert await r.sismember(PRIORITY_ACTIVE_SET, "puuid-abc")
        await clear_priority(r, "puuid-abc")
        assert not await r.sismember(PRIORITY_ACTIVE_SET, "puuid-abc")

    @pytest.mark.asyncio
    async def test_clear_priority__nonexistent__srem_no_error(self, r):
        """Clearing a puuid not in priority:active SET does not raise."""
        await clear_priority(r, "puuid-nonexistent")
        assert await r.scard(PRIORITY_ACTIVE_SET) == 0


class TestSetPriorityIdempotency:
    @pytest.mark.asyncio
    async def test_set_priority__same_puuid_twice__only_one_key(self, r):
        """Calling set_priority twice for the same PUUID creates only one key."""
        await set_priority(r, "puuid-dup")
        await set_priority(r, "puuid-dup")
        assert await r.exists("player:priority:puuid-dup") == 1

    @pytest.mark.asyncio
    async def test_set_priority__different_puuids__creates_separate_keys(self, r):
        """set_priority for distinct PUUIDs creates one key each and all in active SET."""
        await set_priority(r, "puuid-a")
        await set_priority(r, "puuid-b")
        await set_priority(r, "puuid-c")
        assert await r.exists("player:priority:puuid-a") == 1
        assert await r.exists("player:priority:puuid-b") == 1
        assert await r.exists("player:priority:puuid-c") == 1
        assert await r.scard(PRIORITY_ACTIVE_SET) == 3


class TestHasPriorityPlayers:
    @pytest.mark.asyncio
    async def test_has_priority_players__no_keys__returns_false(self, r):
        """With no player:priority:* keys, returns False."""
        result = await has_priority_players(r)
        assert result is False

    @pytest.mark.asyncio
    async def test_has_priority_players__one_key__returns_true(self, r):
        """With one player:priority:* key, returns True."""
        await set_priority(r, "puuid-abc")
        result = await has_priority_players(r)
        assert result is True

    @pytest.mark.asyncio
    async def test_has_priority_players__multiple_keys__returns_true(self, r):
        """With multiple player:priority:* keys, returns True."""
        await set_priority(r, "puuid-1")
        await set_priority(r, "puuid-2")
        await set_priority(r, "puuid-3")
        result = await has_priority_players(r)
        assert result is True

    @pytest.mark.asyncio
    async def test_has_priority_players__after_clear__returns_false(self, r):
        """After clearing the only priority key, returns False."""
        await set_priority(r, "puuid-abc")
        assert await has_priority_players(r) is True
        await clear_priority(r, "puuid-abc")
        assert await has_priority_players(r) is False

    @pytest.mark.asyncio
    async def test_has_priority_players__partial_clear__returns_true(self, r):
        """After clearing one of two priority keys, still returns True."""
        await set_priority(r, "puuid-a")
        await set_priority(r, "puuid-b")
        await clear_priority(r, "puuid-a")
        assert await has_priority_players(r) is True

    @pytest.mark.asyncio
    async def test_has_priority_players__ignores_stale_counter(self, r):
        """A stale system:priority_count key does not affect SCAN-based detection."""
        await r.set("system:priority_count", "99")
        # No actual priority keys exist
        result = await has_priority_players(r)
        assert result is False

    @pytest.mark.asyncio
    async def test_has_priority_players__ignores_non_priority_keys(self, r):
        """Non-priority Redis keys do not affect has_priority_players."""
        await r.set("player:puuid-abc", "data")
        await r.set("player:stats:puuid-abc", "data")
        result = await has_priority_players(r)
        assert result is False


class TestPriorityKeyTTL:
    """B12: player:priority:{puuid} has a TTL to prevent orphaned keys blocking Discovery."""

    @pytest.mark.asyncio
    async def test_set_priority__key_has_ttl(self, r):
        """set_priority creates key WITH TTL — prevents orphaned keys (B12)."""
        await set_priority(r, "puuid-abc")

        val = await r.get("player:priority:puuid-abc")
        assert val == "1"
        ttl = await r.ttl("player:priority:puuid-abc")
        assert 0 < ttl <= 86400  # default 24h TTL


class TestPriorityKeyTTLValue:
    """B12: TTL can be customised and defaults to 24h."""

    @pytest.mark.asyncio
    async def test_set_priority__default_ttl_is_86400(self, r):
        """Default TTL is 86400 seconds (24 hours)."""
        await set_priority(r, "puuid-ttl")
        ttl = await r.ttl("player:priority:puuid-ttl")
        # Allow some tolerance for test execution time
        assert 86390 <= ttl <= 86400

    @pytest.mark.asyncio
    async def test_set_priority__custom_ttl(self, r):
        """Custom TTL is applied when passed explicitly."""
        await set_priority(r, "puuid-custom", ttl=3600)
        ttl = await r.ttl("player:priority:puuid-custom")
        assert 3590 <= ttl <= 3600

    @pytest.mark.asyncio
    async def test_set_priority__duplicate_does_not_reset_ttl(self, r):
        """Calling set_priority twice with same PUUID does not reset the TTL."""
        await set_priority(r, "puuid-dup-ttl", ttl=100)
        # First call sets TTL around 100
        ttl1 = await r.ttl("player:priority:puuid-dup-ttl")
        assert 90 <= ttl1 <= 100
        # Second call (SET NX fails) — TTL stays
        await set_priority(r, "puuid-dup-ttl", ttl=5000)
        ttl2 = await r.ttl("player:priority:puuid-dup-ttl")
        # Should still be close to original TTL, NOT 5000
        assert ttl2 <= 100


class TestPriorityConstants:
    """4-tier priority constants and ordering."""

    def test_constants_are_strings(self):
        """All priority constants are strings."""
        assert isinstance(PRIORITY_MANUAL_20, str)
        assert isinstance(PRIORITY_MANUAL_20PLUS, str)
        assert isinstance(PRIORITY_AUTO_20, str)
        assert isinstance(PRIORITY_AUTO_NEW, str)

    def test_constants_unique(self):
        """All four tier constants are distinct."""
        tiers = {PRIORITY_MANUAL_20, PRIORITY_MANUAL_20PLUS, PRIORITY_AUTO_20, PRIORITY_AUTO_NEW}
        assert len(tiers) == 4

    def test_priority_order__manual_20_highest(self):
        """manual_20 has the highest numeric order."""
        assert PRIORITY_ORDER[PRIORITY_MANUAL_20] == 4

    def test_priority_order__descending(self):
        """Tiers are strictly descending: manual_20 > manual_20plus > auto_20 > auto_new."""
        assert (
            PRIORITY_ORDER[PRIORITY_MANUAL_20]
            > PRIORITY_ORDER[PRIORITY_MANUAL_20PLUS]
            > PRIORITY_ORDER[PRIORITY_AUTO_20]
            > PRIORITY_ORDER[PRIORITY_AUTO_NEW]
        )

    def test_priority_order__backwards_compat_high(self):
        """Legacy 'high' maps to same order as manual_20."""
        assert PRIORITY_ORDER["high"] == PRIORITY_ORDER[PRIORITY_MANUAL_20]

    def test_priority_order__backwards_compat_normal(self):
        """Legacy 'normal' maps to same order as auto_new."""
        assert PRIORITY_ORDER["normal"] == PRIORITY_ORDER[PRIORITY_AUTO_NEW]

    def test_downgrade_threshold_is_20(self):
        """Downgrade threshold is 20 match IDs."""
        assert PRIORITY_DOWNGRADE_THRESHOLD == 20


class TestDowngradePriority:
    """downgrade_priority() maps tier to its lower counterpart."""

    def test_manual_20__downgrades_to_manual_20plus(self):
        assert downgrade_priority(PRIORITY_MANUAL_20) == PRIORITY_MANUAL_20PLUS

    def test_auto_20__downgrades_to_auto_new(self):
        assert downgrade_priority(PRIORITY_AUTO_20) == PRIORITY_AUTO_NEW

    def test_manual_20plus__no_further_downgrade(self):
        """manual_20plus has no downgrade — returns itself."""
        assert downgrade_priority(PRIORITY_MANUAL_20PLUS) == PRIORITY_MANUAL_20PLUS

    def test_auto_new__no_further_downgrade(self):
        """auto_new has no downgrade — returns itself."""
        assert downgrade_priority(PRIORITY_AUTO_NEW) == PRIORITY_AUTO_NEW

    def test_unknown_value__returns_itself(self):
        """Unknown priority string passes through unchanged."""
        assert downgrade_priority("unknown_tier") == "unknown_tier"

    def test_legacy_high__returns_itself(self):
        """Legacy 'high' is not in downgrade map — returns itself."""
        assert downgrade_priority("high") == "high"

    def test_legacy_normal__returns_itself(self):
        """Legacy 'normal' is not in downgrade map — returns itself."""
        assert downgrade_priority("normal") == "normal"


class TestPriorityActiveSetTTL:
    """Bug 1 fix: priority:active SET gets a TTL to prevent orphan accumulation."""

    @pytest.mark.asyncio
    async def test_set_priority__active_set_has_ttl(self, r):
        """set_priority sets a TTL on the priority:active SET itself."""
        await set_priority(r, "puuid-abc")
        ttl = await r.ttl(PRIORITY_ACTIVE_SET)
        assert ttl > 0
        assert ttl <= PRIORITY_ACTIVE_SET_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_active_set_ttl__longer_than_key_ttl(self, r):
        """priority:active SET TTL is longer than individual key TTL (buffer)."""
        assert PRIORITY_ACTIVE_SET_TTL_SECONDS > PRIORITY_KEY_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_set_priority__active_set_ttl_refreshed_on_each_add(self, r):
        """Each set_priority call refreshes the active SET TTL."""
        await set_priority(r, "puuid-a")
        ttl1 = await r.ttl(PRIORITY_ACTIVE_SET)
        await set_priority(r, "puuid-b")
        ttl2 = await r.ttl(PRIORITY_ACTIVE_SET)
        # TTL should be refreshed (close to max)
        assert ttl2 >= ttl1


class TestHasPriorityPlayersOrphanCleanup:
    """Bug 1 fix: has_priority_players spot-checks to clean orphaned SET entries."""

    @pytest.mark.asyncio
    async def test_orphaned_entry__cleaned_and_returns_false(self, r):
        """When priority key expired but puuid remains in SET, spot-check cleans it."""
        # Simulate orphan: add to SET without a corresponding priority key
        await r.sadd(PRIORITY_ACTIVE_SET, "orphan-puuid")
        assert await r.scard(PRIORITY_ACTIVE_SET) == 1

        result = await has_priority_players(r)
        assert result is False
        # Orphan should be removed from the SET
        assert await r.scard(PRIORITY_ACTIVE_SET) == 0

    @pytest.mark.asyncio
    async def test_orphaned_entry__mixed_with_live__returns_true(self, r):
        """When SET has both orphans and live entries, returns True."""
        # Add a live priority player
        await set_priority(r, "live-puuid")
        # Add an orphan (SET member without key)
        await r.sadd(PRIORITY_ACTIVE_SET, "orphan-puuid")
        assert await r.scard(PRIORITY_ACTIVE_SET) == 2

        result = await has_priority_players(r)
        assert result is True

    @pytest.mark.asyncio
    async def test_all_orphaned__returns_false_after_cleanup(self, r):
        """When all SET members are orphans, returns False after spot-check cleanup."""
        # Add only orphans (no corresponding priority keys)
        await r.sadd(PRIORITY_ACTIVE_SET, "orphan-1")

        result = await has_priority_players(r)
        assert result is False
        assert await r.scard(PRIORITY_ACTIVE_SET) == 0

    @pytest.mark.asyncio
    async def test_live_entry__not_removed(self, r):
        """Live priority keys are not removed by spot-check."""
        await set_priority(r, "live-puuid")
        result = await has_priority_players(r)
        assert result is True
        assert await r.sismember(PRIORITY_ACTIVE_SET, "live-puuid")

    @pytest.mark.asyncio
    async def test_multiple_orphans__all_cleaned_within_n_calls(self, r):
        """TCG-8: Multiple orphaned members in priority:active are cleaned up.

        has_priority_players() removes one orphan per call (spot-check samples
        one random member). With 3 orphans and no live keys, calling it up to
        10 times must eventually return False (all orphans removed).

        This documents the O(n) cleanup bound where n = number of orphans.
        """
        # Add 3 orphaned members (SET entries without corresponding priority keys)
        await r.sadd(PRIORITY_ACTIVE_SET, "puuid1", "puuid2", "puuid3")
        # Do NOT create player:priority:{puuid} keys — all are orphans

        assert await r.scard(PRIORITY_ACTIVE_SET) == 3

        # Call has_priority_players up to 10 times — should converge to False
        result = True
        calls = 0
        for _ in range(10):
            calls += 1
            result = await has_priority_players(r)
            if not result:
                break

        assert result is False, (
            f"has_priority_players still returns True after {calls} calls "
            f"with {await r.scard(PRIORITY_ACTIVE_SET)} orphans remaining"
        )
        # All orphans should have been removed
        assert await r.scard(PRIORITY_ACTIVE_SET) == 0
        # Should converge within n+1 calls (3 orphans -> at most 4 calls)
        assert calls <= 4, (
            f"Expected cleanup within 4 calls (3 orphans + 1 final check), took {calls}"
        )
