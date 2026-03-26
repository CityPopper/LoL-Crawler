"""Unit tests for IMP-054, IMP-055, IMP-056 fixes in _helpers.py."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_admin._helpers import (
    ARCHIVE_BATCH_SIZE,
    _backfill_batch,
    _build_eval_args,
    _dlq_archive_entries,
    _scan_parsed_matches,
)

_ARCHIVE_STREAM = "stream:dlq:archive"


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


# ---------------------------------------------------------------------------
# IMP-054: _scan_parsed_matches pipelines HGETs
# ---------------------------------------------------------------------------


class TestScanParsedMatchesPipeline:
    """IMP-054: _scan_parsed_matches should pipeline HGETs, not issue N+1 calls."""

    async def test_empty__returns_empty_set(self, r):
        """No match:* keys -> empty set."""
        result = await _scan_parsed_matches(r)
        assert result == set()

    async def test_returns_only_parsed_matches(self, r):
        """Only match IDs with status=parsed are included."""
        await r.hset("match:NA1_001", mapping={"status": "parsed", "queue_id": "420"})
        await r.hset("match:NA1_002", mapping={"status": "failed", "queue_id": "420"})
        await r.hset("match:NA1_003", mapping={"status": "parsed", "queue_id": "420"})
        result = await _scan_parsed_matches(r)
        assert result == {"NA1_001", "NA1_003"}

    async def test_skips_nested_keys(self, r):
        """Keys with more than one colon (match:participants:*, match:status:*) are skipped."""
        await r.hset("match:NA1_001", mapping={"status": "parsed"})
        await r.hset("match:participants:NA1_001", mapping={"p1": "puuid1"})
        await r.hset("match:status:parsed", mapping={"NA1_001": "1"})
        result = await _scan_parsed_matches(r)
        assert result == {"NA1_001"}

    async def test_uses_pipeline_not_sequential_hgets(self, r):
        """Verify the implementation uses a pipeline for HGETs."""
        await r.hset("match:NA1_001", mapping={"status": "parsed"})
        await r.hset("match:NA1_002", mapping={"status": "parsed"})

        # Spy on the pipeline method to confirm it is used
        original_pipeline = r.pipeline

        pipeline_call_count = 0

        def counting_pipeline(*args, **kwargs):
            nonlocal pipeline_call_count
            pipeline_call_count += 1
            return original_pipeline(*args, **kwargs)

        r.pipeline = counting_pipeline
        result = await _scan_parsed_matches(r)
        assert len(result) == 2
        # At least one pipeline call was made for the HGETs
        assert pipeline_call_count >= 1

    async def test_many_matches__all_collected(self, r):
        """Verifies pipeline handles a larger batch correctly."""
        for i in range(50):
            await r.hset(f"match:NA1_{i:04d}", mapping={"status": "parsed"})
        result = await _scan_parsed_matches(r)
        assert len(result) == 50


# ---------------------------------------------------------------------------
# IMP-055: _backfill_batch pipelines EVALs
# ---------------------------------------------------------------------------


def _setup_ranked_match(r, match_id, patch="14.1", game_start="1700000000000"):
    """Helper to set up a ranked match with metadata and participants."""

    async def _setup():
        await r.hset(f"match:{match_id}", mapping={
            "queue_id": "420",
            "patch": patch,
            "game_start": game_start,
            "status": "parsed",
        })

    return _setup()


def _setup_participant(r, match_id, puuid, champion, role, win="1"):
    """Helper to set up a participant hash."""

    async def _setup():
        await r.hset(f"participant:{match_id}:{puuid}", mapping={
            "champion_name": champion,
            "team_position": role,
            "win": win,
            "kills": "5",
            "deaths": "2",
            "assists": "7",
            "gold_earned": "12000",
            "total_minions_killed": "180",
            "total_damage_dealt_to_champions": "25000",
            "vision_score": "30",
            "double_kills": "1",
            "triple_kills": "0",
            "quadra_kills": "0",
            "penta_kills": "0",
        })

    return _setup()


class TestBuildEvalArgs:
    """_build_eval_args constructs correct Lua EVAL arguments."""

    def test_valid_participant(self):
        p = {
            "champion_name": "Jinx",
            "team_position": "BOTTOM",
            "win": "1",
            "kills": "10",
            "deaths": "3",
            "assists": "5",
        }
        result = _build_eval_args(p, "14.1", "1700000000000", 7776000)
        assert result is not None
        stats_key, index_key, index_member, argv = result
        assert stats_key == "champion:stats:Jinx:14.1:BOTTOM"
        assert index_key == "champion:index:14.1"
        assert index_member == "Jinx:BOTTOM"
        assert argv[0] == 1  # win
        assert argv[1] == 10  # kills

    def test_missing_champion__returns_none(self):
        p = {"team_position": "TOP", "win": "1"}
        assert _build_eval_args(p, "14.1", "0", 100) is None

    def test_missing_position__returns_none(self):
        p = {"champion_name": "Jinx", "win": "1"}
        assert _build_eval_args(p, "14.1", "0", 100) is None


class TestBackfillBatchPipeline:
    """IMP-055: _backfill_batch pipelines Lua EVALs for participants."""

    async def test_basic_backfill__counts_ranked_match(self, r):
        """A single ranked match with participants returns count=1."""
        await _setup_ranked_match(r, "NA1_001")
        await _setup_participant(r, "NA1_001", "puuid1", "Jinx", "BOTTOM")
        await _setup_participant(r, "NA1_001", "puuid2", "Thresh", "UTILITY")

        count = await _backfill_batch(r, ["NA1_001"])
        assert count == 1

    async def test_backfill__writes_champion_stats(self, r):
        """Backfill writes champion stats keys via Lua EVAL."""
        await _setup_ranked_match(r, "NA1_001", patch="14.1")
        await _setup_participant(r, "NA1_001", "puuid1", "Jinx", "BOTTOM", win="1")

        await _backfill_batch(r, ["NA1_001"])

        stats = await r.hgetall("champion:stats:Jinx:14.1:BOTTOM")
        assert stats.get("games") == "1"
        assert stats.get("wins") == "1"
        assert stats.get("kills") == "5"

    async def test_backfill__skips_non_ranked(self, r):
        """Non-ranked (queue_id != 420) matches are skipped."""
        await r.hset("match:NA1_001", mapping={
            "queue_id": "450",  # ARAM, not ranked
            "patch": "14.1",
            "game_start": "0",
        })
        await _setup_participant(r, "NA1_001", "puuid1", "Jinx", "BOTTOM")

        count = await _backfill_batch(r, ["NA1_001"])
        assert count == 0

    async def test_backfill__skips_no_patch(self, r):
        """Matches without a patch field are skipped."""
        await r.hset("match:NA1_001", mapping={
            "queue_id": "420",
            "patch": "",
            "game_start": "0",
        })
        await _setup_participant(r, "NA1_001", "puuid1", "Jinx", "BOTTOM")

        count = await _backfill_batch(r, ["NA1_001"])
        assert count == 0

    async def test_backfill__multiple_participants_pipelined(self, r):
        """Multiple participants in same match all get stats written."""
        await _setup_ranked_match(r, "NA1_001", patch="14.1")
        await _setup_participant(r, "NA1_001", "puuid1", "Jinx", "BOTTOM")
        await _setup_participant(r, "NA1_001", "puuid2", "Thresh", "UTILITY")
        await _setup_participant(r, "NA1_001", "puuid3", "Ahri", "MIDDLE")

        count = await _backfill_batch(r, ["NA1_001"])
        assert count == 1

        # All three champion stats should exist
        jinx_stats = await r.hgetall("champion:stats:Jinx:14.1:BOTTOM")
        assert jinx_stats.get("games") == "1"
        thresh_stats = await r.hgetall("champion:stats:Thresh:14.1:UTILITY")
        assert thresh_stats.get("games") == "1"
        ahri_stats = await r.hgetall("champion:stats:Ahri:14.1:MIDDLE")
        assert ahri_stats.get("games") == "1"

    async def test_backfill__uses_pipeline_for_evals(self, r):
        """Verify pipeline is used for EVAL calls (not sequential)."""
        await _setup_ranked_match(r, "NA1_001", patch="14.1")
        await _setup_participant(r, "NA1_001", "puuid1", "Jinx", "BOTTOM")
        await _setup_participant(r, "NA1_001", "puuid2", "Thresh", "UTILITY")

        original_pipeline = r.pipeline
        pipeline_call_count = 0

        def counting_pipeline(*args, **kwargs):
            nonlocal pipeline_call_count
            pipeline_call_count += 1
            return original_pipeline(*args, **kwargs)

        r.pipeline = counting_pipeline
        await _backfill_batch(r, ["NA1_001"])
        # At least 3 pipeline calls: metadata fetch, participant fetch, EVAL batch
        assert pipeline_call_count >= 3


# ---------------------------------------------------------------------------
# IMP-056: _dlq_archive_entries uses COUNT-capped XRANGE
# ---------------------------------------------------------------------------


class TestDlqArchiveEntriesBatched:
    """IMP-056: _dlq_archive_entries caps XRANGE and paginates."""

    async def test_empty_archive__returns_empty_list(self, r):
        result = await _dlq_archive_entries(r)
        assert result == []

    async def test_small_archive__returns_all(self, r):
        """Fewer entries than ARCHIVE_BATCH_SIZE returns all entries."""
        for i in range(5):
            await r.xadd(_ARCHIVE_STREAM, {"failure_code": f"code_{i}"})
        result = await _dlq_archive_entries(r)
        assert len(result) == 5

    async def test_entry_fields_preserved(self, r):
        """Entry IDs and fields are correctly returned."""
        entry_id = await r.xadd(
            _ARCHIVE_STREAM, {"failure_code": "http_429", "reason": "rate limited"}
        )
        result = await _dlq_archive_entries(r)
        assert len(result) == 1
        assert result[0][0] == entry_id
        assert result[0][1]["failure_code"] == "http_429"
        assert result[0][1]["reason"] == "rate limited"

    async def test_batched_retrieval__large_stream(self, r, monkeypatch):
        """When entries exceed batch size, function loops to get all of them."""
        # Temporarily reduce batch size to make the test fast
        monkeypatch.setattr("lol_admin._helpers.ARCHIVE_BATCH_SIZE", 10)

        for i in range(25):
            await r.xadd(_ARCHIVE_STREAM, {"failure_code": f"code_{i}"})
        result = await _dlq_archive_entries(r)
        assert len(result) == 25

    async def test_xrange_called_with_count(self, r):
        """Verify XRANGE is called with count parameter (not unbounded)."""
        for i in range(3):
            await r.xadd(_ARCHIVE_STREAM, {"failure_code": f"code_{i}"})

        original_xrange = r.xrange
        xrange_calls = []

        async def spy_xrange(*args, **kwargs):
            xrange_calls.append(kwargs)
            return await original_xrange(*args, **kwargs)

        r.xrange = spy_xrange
        await _dlq_archive_entries(r)
        # Every XRANGE call should have a count parameter
        assert all("count" in call for call in xrange_calls)
        assert all(call["count"] == ARCHIVE_BATCH_SIZE for call in xrange_calls)

    async def test_no_duplicate_entries_across_batches(self, r, monkeypatch):
        """Cursor advancement prevents duplicate entries across batches."""
        monkeypatch.setattr("lol_admin._helpers.ARCHIVE_BATCH_SIZE", 5)

        for i in range(12):
            await r.xadd(_ARCHIVE_STREAM, {"idx": str(i)})
        result = await _dlq_archive_entries(r)
        # All entries unique
        entry_ids = [eid for eid, _ in result]
        assert len(entry_ids) == len(set(entry_ids)) == 12

    async def test_archive_batch_size_constant(self):
        """ARCHIVE_BATCH_SIZE is 500 by default."""
        assert ARCHIVE_BATCH_SIZE == 500
