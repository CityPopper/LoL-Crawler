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
