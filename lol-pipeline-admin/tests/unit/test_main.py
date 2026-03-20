"""Unit tests for lol_admin.main — Phase 06 ACs 06-01 through 06-11."""

from __future__ import annotations

import argparse
import json
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import httpx
import pytest
import respx
from lol_pipeline.config import Config
from lol_pipeline.models import DLQEnvelope
from lol_pipeline.riot_api import RiotClient

from lol_admin.main import (
    _dispatch,
    _dlq_entries,
    _make_replay_envelope,
    _region_from_match_id,
    _resolve_puuid,
    cmd_dlq_clear,
    cmd_dlq_list,
    cmd_dlq_replay,
    cmd_recalc_priority,
    cmd_replay_fetch,
    cmd_replay_parse,
    cmd_reseed,
    cmd_stats,
    cmd_system_halt,
    cmd_system_resume,
    main,
)

_DLQ_STREAM = "stream:dlq"


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    return Config(_env_file=None)  # type: ignore[call-arg]


def _make_dlq(failure_code="http_429", match_id="NA1_123"):
    return DLQEnvelope(
        source_stream="stream:dlq",
        type="dlq",
        payload={"match_id": match_id, "region": "na1"},
        attempts=3,
        max_attempts=5,
        failure_code=failure_code,
        failure_reason="test",
        failed_by="fetcher",
        original_stream="stream:match_id",
        original_message_id="1234-0",
    )


async def _add_dlq_entries(r, count=1):
    """Add DLQ entries to stream:dlq, return list of entry IDs."""
    ids = []
    for i in range(count):
        dlq = _make_dlq(match_id=f"NA1_{i}")
        entry_id = await r.xadd(_DLQ_STREAM, dlq.to_redis_fields())
        ids.append(entry_id)
    return ids


class TestDlqList:
    @pytest.mark.asyncio
    async def test_empty_dlq(self, r, capsys):
        """AC-06-01: dlq list empty → stdout '(empty)', exit 0."""
        args = argparse.Namespace()
        result = await cmd_dlq_list(r, args)
        assert result == 0
        assert "(empty)" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_three_entries(self, r, capsys):
        """AC-06-02: 3 DLQ entries → 3 records printed."""
        await _add_dlq_entries(r, 3)
        args = argparse.Namespace()
        result = await cmd_dlq_list(r, args)
        assert result == 0
        lines = [x for x in capsys.readouterr().out.strip().split("\n") if x]
        assert len(lines) == 3
        for line in lines:
            record = json.loads(line)
            assert "failure_code" in record
            assert "attempts" in record
            assert "enqueued_at" in record


class TestDlqReplay:
    @pytest.mark.asyncio
    async def test_replay_all(self, r, cfg):
        """AC-06-03: dlq replay --all → all XADDed; DLQ empty."""
        await _add_dlq_entries(r, 2)
        args = argparse.Namespace(all=True, id=None)
        result = await cmd_dlq_replay(r, cfg, args)
        assert result == 0
        assert await r.xlen(_DLQ_STREAM) == 0
        assert await r.xlen("stream:match_id") == 2

    @pytest.mark.asyncio
    async def test_replay_single(self, r, cfg):
        """AC-06-04: dlq replay <id> → only that entry replayed."""
        ids = await _add_dlq_entries(r, 2)
        args = argparse.Namespace(all=False, id=ids[0])
        result = await cmd_dlq_replay(r, cfg, args)
        assert result == 0
        assert await r.xlen(_DLQ_STREAM) == 1
        assert await r.xlen("stream:match_id") == 1


class TestDlqReplayValidation:
    @pytest.mark.asyncio
    async def test_replay_no_id_no_all__returns_error(self, r, cfg, capsys):
        """Q4: dlq replay with neither ID nor --all prints error and returns 1."""
        await _add_dlq_entries(r, 2)
        args = argparse.Namespace(all=False, id=None)
        result = await cmd_dlq_replay(r, cfg, args)
        assert result == 1
        captured = capsys.readouterr()
        assert "specify a message ID or --all" in captured.err
        # DLQ should be unchanged
        assert await r.xlen(_DLQ_STREAM) == 2


class TestDlqClear:
    @pytest.mark.asyncio
    async def test_clear_all(self, r):
        """AC-06-05: dlq clear --all → DLQ empty."""
        await _add_dlq_entries(r, 3)
        args = argparse.Namespace(all=True)
        result = await cmd_dlq_clear(r, args)
        assert result == 0
        assert await r.xlen(_DLQ_STREAM) == 0


class TestReplayParse:
    @pytest.mark.asyncio
    async def test_replay_parse_all(self, r, cfg):
        """AC-06-06: replay-parse --all with 5 entries → stream:parse += 5."""
        for i in range(5):
            await r.sadd("match:status:parsed", f"NA1_{i}")
        args = argparse.Namespace(all=True)
        result = await cmd_replay_parse(r, cfg, args)
        assert result == 0
        assert await r.xlen("stream:parse") == 5


class TestReplayFetch:
    @pytest.mark.asyncio
    async def test_replay_fetch(self, r, cfg):
        """AC-06-07: replay-fetch <match_id> → stream:match_id += 1."""
        args = argparse.Namespace(match_id="NA1_999")
        result = await cmd_replay_fetch(r, cfg, args)
        assert result == 0
        assert await r.xlen("stream:match_id") == 1


class TestReseed:
    @pytest.mark.asyncio
    async def test_reseed(self, r, cfg):
        """AC-06-08: reseed → clears cooldown; publishes to stream:puuid."""
        puuid = "test-puuid-0001"
        await r.hset(
            f"player:{puuid}", mapping={"seeded_at": "2024-01-01", "last_crawled_at": "2024-01-01"}
        )

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/KR1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Faker", "tagLine": "KR1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Faker#KR1", region="na1")
            result = await cmd_reseed(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        assert await r.xlen("stream:puuid") == 1
        # Cooldown fields should be deleted
        assert await r.hget(f"player:{puuid}", "seeded_at") is None
        assert await r.hget(f"player:{puuid}", "last_crawled_at") is None


class TestSystemHalt:
    @pytest.mark.asyncio
    async def test_system_halt(self, r, capsys):
        """system-halt → system:halted set to '1'."""
        args = argparse.Namespace()
        result = await cmd_system_halt(r, args)
        assert result == 0
        assert await r.get("system:halted") == "1"
        assert "halted" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_system_halt_already_halted(self, r, capsys):
        """system-halt when already halted → still succeeds."""
        await r.set("system:halted", "1")
        args = argparse.Namespace()
        result = await cmd_system_halt(r, args)
        assert result == 0
        assert await r.get("system:halted") == "1"


class TestSystemResume:
    @pytest.mark.asyncio
    async def test_system_resume(self, r, capsys):
        """AC-06-10: system-resume → system:halted deleted."""
        await r.set("system:halted", "1")
        args = argparse.Namespace()
        result = await cmd_system_resume(r, args)
        assert result == 0
        assert await r.exists("system:halted") == 0
        assert "resumed" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_system_resume_not_set(self, r):
        """AC-06-10b: system-resume when not set → exit 0; no error."""
        args = argparse.Namespace()
        result = await cmd_system_resume(r, args)
        assert result == 0


class TestResolvePuuidCache:
    @pytest.mark.asyncio
    async def test_cached_puuid_skips_api(self, r):
        """Cached player:name: key → no API call needed."""
        await r.set("player:name:faker#kr1", "cached-puuid")
        with respx.mock:
            # No routes — any HTTP call would raise
            riot = RiotClient("RGAPI-test")
            result = await _resolve_puuid(riot, "Faker#KR1", "na1", r)
            await riot.close()
        assert result == "cached-puuid"


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_found(self, r, cfg, capsys):
        """AC-06-09: stats for existing player → prints fields."""
        puuid = "test-puuid-0001"
        await r.hset(f"player:stats:{puuid}", mapping={"total_games": "10", "win_rate": "0.6000"})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Faker/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Faker", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Faker#NA1", region="na1")
            result = await cmd_stats(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        output = capsys.readouterr().out
        assert "total_games" in output
        assert "win_rate" in output


class TestDispatch:
    @pytest.mark.asyncio
    async def test_stats_dispatches(self, r, cfg):
        """stats command dispatches to cmd_stats."""
        import argparse

        await r.hset("player:stats:p", mapping={"total_games": "5", "win_rate": "0.5000"})
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "p", "gameName": "Test", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(command="stats", riot_id="Test#NA1", region="na1")
            result = await _dispatch(r, riot, cfg, args)
            await riot.close()
        assert result == 0

    @pytest.mark.asyncio
    async def test_system_resume_dispatches(self, r, cfg):
        """system-resume command dispatches correctly."""
        import argparse

        riot = RiotClient("RGAPI-test")
        args = argparse.Namespace(command="system-resume")
        result = await _dispatch(r, riot, cfg, args)
        await riot.close()
        assert result == 0

    @pytest.mark.asyncio
    async def test_unknown_command_raises(self, r, cfg):
        """Unknown command raises AssertionError."""
        import argparse

        riot = RiotClient("RGAPI-test")
        args = argparse.Namespace(command="nonexistent")
        with pytest.raises(AssertionError, match="unreachable"):
            await _dispatch(r, riot, cfg, args)
        await riot.close()


class TestResolvePuuidApi:
    @pytest.mark.asyncio
    async def test_api_fallback(self, r):
        """When no cache, resolves via Riot API."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "resolved-puuid", "gameName": "Test", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            result = await _resolve_puuid(riot, "Test#NA1", "na1", r)
            await riot.close()
        assert result == "resolved-puuid"
        # Should be cached now
        assert await r.get("player:name:test#na1") == "resolved-puuid"


class TestMainEntryPoint:
    """Tests for main() CLI parsing and dispatch."""

    @pytest.mark.asyncio
    async def test_main__stats_command__dispatches(self, monkeypatch):
        """main(['admin', 'stats', 'Faker#KR1']) → calls _dispatch with command='stats'."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_dispatch = AsyncMock(return_value=0)
        with (
            patch("lol_admin.main._dispatch", mock_dispatch),
            patch("lol_admin.main.get_redis") as mock_redis,
            patch("lol_admin.main.RiotClient") as mock_riot,
        ):
            mock_redis.return_value = AsyncMock()
            mock_riot.return_value = AsyncMock()
            result = await main(["admin", "stats", "Faker#KR1"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "stats"
        assert args.riot_id == "Faker#KR1"

    @pytest.mark.asyncio
    async def test_main__system_resume__dispatches(self, monkeypatch):
        """main(['admin', 'system-resume']) → command='system-resume'."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_dispatch = AsyncMock(return_value=0)
        with (
            patch("lol_admin.main._dispatch", mock_dispatch),
            patch("lol_admin.main.get_redis") as mock_redis,
            patch("lol_admin.main.RiotClient") as mock_riot,
        ):
            mock_redis.return_value = AsyncMock()
            mock_riot.return_value = AsyncMock()
            result = await main(["admin", "system-resume"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "system-resume"

    @pytest.mark.asyncio
    async def test_main__dlq_list__dispatches(self, monkeypatch):
        """main(['admin', 'dlq', 'list']) → command='dlq', dlq_command='list'."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_dispatch = AsyncMock(return_value=0)
        with (
            patch("lol_admin.main._dispatch", mock_dispatch),
            patch("lol_admin.main.get_redis") as mock_redis,
            patch("lol_admin.main.RiotClient") as mock_riot,
        ):
            mock_redis.return_value = AsyncMock()
            mock_riot.return_value = AsyncMock()
            result = await main(["admin", "dlq", "list"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "dlq"
        assert args.dlq_command == "list"

    @pytest.mark.asyncio
    async def test_main__dlq_replay_all__dispatches(self, monkeypatch):
        """main(['admin', 'dlq', 'replay', '--all']) → dlq_command='replay', all=True."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_dispatch = AsyncMock(return_value=0)
        with (
            patch("lol_admin.main._dispatch", mock_dispatch),
            patch("lol_admin.main.get_redis") as mock_redis,
            patch("lol_admin.main.RiotClient") as mock_riot,
        ):
            mock_redis.return_value = AsyncMock()
            mock_riot.return_value = AsyncMock()
            result = await main(["admin", "dlq", "replay", "--all"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "dlq"
        assert args.dlq_command == "replay"
        assert args.all is True

    @pytest.mark.asyncio
    async def test_main__reseed__dispatches(self, monkeypatch):
        """main(['admin', 'reseed', 'Faker#KR1']) → command='reseed'."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        mock_dispatch = AsyncMock(return_value=0)
        with (
            patch("lol_admin.main._dispatch", mock_dispatch),
            patch("lol_admin.main.get_redis") as mock_redis,
            patch("lol_admin.main.RiotClient") as mock_riot,
        ):
            mock_redis.return_value = AsyncMock()
            mock_riot.return_value = AsyncMock()
            result = await main(["admin", "reseed", "Faker#KR1"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "reseed"
        assert args.riot_id == "Faker#KR1"


class TestRecalcPriority:
    @pytest.mark.asyncio
    async def test_recalc_priority__counts_actual_keys(self, r, capsys):
        """recalc-priority scans player:priority:* keys and updates counter."""
        # Set up 3 priority keys and a stale counter
        await r.set("player:priority:puuid-1", "high")
        await r.set("player:priority:puuid-2", "high")
        await r.set("player:priority:puuid-3", "high")
        await r.set("system:priority_count", "99")  # stale/drifted value

        args = argparse.Namespace()
        result = await cmd_recalc_priority(r, args)
        assert result == 0
        assert await r.get("system:priority_count") == "3"
        output = capsys.readouterr().out
        assert "recalculated: 3" in output

    @pytest.mark.asyncio
    async def test_recalc_priority__warns_when_not_halted(self, r, capsys):
        """I2-H13: recalc-priority prints warning to stderr when system is not halted."""
        await r.set("player:priority:puuid-1", "high")
        args = argparse.Namespace()
        result = await cmd_recalc_priority(r, args)
        assert result == 0
        captured = capsys.readouterr()
        assert "Warning" in captured.err
        assert "pipeline halted" in captured.err
        # Should still complete successfully
        assert await r.get("system:priority_count") == "1"

    @pytest.mark.asyncio
    async def test_recalc_priority__no_warning_when_halted(self, r, capsys):
        """I2-H13: recalc-priority does NOT warn when system:halted is set."""
        await r.set("system:halted", "1")
        await r.set("player:priority:puuid-1", "high")
        args = argparse.Namespace()
        result = await cmd_recalc_priority(r, args)
        assert result == 0
        captured = capsys.readouterr()
        assert "Warning" not in captured.err
        assert await r.get("system:priority_count") == "1"


class TestUserFacingErrorsUsePrint:
    """CQ-2: user-facing errors should go to stderr via print(), not JSON logger."""

    @pytest.mark.asyncio
    async def test_resolve_puuid_invalid_riot_id__prints_to_stderr(self, r, capsys):
        """Invalid Riot ID → prints error to stderr, not log.error."""
        riot = RiotClient("RGAPI-test")
        result = await _resolve_puuid(riot, "NoHash", "na1", r)
        await riot.close()
        assert result is None
        captured = capsys.readouterr()
        assert "invalid Riot ID" in captured.err
        assert "NoHash" in captured.err


class TestRegionFromMatchId:
    """Tests for _region_from_match_id helper."""

    def test_na1_prefix__returns_na1(self):
        """NA1_12345 → 'na1'."""
        assert _region_from_match_id("NA1_12345") == "na1"

    def test_euw1_prefix__returns_euw1(self):
        """EUW1_67890 → 'euw1'."""
        assert _region_from_match_id("EUW1_67890") == "euw1"

    def test_kr_prefix__returns_kr(self):
        """KR_99999 → 'kr'."""
        assert _region_from_match_id("KR_99999") == "kr"

    def test_br1_prefix__returns_br1(self):
        """BR1_11111 → 'br1'."""
        assert _region_from_match_id("BR1_11111") == "br1"

    def test_lowercase_prefix__returns_lowercase(self):
        """Already lowercase prefix works the same."""
        assert _region_from_match_id("eun1_55555") == "eun1"

    def test_unknown_prefix__defaults_to_na1(self):
        """Unknown platform prefix falls back to 'na1'."""
        assert _region_from_match_id("UNKNOWN_12345") == "na1"

    def test_no_underscore__defaults_to_na1(self):
        """Match ID with no underscore → entire string is prefix; falls back to 'na1'."""
        assert _region_from_match_id("badmatchid") == "na1"

    def test_jp1_prefix__returns_jp1(self):
        """JP1_44444 → 'jp1'."""
        assert _region_from_match_id("JP1_44444") == "jp1"

    def test_oc1_prefix__returns_oc1(self):
        """OC1_77777 → 'oc1'."""
        assert _region_from_match_id("OC1_77777") == "oc1"


class TestResolvePuuidNoRedis:
    """Tests for _resolve_puuid when r=None (no Redis available)."""

    @pytest.mark.asyncio
    async def test_no_redis__api_success__returns_puuid(self):
        """r=None, API returns account → returns puuid string."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "api-puuid", "gameName": "Test", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            result = await _resolve_puuid(riot, "Test#NA1", "na1", r=None)
            await riot.close()
        assert result == "api-puuid"

    @pytest.mark.asyncio
    async def test_no_redis__api_404__returns_none_and_prints_error(self, capsys):
        """r=None, API returns 404 → returns None, prints error to stderr."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Ghost/NA1"
            ).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            result = await _resolve_puuid(riot, "Ghost#NA1", "na1", r=None)
            await riot.close()
        assert result is None
        captured = capsys.readouterr()
        assert "player not found" in captured.err
        assert "Ghost#NA1" in captured.err

    @pytest.mark.asyncio
    async def test_no_redis__invalid_riot_id__returns_none(self, capsys):
        """r=None, invalid Riot ID (no #) → returns None, prints error."""
        riot = RiotClient("RGAPI-test")
        result = await _resolve_puuid(riot, "NoHashTag", "na1", r=None)
        await riot.close()
        assert result is None
        captured = capsys.readouterr()
        assert "invalid Riot ID" in captured.err


class TestDlqClearNoAll:
    """cmd_dlq_clear with all=False must return error."""

    @pytest.mark.asyncio
    async def test_clear_no_all__returns_error(self, r, capsys):
        """cmd_dlq_clear with all=False → prints error to stderr, returns 1."""
        await _add_dlq_entries(r, 2)
        args = argparse.Namespace(all=False)
        result = await cmd_dlq_clear(r, args)
        assert result == 1
        captured = capsys.readouterr()
        assert "--all is required" in captured.err
        # DLQ should be unchanged
        assert await r.xlen(_DLQ_STREAM) == 2


class TestDlqEntriesCorruptSkip:
    """B4: corrupt DLQ entries must be skipped, not crash the caller."""

    @pytest.mark.asyncio
    async def test_corrupt_entry_skipped__valid_entries_returned(self, r):
        """Mixed valid + corrupt entries → only valid ones returned."""
        # Add a valid entry
        dlq = _make_dlq(match_id="NA1_good")
        await r.xadd(_DLQ_STREAM, dlq.to_redis_fields())

        # Add a corrupt entry (missing required 'id' field)
        await r.xadd(_DLQ_STREAM, {"garbage": "data"})

        # Add another valid entry
        dlq2 = _make_dlq(match_id="NA1_also_good")
        await r.xadd(_DLQ_STREAM, dlq2.to_redis_fields())

        entries = await _dlq_entries(r)
        assert len(entries) == 2
        payloads = [e.payload["match_id"] for _, e in entries]
        assert "NA1_good" in payloads
        assert "NA1_also_good" in payloads

    @pytest.mark.asyncio
    async def test_all_corrupt__returns_empty(self, r):
        """All entries corrupt → returns empty list, no crash."""
        await r.xadd(_DLQ_STREAM, {"bad": "entry"})
        await r.xadd(_DLQ_STREAM, {"also": "bad"})

        entries = await _dlq_entries(r)
        assert entries == []

    @pytest.mark.asyncio
    async def test_corrupt_entry_with_bad_json_payload__skipped(self, r):
        """Entry with invalid JSON in payload field → skipped."""
        dlq = _make_dlq(match_id="NA1_valid")
        await r.xadd(_DLQ_STREAM, dlq.to_redis_fields())

        # Entry with all fields present but payload is invalid JSON
        bad_fields = dlq.to_redis_fields()
        bad_fields["payload"] = "not-valid-json{{"
        await r.xadd(_DLQ_STREAM, bad_fields)

        entries = await _dlq_entries(r)
        assert len(entries) == 1
        assert entries[0][1].payload["match_id"] == "NA1_valid"

    @pytest.mark.asyncio
    async def test_dlq_list_with_corrupt_entry__still_works(self, r, capsys):
        """cmd_dlq_list with mixed valid/corrupt → prints valid, returns 0."""
        dlq = _make_dlq(match_id="NA1_ok")
        await r.xadd(_DLQ_STREAM, dlq.to_redis_fields())
        await r.xadd(_DLQ_STREAM, {"corrupt": "true"})

        args = argparse.Namespace()
        result = await cmd_dlq_list(r, args)
        assert result == 0
        lines = [x for x in capsys.readouterr().out.strip().split("\n") if x]
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["failure_code"] == "http_429"


class TestMakeReplayEnvelopePreservesMetadata:
    """B5: _make_replay_envelope must preserve enqueued_at and dlq_attempts."""

    def test_preserves_enqueued_at(self):
        """Replay envelope uses the original enqueued_at, not current time."""
        original_time = "2024-06-15T12:00:00+00:00"
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_1", "region": "na1"},
            attempts=3,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="msg-001",
            enqueued_at=original_time,
        )
        envelope = _make_replay_envelope(dlq, max_attempts=5)
        assert envelope.enqueued_at == original_time

    def test_preserves_dlq_attempts(self):
        """Replay envelope carries forward dlq_attempts from DLQ envelope."""
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_2", "region": "na1"},
            attempts=3,
            max_attempts=5,
            failure_code="http_5xx",
            failure_reason="server error",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="msg-002",
            dlq_attempts=4,
        )
        envelope = _make_replay_envelope(dlq, max_attempts=5)
        assert envelope.dlq_attempts == 4

    def test_preserves_both_enqueued_at_and_dlq_attempts(self):
        """Both fields are preserved together in a single replay envelope."""
        original_time = "2024-01-01T00:00:00+00:00"
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_3", "region": "na1"},
            attempts=2,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:parse",
            original_message_id="msg-003",
            enqueued_at=original_time,
            dlq_attempts=7,
        )
        envelope = _make_replay_envelope(dlq, max_attempts=10)
        assert envelope.enqueued_at == original_time
        assert envelope.dlq_attempts == 7
        assert envelope.source_stream == "stream:parse"
        assert envelope.type == "parse"
        assert envelope.max_attempts == 10

    def test_preserves_priority(self):
        """I2-H6: Replay envelope preserves priority from original DLQ envelope."""
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_4", "region": "na1"},
            attempts=1,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="rate limited",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="msg-004",
            priority="high",
        )
        envelope = _make_replay_envelope(dlq, max_attempts=5)
        assert envelope.priority == "high"

    def test_preserves_normal_priority(self):
        """Replay envelope preserves normal priority (default) correctly."""
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_5", "region": "na1"},
            attempts=1,
            max_attempts=5,
            failure_code="http_5xx",
            failure_reason="server error",
            failed_by="fetcher",
            original_stream="stream:parse",
            original_message_id="msg-005",
            priority="normal",
        )
        envelope = _make_replay_envelope(dlq, max_attempts=5)
        assert envelope.priority == "normal"


class TestReseedHighPriority:
    """I2-H8: cmd_reseed must use high priority and set_priority(), matching Seed service."""

    @pytest.mark.asyncio
    async def test_reseed__uses_high_priority(self, r, cfg):
        """Reseed envelope has priority='high', not default 'normal'."""
        puuid = "test-puuid-high"
        await r.hset(
            f"player:{puuid}",
            mapping={"seeded_at": "2024-01-01", "last_crawled_at": "2024-01-01"},
        )

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Hi/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Hi", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Hi#NA1", region="na1")
            result = await cmd_reseed(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        # Read back the envelope from stream:puuid and verify priority
        entries = await r.xrange("stream:puuid", "-", "+")
        assert len(entries) == 1
        from lol_pipeline.models import MessageEnvelope

        env = MessageEnvelope.from_redis_fields(entries[0][1])
        assert env.priority == "high"

    @pytest.mark.asyncio
    async def test_reseed__sets_priority_key(self, r, cfg):
        """Reseed sets player:priority:{puuid} via set_priority()."""
        puuid = "test-puuid-prio"
        await r.hset(
            f"player:{puuid}",
            mapping={"seeded_at": "2024-01-01", "last_crawled_at": "2024-01-01"},
        )

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Prio/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Prio", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Prio#NA1", region="na1")
            result = await cmd_reseed(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        # Verify priority key was set
        prio_val = await r.get(f"player:priority:{puuid}")
        assert prio_val == "high"


class TestMainRedisConnectionError:
    """I2-H9: Redis connection errors produce friendly messages, not raw tracebacks."""

    @pytest.mark.asyncio
    async def test_main__redis_error_during_dispatch__returns_1(self, monkeypatch, capsys):
        """RedisError during command dispatch prints friendly message, returns 1."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        from redis.exceptions import ConnectionError as RedisConnectionError

        mock_dispatch = AsyncMock(side_effect=RedisConnectionError("Connection refused"))
        mock_r = AsyncMock()
        mock_riot = AsyncMock()
        with (
            patch("lol_admin.main._dispatch", mock_dispatch),
            patch("lol_admin.main.get_redis", return_value=mock_r),
            patch("lol_admin.main.RiotClient", return_value=mock_riot),
        ):
            result = await main(["admin", "system-halt"])
        assert result == 1
        captured = capsys.readouterr()
        assert "Cannot connect to Redis" in captured.err
        assert "just run" in captured.err

    @pytest.mark.asyncio
    async def test_main__redis_error_during_dispatch__closes_connections(self, monkeypatch, capsys):
        """Even on RedisError, connections are properly closed via finally block."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        from redis.exceptions import RedisError

        mock_dispatch = AsyncMock(side_effect=RedisError("Redis unavailable"))
        mock_r = AsyncMock()
        mock_riot = AsyncMock()
        with (
            patch("lol_admin.main._dispatch", mock_dispatch),
            patch("lol_admin.main.get_redis", return_value=mock_r),
            patch("lol_admin.main.RiotClient", return_value=mock_riot),
        ):
            result = await main(["admin", "system-resume"])
        assert result == 1
        mock_r.aclose.assert_awaited_once()
        mock_riot.close.assert_awaited_once()
