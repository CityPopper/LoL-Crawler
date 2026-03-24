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

from lol_admin._helpers import _relative_age
from lol_admin.main import (
    _confirm,
    _dispatch,
    _dlq_entries,
    _format_dlq_table,
    _format_stat_value,
    _format_stats_output,
    _make_replay_envelope,
    _region_from_match_id,
    _resolve_puuid,
    cmd_clear_priority,
    cmd_delayed_flush,
    cmd_delayed_list,
    cmd_dlq_archive_clear,
    cmd_dlq_archive_list,
    cmd_dlq_clear,
    cmd_dlq_list,
    cmd_dlq_replay,
    cmd_recalc_players,
    cmd_recalc_priority,
    cmd_replay_fetch,
    cmd_replay_parse,
    cmd_reseed,
    cmd_reset_stats,
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
        """AC-06-01: dlq list empty → stdout info message, exit 0."""
        args = argparse.Namespace(json=False)
        result = await cmd_dlq_list(r, args)
        assert result == 0
        assert "[--]" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_three_entries(self, r, capsys):
        """AC-06-02: 3 DLQ entries → 3 data rows in table output."""
        await _add_dlq_entries(r, 3)
        args = argparse.Namespace(json=False)
        result = await cmd_dlq_list(r, args)
        assert result == 0
        output = capsys.readouterr().out
        # Table output: header + separator + 3 data rows
        assert "Entry ID" in output
        assert "http_429" in output

    @pytest.mark.asyncio
    async def test_three_entries_json(self, r, capsys):
        """AC-06-02b: --json flag → 3 JSON records printed."""
        await _add_dlq_entries(r, 3)
        args = argparse.Namespace(json=True)
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

    @pytest.mark.asyncio
    async def test_replay__invalid_original_stream__skipped_entry_remains(self, r, cfg, capsys):
        """Entries with invalid original_stream are skipped; DLQ entry not removed."""
        bad_dlq = _make_dlq()
        bad_dlq = DLQEnvelope(**{**bad_dlq.__dict__, "original_stream": "stream:unknown-sink"})
        await r.xadd(_DLQ_STREAM, bad_dlq.to_redis_fields())
        args = argparse.Namespace(all=True, id=None)
        result = await cmd_dlq_replay(r, cfg, args)
        assert result == 0
        captured = capsys.readouterr()
        assert "refusing to replay" in captured.err
        # Entry was NOT removed from DLQ
        assert await r.xlen(_DLQ_STREAM) == 1
        # Nothing published to the invalid stream
        assert await r.xlen("stream:unknown-sink") == 0

    @pytest.mark.asyncio
    async def test_replay__valid_and_invalid_mixed__only_valid_replayed(self, r, cfg, capsys):
        """Mixed batch: valid entry replayed, invalid entry kept in DLQ."""
        await _add_dlq_entries(r, 1)
        bad_dlq = DLQEnvelope(**{**_make_dlq().__dict__, "original_stream": "stream:unknown-sink"})
        await r.xadd(_DLQ_STREAM, bad_dlq.to_redis_fields())

        args = argparse.Namespace(all=True, id=None)
        result = await cmd_dlq_replay(r, cfg, args)
        assert result == 0
        # Good entry replayed, bad entry stays
        assert await r.xlen("stream:match_id") == 1
        assert await r.xlen(_DLQ_STREAM) == 1
        captured = capsys.readouterr()
        assert "refusing to replay" in captured.err


class TestDlqClear:
    @pytest.mark.asyncio
    async def test_clear_all(self, r):
        """AC-06-05: dlq clear --all → DLQ empty."""
        await _add_dlq_entries(r, 3)
        args = argparse.Namespace(all=True, yes=True)
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
        args = argparse.Namespace(yes=True)
        result = await cmd_system_halt(r, args)
        assert result == 0
        assert await r.get("system:halted") == "1"
        assert "halted" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_system_halt_already_halted(self, r, capsys):
        """system-halt when already halted → still succeeds."""
        await r.set("system:halted", "1")
        args = argparse.Namespace(yes=True)
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
        """AC-06-09: stats for existing player → prints formatted fields."""
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
            args = argparse.Namespace(riot_id="Faker#NA1", region="na1", json=False)
            result = await cmd_stats(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        output = capsys.readouterr().out
        assert "Total Games" in output
        assert "Win Rate" in output
        assert "60.0%" in output


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
        """recalc-priority scans player:priority:* keys and prints count (diagnostic-only)."""
        await r.set("player:priority:puuid-1", "1")
        await r.set("player:priority:puuid-2", "1")
        await r.set("player:priority:puuid-3", "1")

        args = argparse.Namespace()
        result = await cmd_recalc_priority(r, args)
        assert result == 0
        output = capsys.readouterr().out
        assert "keys found: 3" in output

    @pytest.mark.asyncio
    async def test_recalc_priority__no_keys__reports_zero(self, r, capsys):
        """recalc-priority with no priority keys reports 0."""
        args = argparse.Namespace()
        result = await cmd_recalc_priority(r, args)
        assert result == 0
        output = capsys.readouterr().out
        assert "keys found: 0" in output

    @pytest.mark.asyncio
    async def test_recalc_priority__does_not_write_counter(self, r, capsys):
        """recalc-priority is read-only — does NOT write system:priority_count."""
        await r.set("player:priority:puuid-1", "1")
        args = argparse.Namespace()
        await cmd_recalc_priority(r, args)
        # No counter key should be created
        assert await r.exists("system:priority_count") == 0


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


class TestSanitizeOutput:
    """Terminal injection: control characters in user input must be stripped before printing."""

    @pytest.mark.asyncio
    async def test_resolve_puuid__strips_ansi_escape_from_invalid_id(self, r, capsys):
        """ANSI escape sequences in riot_id are stripped from stderr output."""
        malicious = "Evil\x1b[31mRed\x1b[0mText"
        riot = RiotClient("RGAPI-test")
        result = await _resolve_puuid(riot, malicious, "na1", r)
        await riot.close()
        assert result is None
        captured = capsys.readouterr()
        # The ESC control byte (\x1b) should be stripped; printable remnants are safe
        assert "\x1b" not in captured.err
        assert "Evil[31mRed[0mText" in captured.err

    @pytest.mark.asyncio
    async def test_resolve_puuid__strips_control_chars_from_not_found(self, capsys):
        """Control characters in riot_id stripped from 'player not found' message."""
        malicious = "Bad\x07Name#\x1b[2JTag"
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1"
                "/accounts/by-riot-id/Bad%07Name/%1B%5B2JTag"
            ).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            result = await _resolve_puuid(riot, malicious, "na1", r=None)
            await riot.close()
        assert result is None
        captured = capsys.readouterr()
        assert "\x07" not in captured.err
        assert "\x1b" not in captured.err
        assert "BadName#[2JTag" in captured.err

    def test_sanitize_strips_all_c0_and_c1_control_chars(self):
        """_sanitize removes C0 (0x00-0x1F) and C1 (0x7F-0x9F) control characters."""
        from lol_admin.main import _sanitize

        # C0 controls
        assert _sanitize("hello\x00world") == "helloworld"
        assert _sanitize("\x1b[31mred\x1b[0m") == "[31mred[0m"
        assert _sanitize("bell\x07here") == "bellhere"
        assert _sanitize("\ttab\nnewline\rreturn") == "tabnewlinereturn"
        # C1 controls
        assert _sanitize("hi\x7fthere") == "hithere"
        assert _sanitize("test\x9fend") == "testend"
        # Clean string passes through unchanged
        assert _sanitize("Player#NA1") == "Player#NA1"


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

    def test_empty_string__defaults_to_na1(self):
        """Empty string → split returns [''], lowered to '', falls back to 'na1'."""
        assert _region_from_match_id("") == "na1"

    def test_just_underscore__defaults_to_na1(self):
        """Single underscore → prefix is '', not a known platform, falls back to 'na1'."""
        assert _region_from_match_id("_12345") == "na1"

    def test_multiple_underscores__uses_first_segment(self):
        """NA1_123_456 → prefix is 'na1', which is valid."""
        assert _region_from_match_id("NA1_123_456") == "na1"


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


class TestResolvePuuidWithRedisErrors:
    """_resolve_puuid error paths when Redis is available but player is not found."""

    @pytest.mark.asyncio
    async def test_resolve_puuid__redis_available__player_not_found__returns_none(self, r):
        """resolve_puuid via Redis path: player not in cache, API 404 → returns None."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Ghost/NA1"
            ).mock(return_value=httpx.Response(404))
            riot = RiotClient("RGAPI-test")
            result = await _resolve_puuid(riot, "Ghost#NA1", "na1", r)
            await riot.close()
        assert result is None
        # Cache should not be populated for a 404
        assert await r.get("player:name:ghost#na1") is None

    @pytest.mark.asyncio
    async def test_resolve_puuid__redis_available__api_success__returns_puuid(self, r):
        """resolve_puuid via Redis path: not cached, API success → returns puuid, caches it."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Found/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "found-puuid", "gameName": "Found", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            result = await _resolve_puuid(riot, "Found#NA1", "na1", r)
            await riot.close()
        assert result == "found-puuid"
        # Should be cached
        assert await r.get("player:name:found#na1") == "found-puuid"


class TestDlqClearNoAll:
    """cmd_dlq_clear with all=False must return error."""

    @pytest.mark.asyncio
    async def test_clear_no_all__returns_error(self, r, capsys):
        """cmd_dlq_clear with all=False → prints error to stderr, returns 1."""
        await _add_dlq_entries(r, 2)
        args = argparse.Namespace(all=False, yes=True)
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
        """cmd_dlq_list with mixed valid/corrupt → prints valid in table, returns 0."""
        dlq = _make_dlq(match_id="NA1_ok")
        await r.xadd(_DLQ_STREAM, dlq.to_redis_fields())
        await r.xadd(_DLQ_STREAM, {"corrupt": "true"})

        args = argparse.Namespace(json=False)
        result = await cmd_dlq_list(r, args)
        assert result == 0
        output = capsys.readouterr().out
        assert "http_429" in output
        assert "Entry ID" in output


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


class TestReseedManualPriority:
    """I2-H8: cmd_reseed must use manual_20 priority and set_priority(), matching Seed service."""

    @pytest.mark.asyncio
    async def test_reseed__uses_manual_20_priority(self, r, cfg):
        """Reseed envelope has priority='manual_20'."""
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
        assert env.priority == "manual_20"

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
        assert prio_val == "1"


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
        assert "just up" in captured.err

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


class TestStatsJson:
    """--json flag outputs a JSON object instead of human-readable text."""

    @pytest.mark.asyncio
    async def test_stats_json__outputs_valid_json(self, r, cfg, capsys):
        """--json: stats command prints a single JSON object to stdout."""
        puuid = "test-puuid-json"
        await r.hset(
            f"player:stats:{puuid}",
            mapping={"total_games": "20", "win_rate": "0.7000", "kda": "3.50"},
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
            args = argparse.Namespace(riot_id="Faker#KR1", region="na1", json=True)
            result = await cmd_stats(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        output = capsys.readouterr().out
        record = json.loads(output)
        assert record["game_name"] == "Faker"
        assert record["tag_line"] == "KR1"
        assert record["region"] == "na1"
        assert record["total_games"] == "20"
        assert record["win_rate"] == "0.7000"
        assert record["kda"] == "3.50"

    @pytest.mark.asyncio
    async def test_stats_json__no_human_text(self, r, cfg, capsys):
        """--json: stdout contains no 'Stats for' header line."""
        puuid = "test-puuid-json2"
        await r.hset(
            f"player:stats:{puuid}",
            mapping={"total_games": "5", "win_rate": "0.4000"},
        )

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Test/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Test", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Test#NA1", region="na1", json=True)
            result = await cmd_stats(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        output = capsys.readouterr().out
        assert "Stats for" not in output
        # Confirm it is valid JSON with expected keys
        record = json.loads(output)
        assert set(record.keys()) == {
            "game_name",
            "tag_line",
            "region",
            "win_rate",
            "kda",
            "total_games",
        }

    @pytest.mark.asyncio
    async def test_stats_no_json_flag__outputs_human_text(self, r, cfg, capsys):
        """Without --json, stats prints human-readable 'Player: ...' header."""
        puuid = "test-puuid-human"
        await r.hset(
            f"player:stats:{puuid}",
            mapping={"total_games": "8", "win_rate": "0.5000"},
        )

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Human/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Human", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Human#NA1", region="na1", json=False)
            result = await cmd_stats(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        output = capsys.readouterr().out
        assert "Player: Human#NA1" in output
        assert "Total Games" in output

    def test_build_parser__json_flag_defaults_false(self):
        """_build_parser produces --json flag that defaults to False."""
        from lol_admin.main import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["stats", "Faker#KR1"])
        assert args.json is False

    def test_build_parser__json_flag_can_be_set(self):
        """_build_parser --json flag sets json=True when provided."""
        from lol_admin.main import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--json", "stats", "Faker#KR1"])
        assert args.json is True

    @pytest.mark.asyncio
    async def test_stats_json__missing_fields_are_none(self, r, cfg, capsys):
        """--json: fields absent from Redis appear as null in JSON output."""
        puuid = "test-puuid-partial"
        # Only set total_games; kda and win_rate absent
        await r.hset(f"player:stats:{puuid}", mapping={"total_games": "3"})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Part/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Part", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Part#NA1", region="na1", json=True)
            result = await cmd_stats(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        record = json.loads(capsys.readouterr().out)
        assert record["total_games"] == "3"
        assert record["kda"] is None
        assert record["win_rate"] is None

    @pytest.mark.asyncio
    async def test_main_json_flag__passed_through_to_args(self, monkeypatch):
        """main(['admin', '--json', 'stats', 'Faker#KR1']) sets args.json=True."""
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
            result = await main(["admin", "--json", "stats", "Faker#KR1"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.json is True
        assert args.command == "stats"
        assert args.riot_id == "Faker#KR1"


class TestRecalcPlayers:
    """recalc-players rebuilds players:all from existing player:{puuid} hashes."""

    @pytest.mark.asyncio
    async def test_recalc_players__indexes_existing_players(self, r, capsys):
        """Players with seeded_at get indexed in players:all."""
        await r.hset(
            "player:puuid-one",
            mapping={
                "game_name": "PlayerOne",
                "tag_line": "001",
                "region": "na1",
                "seeded_at": "2026-03-19T12:00:00+00:00",
            },
        )
        await r.hset(
            "player:puuid-two",
            mapping={
                "game_name": "PlayerTwo",
                "tag_line": "002",
                "region": "euw1",
                "seeded_at": "2026-03-18T06:00:00+00:00",
            },
        )
        args = argparse.Namespace()
        result = await cmd_recalc_players(r, args)
        assert result == 0
        assert await r.zcard("players:all") == 2
        assert await r.zscore("players:all", "puuid-one") is not None
        assert await r.zscore("players:all", "puuid-two") is not None
        output = capsys.readouterr().out
        assert "2 players indexed" in output

    @pytest.mark.asyncio
    async def test_recalc_players__skips_player_without_seeded_at(self, r, capsys):
        """Players missing seeded_at are not indexed."""
        await r.hset(
            "player:puuid-no-seed",
            mapping={"game_name": "NoSeed", "tag_line": "001", "region": "na1"},
        )
        args = argparse.Namespace()
        result = await cmd_recalc_players(r, args)
        assert result == 0
        assert await r.zcard("players:all") == 0
        assert "0 players indexed" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_recalc_players__skips_non_player_keys(self, r, capsys):
        """player:stats:*, player:matches:* etc. are ignored."""
        await r.hset("player:stats:puuid-x", mapping={"total_games": "10"})
        await r.hset("player:matches:puuid-x", mapping={"NA1_1": "123"})
        await r.hset(
            "player:puuid-x",
            mapping={
                "game_name": "RealPlayer",
                "tag_line": "001",
                "seeded_at": "2026-01-01T00:00:00+00:00",
            },
        )
        args = argparse.Namespace()
        result = await cmd_recalc_players(r, args)
        assert result == 0
        assert await r.zcard("players:all") == 1
        assert "1 players indexed" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_recalc_players__dispatches_via_cli(self, monkeypatch):
        """recalc-players command dispatches correctly via main()."""
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
            result = await main(["admin", "recalc-players"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "recalc-players"


class TestFormatStatsOutput:
    """P10-GD-1: _format_stats_output produces formatted, human-readable stats."""

    def test_format_stats_output__win_rate_formatted_as_percent(self):
        """win_rate 0.5374 → '53.7%' in output."""
        stats = {
            "win_rate": "0.5374",
            "kda": "3.42",
            "total_games": "100",
            "kills": "850",
        }
        output = _format_stats_output(stats, "Faker", "KR1", "abcdef1234567890")
        assert "53.7%" in output
        assert "(100 games)" in output

    def test_format_stats_output__priority_order(self):
        """win_rate appears before kda, which appears before total_games."""
        stats = {
            "total_games": "50",
            "kda": "2.50",
            "win_rate": "0.6000",
            "assists": "300",
            "deaths": "200",
            "kills": "400",
        }
        output = _format_stats_output(stats, "Test", "NA1", "aabb0011223344")
        lines = output.split("\n")
        # Find line indices for priority keys
        win_rate_idx = next(i for i, ln in enumerate(lines) if "Win Rate" in ln)
        kda_idx = next(i for i, ln in enumerate(lines) if "KDA" in ln)
        total_games_idx = next(i for i, ln in enumerate(lines) if "Total Games" in ln)
        assert win_rate_idx < kda_idx < total_games_idx

    def test_format_stats_output__header_includes_player_info(self):
        """Header line includes game_name#tag_line and truncated puuid."""
        stats = {"win_rate": "0.5000", "total_games": "10"}
        output = _format_stats_output(stats, "MyName", "TAG1", "abcdef1234567890")
        assert "MyName#TAG1" in output
        assert "abcdef12" in output

    def test_format_stats_output__kda_formatted_as_float(self):
        """kda 3.4 → '3.40' (2 decimal places)."""
        stats = {"kda": "3.4", "total_games": "5", "win_rate": "0.5"}
        output = _format_stats_output(stats, "P", "T", "aabbccdd")
        assert "3.40" in output

    def test_format_stats_output__total_games_as_integer(self):
        """total_games shown as integer without decimals."""
        stats = {"total_games": "42", "win_rate": "0.5"}
        output = _format_stats_output(stats, "P", "T", "aabbccdd")
        # "42" should appear as the value, not "42.0"
        assert "42" in output

    def test_format_stats_output__rule_lines_present(self):
        """Output contains horizontal rule lines (unicode box-drawing)."""
        stats = {"win_rate": "0.5", "total_games": "10"}
        output = _format_stats_output(stats, "P", "T", "aabbccdd")
        assert "\u2500" in output  # ─ character


class TestFormatDlqTable:
    """P10-GD-2: _format_dlq_table produces human-readable table output."""

    def test_format_dlq_table__plain_text_output(self):
        """Table has headers and at least one data row."""
        dlq = _make_dlq(failure_code="http_429", match_id="NA1_123")
        entries = [("1720000000000-0", dlq)]
        output = _format_dlq_table(entries)
        # Header row
        assert "Entry ID" in output
        assert "Stream" in output
        assert "Code" in output
        assert "Attempts" in output
        assert "Age" in output
        # Data row
        assert "1720000000000-0" in output
        assert "http_429" in output

    def test_format_dlq_table__stream_truncated(self):
        """Long stream names are truncated to 15 chars."""
        dlq = _make_dlq()
        entries = [("1-0", dlq)]
        output = _format_dlq_table(entries)
        # "stream:match_id" is 15 chars, should fit exactly
        assert "stream:match_id" in output

    def test_format_dlq_table__dlq_attempts_shown(self):
        """Attempts column shows dlq_attempts with 'dlq' suffix."""
        dlq = DLQEnvelope(
            source_stream="stream:dlq",
            type="dlq",
            payload={"match_id": "NA1_1", "region": "na1"},
            attempts=3,
            max_attempts=5,
            failure_code="http_429",
            failure_reason="test",
            failed_by="fetcher",
            original_stream="stream:match_id",
            original_message_id="1234-0",
            dlq_attempts=5,
        )
        entries = [("1-0", dlq)]
        output = _format_dlq_table(entries)
        assert "5 dlq" in output


class TestCmdStatsPlayerNotFound:
    """P10-GD-3: error messages use [ERROR] prefix."""

    @pytest.mark.asyncio
    async def test_cmd_stats_player_not_found__error_prefix(self, r, cfg, capsys):
        """stats for non-existent player → stderr starts with [ERROR]."""
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Ghost/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": "ghost-puuid", "gameName": "Ghost", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Ghost#NA1", region="na1", json=False)
            result = await cmd_stats(r, riot, cfg, args)
            await riot.close()
        assert result == 1
        captured = capsys.readouterr()
        assert captured.err.strip().startswith("[ERROR]")


class TestErrorPrefixes:
    """P10-GD-3: standardized error/warning/success prefixes."""

    @pytest.mark.asyncio
    async def test_system_halt__ok_prefix(self, r, capsys):
        """system-halt success message uses [OK] prefix."""
        args = argparse.Namespace(yes=True)
        await cmd_system_halt(r, args)
        output = capsys.readouterr().out
        assert output.strip().startswith("[OK]")

    @pytest.mark.asyncio
    async def test_system_resume__ok_prefix(self, r, capsys):
        """system-resume success message uses [OK] prefix."""
        await r.set("system:halted", "1")
        args = argparse.Namespace()
        await cmd_system_resume(r, args)
        output = capsys.readouterr().out
        assert output.strip().startswith("[OK]")

    @pytest.mark.asyncio
    async def test_dlq_replay__ok_prefix(self, r, cfg, capsys):
        """dlq replay success message uses [OK] prefix."""
        await _add_dlq_entries(r, 1)
        entries = await r.xrange("stream:dlq", "-", "+")
        entry_id = entries[0][0]
        args = argparse.Namespace(all=False, id=entry_id)
        await cmd_dlq_replay(r, cfg, args)
        output = capsys.readouterr().out
        assert "[OK]" in output

    @pytest.mark.asyncio
    async def test_dlq_clear__ok_prefix(self, r, capsys):
        """dlq clear success message uses [OK] prefix."""
        await _add_dlq_entries(r, 2)
        args = argparse.Namespace(all=True, yes=True)
        await cmd_dlq_clear(r, args)
        output = capsys.readouterr().out
        assert output.strip().startswith("[OK]")

    @pytest.mark.asyncio
    async def test_resolve_puuid_invalid__error_prefix(self, r, capsys):
        """Invalid Riot ID error uses [ERROR] prefix."""
        riot = RiotClient("RGAPI-test")
        await _resolve_puuid(riot, "NoHash", "na1", r)
        await riot.close()
        captured = capsys.readouterr()
        assert captured.err.strip().startswith("[ERROR]")

    @pytest.mark.asyncio
    async def test_reseed__ok_prefix(self, r, cfg, capsys):
        """reseed success message uses [OK] prefix."""
        puuid = "test-puuid-prefix"
        await r.hset(
            f"player:{puuid}",
            mapping={"seeded_at": "2024-01-01", "last_crawled_at": "2024-01-01"},
        )
        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/PFX/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "PFX", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="PFX#NA1", region="na1")
            await cmd_reseed(r, riot, cfg, args)
            await riot.close()
        output = capsys.readouterr().out
        assert "[OK]" in output

    @pytest.mark.asyncio
    async def test_main__redis_error__error_prefix(self, monkeypatch, capsys):
        """Redis connection error uses [ERROR] prefix."""
        monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
        monkeypatch.setenv("REDIS_URL", "redis://localhost")
        from redis.exceptions import ConnectionError as RedisConnectionError

        mock_dispatch = AsyncMock(side_effect=RedisConnectionError("refused"))
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
        assert captured.err.strip().startswith("[ERROR]")


class TestFormatStatValueNanInf:
    """P11-TST-3: _format_stat_value guards against NaN/Inf inputs."""

    def test_win_rate_nan__returns_raw_value(self):
        """win_rate='nan' should return 'nan', not 'nan%'."""
        result = _format_stat_value("win_rate", "nan", {})
        assert result == "nan"

    def test_win_rate_inf__returns_raw_value(self):
        """win_rate='inf' should return 'inf', not 'inf%'."""
        result = _format_stat_value("win_rate", "inf", {})
        assert result == "inf"

    def test_win_rate_neg_inf__returns_raw_value(self):
        """win_rate='-inf' should return '-inf'."""
        result = _format_stat_value("win_rate", "-inf", {})
        assert result == "-inf"

    def test_kda_nan__returns_raw_value(self):
        """kda='nan' should return 'nan', not formatted float."""
        result = _format_stat_value("kda", "nan", {})
        assert result == "nan"

    def test_kda_inf__returns_raw_value(self):
        """kda='inf' should return 'inf', not formatted float."""
        result = _format_stat_value("kda", "inf", {})
        assert result == "inf"

    def test_total_games_nan__falls_through_to_except(self):
        """total_games='nan' hits int(float('nan')) -> ValueError -> returns raw."""
        result = _format_stat_value("total_games", "nan", {})
        assert result == "nan"

    def test_normal_values_still_format(self):
        """Normal values are not affected by the guard."""
        assert _format_stat_value("win_rate", "0.5", {"total_games": "10"}) == "50.0%  (10 games)"
        assert _format_stat_value("kda", "3.4", {}) == "3.40"
        assert _format_stat_value("total_games", "42", {}) == "42"


# ---------------------------------------------------------------------------
# P15-OPS-3: Confirmation prompts
# ---------------------------------------------------------------------------


class TestConfirmHelper:
    """_confirm helper respects --yes flag and user input."""

    def test_confirm__yes_flag__returns_true(self):
        """--yes flag bypasses prompt entirely."""
        args = argparse.Namespace(yes=True)
        assert _confirm("Are you sure?", args) is True

    def test_confirm__user_types_y__returns_true(self, monkeypatch):
        """User typing 'y' returns True."""
        monkeypatch.setattr("builtins.input", lambda _: "y")
        args = argparse.Namespace(yes=False)
        assert _confirm("Are you sure?", args) is True

    def test_confirm__user_types_yes__returns_true(self, monkeypatch):
        """User typing 'yes' returns True."""
        monkeypatch.setattr("builtins.input", lambda _: "yes")
        args = argparse.Namespace(yes=False)
        assert _confirm("Are you sure?", args) is True

    def test_confirm__user_types_yes_upper__returns_true(self, monkeypatch):
        """User typing 'YES' (uppercase) returns True."""
        monkeypatch.setattr("builtins.input", lambda _: "YES")
        args = argparse.Namespace(yes=False)
        assert _confirm("Are you sure?", args) is True

    def test_confirm__user_types_n__returns_false(self, monkeypatch):
        """User typing 'n' returns False."""
        monkeypatch.setattr("builtins.input", lambda _: "n")
        args = argparse.Namespace(yes=False)
        assert _confirm("Are you sure?", args) is False

    def test_confirm__user_types_empty__returns_false(self, monkeypatch):
        """User pressing Enter (empty string) returns False."""
        monkeypatch.setattr("builtins.input", lambda _: "")
        args = argparse.Namespace(yes=False)
        assert _confirm("Are you sure?", args) is False

    def test_confirm__user_types_no__returns_false(self, monkeypatch):
        """User typing 'no' returns False."""
        monkeypatch.setattr("builtins.input", lambda _: "no")
        args = argparse.Namespace(yes=False)
        assert _confirm("Are you sure?", args) is False


class TestSystemHaltConfirmation:
    """P15-OPS-3: system-halt requires confirmation."""

    @pytest.mark.asyncio
    async def test_system_halt__no_confirm__aborts(self, r, monkeypatch, capsys):
        """system-halt without confirmation returns 1 and does not set system:halted."""
        monkeypatch.setattr("builtins.input", lambda _: "n")
        args = argparse.Namespace(yes=False)
        result = await cmd_system_halt(r, args)
        assert result == 1
        assert await r.exists("system:halted") == 0
        assert "aborted" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_system_halt__confirm_y__proceeds(self, r, monkeypatch, capsys):
        """system-halt with 'y' confirmation proceeds."""
        monkeypatch.setattr("builtins.input", lambda _: "y")
        args = argparse.Namespace(yes=False)
        result = await cmd_system_halt(r, args)
        assert result == 0
        assert await r.get("system:halted") == "1"


class TestDlqClearConfirmation:
    """P15-OPS-3: dlq clear --all requires confirmation."""

    @pytest.mark.asyncio
    async def test_dlq_clear__no_confirm__aborts(self, r, monkeypatch, capsys):
        """dlq clear --all without confirmation returns 1."""
        await _add_dlq_entries(r, 2)
        monkeypatch.setattr("builtins.input", lambda _: "n")
        args = argparse.Namespace(all=True, yes=False)
        result = await cmd_dlq_clear(r, args)
        assert result == 1
        assert await r.xlen(_DLQ_STREAM) == 2
        assert "aborted" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_dlq_clear__confirm_yes__proceeds(self, r, monkeypatch, capsys):
        """dlq clear --all with 'yes' confirmation proceeds."""
        await _add_dlq_entries(r, 2)
        monkeypatch.setattr("builtins.input", lambda _: "yes")
        args = argparse.Namespace(all=True, yes=False)
        result = await cmd_dlq_clear(r, args)
        assert result == 0
        assert await r.xlen(_DLQ_STREAM) == 0


class TestBuildParserYesFlag:
    """--yes / -y flag is available on the top-level parser."""

    def test_build_parser__yes_flag_defaults_false(self):
        """_build_parser produces --yes flag that defaults to False."""
        from lol_admin.main import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["system-halt"])
        assert args.yes is False

    def test_build_parser__yes_flag_can_be_set(self):
        """_build_parser --yes flag sets yes=True when provided."""
        from lol_admin.main import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["--yes", "system-halt"])
        assert args.yes is True

    def test_build_parser__y_short_flag(self):
        """-y is a shorthand for --yes."""
        from lol_admin.main import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["-y", "system-halt"])
        assert args.yes is True


# ---------------------------------------------------------------------------
# P15-OPS-1: reset-stats
# ---------------------------------------------------------------------------


class TestResetStats:
    """P15-OPS-1: admin reset-stats wipes stats and re-triggers analysis."""

    @pytest.mark.asyncio
    async def test_reset_stats__deletes_keys_and_enqueues(self, r, cfg, capsys):
        """reset-stats deletes 4 keys and enqueues stream:analyze message."""
        puuid = "test-puuid-reset"
        await r.hset(f"player:stats:{puuid}", mapping={"total_games": "10"})
        await r.set(f"player:stats:cursor:{puuid}", "12345")
        await r.zadd(f"player:champions:{puuid}", {"Ahri": 5})
        await r.zadd(f"player:roles:{puuid}", {"MID": 5})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Reset/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Reset", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Reset#NA1", region="na1")
            result = await cmd_reset_stats(r, riot, cfg, args)
            await riot.close()

        assert result == 0
        # All stats keys deleted
        assert await r.exists(f"player:stats:{puuid}") == 0
        assert await r.exists(f"player:stats:cursor:{puuid}") == 0
        assert await r.exists(f"player:champions:{puuid}") == 0
        assert await r.exists(f"player:roles:{puuid}") == 0
        # Analysis re-triggered
        assert await r.xlen("stream:analyze") == 1
        output = capsys.readouterr().out
        assert "[OK]" in output
        assert "deleted" in output

    @pytest.mark.asyncio
    async def test_reset_stats__invalid_riot_id__returns_1(self, r, cfg, capsys):
        """reset-stats with invalid Riot ID returns 1."""
        riot = RiotClient("RGAPI-test")
        args = argparse.Namespace(riot_id="NoHash", region="na1")
        result = await cmd_reset_stats(r, riot, cfg, args)
        await riot.close()
        assert result == 1

    @pytest.mark.asyncio
    async def test_reset_stats__analyze_envelope_has_correct_payload(self, r, cfg):
        """The analyze envelope payload contains just {puuid}."""
        puuid = "test-puuid-env"
        await r.hset(f"player:stats:{puuid}", mapping={"total_games": "5"})

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/Env/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "Env", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(riot_id="Env#NA1", region="na1")
            await cmd_reset_stats(r, riot, cfg, args)
            await riot.close()

        from lol_pipeline.models import MessageEnvelope

        entries = await r.xrange("stream:analyze", "-", "+")
        assert len(entries) == 1
        env = MessageEnvelope.from_redis_fields(entries[0][1])
        assert env.payload == {"puuid": puuid}
        assert env.type == "analyze"
        assert env.source_stream == "stream:analyze"


# ---------------------------------------------------------------------------
# P15-OPS-2: DLQ archive subcommands
# ---------------------------------------------------------------------------

_DLQ_ARCHIVE_STREAM = "stream:dlq:archive"


class TestDlqArchiveList:
    """P15-OPS-2: dlq archive list shows entries from stream:dlq:archive."""

    @pytest.mark.asyncio
    async def test_archive_list__empty(self, r, capsys):
        """Empty archive → info message, returns 0."""
        args = argparse.Namespace()
        result = await cmd_dlq_archive_list(r, args)
        assert result == 0
        assert "empty" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_archive_list__with_entries(self, r, capsys):
        """Archive with entries → prints each one, returns 0."""
        dlq = _make_dlq(match_id="NA1_arch1")
        await r.xadd(_DLQ_ARCHIVE_STREAM, dlq.to_redis_fields())
        await r.xadd(_DLQ_ARCHIVE_STREAM, dlq.to_redis_fields())
        args = argparse.Namespace()
        result = await cmd_dlq_archive_list(r, args)
        assert result == 0
        output = capsys.readouterr().out
        assert "2 archive entries" in output

    @pytest.mark.asyncio
    async def test_archive_list__corrupt_entry__shows_fallback_fields(self, r, capsys):
        """Corrupt entry in archive → displays fallback fields from raw dict."""
        await r.xadd(_DLQ_ARCHIVE_STREAM, {"garbage": "data"})
        args = argparse.Namespace()
        result = await cmd_dlq_archive_list(r, args)
        assert result == 0
        output = capsys.readouterr().out
        # Fallback renders unknown fields as "?" placeholders
        assert "?" in output
        assert "1 archive entries" in output


class TestDlqArchiveClear:
    """P15-OPS-2: dlq archive clear --all clears the archive stream."""

    @pytest.mark.asyncio
    async def test_archive_clear__deletes_all(self, r, capsys):
        """dlq archive clear --all → archive emptied."""
        dlq = _make_dlq(match_id="NA1_arch2")
        await r.xadd(_DLQ_ARCHIVE_STREAM, dlq.to_redis_fields())
        await r.xadd(_DLQ_ARCHIVE_STREAM, dlq.to_redis_fields())
        args = argparse.Namespace(all=True, yes=True)
        result = await cmd_dlq_archive_clear(r, args)
        assert result == 0
        assert await r.xlen(_DLQ_ARCHIVE_STREAM) == 0
        output = capsys.readouterr().out
        assert "cleared 2" in output

    @pytest.mark.asyncio
    async def test_archive_clear__empty__info_message(self, r, capsys):
        """dlq archive clear --all on empty archive → info message."""
        args = argparse.Namespace(all=True, yes=True)
        result = await cmd_dlq_archive_clear(r, args)
        assert result == 0
        assert "empty" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_archive_clear__no_all__returns_error(self, r, capsys):
        """dlq archive clear without --all → error."""
        args = argparse.Namespace(all=False, yes=True)
        result = await cmd_dlq_archive_clear(r, args)
        assert result == 1
        assert "--all is required" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_archive_clear__no_confirm__aborts(self, r, monkeypatch, capsys):
        """dlq archive clear --all without confirmation aborts."""
        await r.xadd(_DLQ_ARCHIVE_STREAM, {"dummy": "1"})
        monkeypatch.setattr("builtins.input", lambda _: "n")
        args = argparse.Namespace(all=True, yes=False)
        result = await cmd_dlq_archive_clear(r, args)
        assert result == 1
        assert "aborted" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# OPS-16-01: clear-priority
# ---------------------------------------------------------------------------


class TestClearPriority:
    """OPS-16-01: admin clear-priority deletes priority keys."""

    @pytest.mark.asyncio
    async def test_clear_priority__all__deletes_all_keys(self, r, capsys):
        """clear-priority --all deletes all player:priority:* keys and priority:active SET."""
        await r.set("player:priority:p1", "1")
        await r.set("player:priority:p2", "1")
        await r.set("player:priority:p3", "1")
        await r.sadd("priority:active", "p1", "p2", "p3")
        riot = RiotClient("RGAPI-test")
        args = argparse.Namespace(all=True, riot_id=None, region="na1")
        result = await cmd_clear_priority(r, riot, args)
        await riot.close()
        assert result == 0
        assert await r.exists("player:priority:p1") == 0
        assert await r.exists("player:priority:p2") == 0
        assert await r.exists("player:priority:p3") == 0
        assert await r.exists("priority:active") == 0
        output = capsys.readouterr().out
        assert "deleted 3" in output

    @pytest.mark.asyncio
    async def test_clear_priority__all__no_keys__zero(self, r, capsys):
        """clear-priority --all with no keys reports 0."""
        riot = RiotClient("RGAPI-test")
        args = argparse.Namespace(all=True, riot_id=None, region="na1")
        result = await cmd_clear_priority(r, riot, args)
        await riot.close()
        assert result == 0
        assert "deleted 0" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_clear_priority__single_player__deletes_key(self, r, capsys):
        """clear-priority <riot_id> deletes only that player's key and removes from SET."""
        puuid = "test-puuid-prio-clear"
        await r.set(f"player:priority:{puuid}", "1")
        await r.set("player:priority:other", "1")
        await r.sadd("priority:active", puuid, "other")

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
            args = argparse.Namespace(all=False, riot_id="Prio#NA1", region="na1")
            result = await cmd_clear_priority(r, riot, args)
            await riot.close()

        assert result == 0
        assert await r.exists(f"player:priority:{puuid}") == 0
        assert await r.exists("player:priority:other") == 1
        assert not await r.sismember("priority:active", puuid)
        assert await r.sismember("priority:active", "other")
        assert "[OK]" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_clear_priority__single_player__no_key__info(self, r, capsys):
        """clear-priority <riot_id> when no priority key exists → info message."""
        puuid = "test-puuid-noprio"

        with respx.mock:
            respx.get(
                "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/NoKey/NA1"
            ).mock(
                return_value=httpx.Response(
                    200,
                    json={"puuid": puuid, "gameName": "NoKey", "tagLine": "NA1"},
                )
            )
            riot = RiotClient("RGAPI-test")
            args = argparse.Namespace(all=False, riot_id="NoKey#NA1", region="na1")
            result = await cmd_clear_priority(r, riot, args)
            await riot.close()

        assert result == 0
        assert "no priority key" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_clear_priority__no_args__returns_error(self, r, capsys):
        """clear-priority with neither riot_id nor --all → error."""
        riot = RiotClient("RGAPI-test")
        args = argparse.Namespace(all=False, riot_id=None, region="na1")
        result = await cmd_clear_priority(r, riot, args)
        await riot.close()
        assert result == 1
        assert "specify a Riot ID or --all" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# OPS-16-07: delayed-list and delayed-flush
# ---------------------------------------------------------------------------


class TestDelayedList:
    """OPS-16-07: admin delayed-list shows delayed:messages entries."""

    @pytest.mark.asyncio
    async def test_delayed_list__empty(self, r, capsys):
        """Empty delayed:messages → info message."""
        args = argparse.Namespace()
        result = await cmd_delayed_list(r, args)
        assert result == 0
        assert "empty" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_delayed_list__with_entries(self, r, capsys):
        """delayed-list with entries → shows them with OK summary."""
        now_ms = 1700000000000.0
        await r.zadd("delayed:messages", {"member1": now_ms, "member2": now_ms + 60000})
        args = argparse.Namespace()
        result = await cmd_delayed_list(r, args)
        assert result == 0
        output = capsys.readouterr().out
        assert "showing 2 of 2" in output
        assert "member1" in output
        assert "member2" in output


class TestDelayedListJson:
    """E5: delayed-list --json outputs one JSON object per entry."""

    @pytest.mark.asyncio
    async def test_delayed_list__json_output(self, r, capsys):
        """delayed-list with --json outputs valid JSON objects."""
        now_ms = 1700000000000.0
        await r.zadd("delayed:messages", {"member1": now_ms, "member2": now_ms + 60000})
        args = argparse.Namespace(json=True)
        result = await cmd_delayed_list(r, args)
        assert result == 0
        output = capsys.readouterr().out
        lines = [ln for ln in output.strip().splitlines() if ln.strip()]
        assert len(lines) >= 2
        for line in lines:
            obj = json.loads(line)
            assert "member" in obj
            assert "ready_ms" in obj
            assert "eta_s" in obj


class TestRecalcPriorityJson:
    """E5: recalc-priority --json outputs JSON."""

    @pytest.mark.asyncio
    async def test_recalc_priority__json_output(self, r, capsys):
        """recalc-priority with --json outputs valid JSON."""
        await r.set("player:priority:puuid-1", "1")
        await r.set("player:priority:puuid-2", "1")
        args = argparse.Namespace(json=True)
        result = await cmd_recalc_priority(r, args)
        assert result == 0
        output = capsys.readouterr().out
        obj = json.loads(output.strip())
        assert "player_priority_key_count" in obj
        assert obj["player_priority_key_count"] == 2


class TestRecalcPlayersJson:
    """E5: recalc-players --json outputs JSON."""

    @pytest.mark.asyncio
    async def test_recalc_players__json_output(self, r, capsys):
        """recalc-players with --json outputs valid JSON."""
        await r.hset(
            "player:puuid-one",
            mapping={
                "game_name": "PlayerOne",
                "tag_line": "001",
                "region": "na1",
                "seeded_at": "2026-03-19T12:00:00+00:00",
            },
        )
        args = argparse.Namespace(json=True)
        result = await cmd_recalc_players(r, args)
        assert result == 0
        output = capsys.readouterr().out
        obj = json.loads(output.strip())
        assert "players_indexed" in obj
        assert obj["players_indexed"] == 1


class TestDelayedFlush:
    """OPS-16-07: admin delayed-flush removes all delayed messages."""

    @pytest.mark.asyncio
    async def test_delayed_flush__deletes_all(self, r, capsys):
        """delayed-flush --all → delayed:messages emptied."""
        await r.zadd("delayed:messages", {"m1": 100, "m2": 200, "m3": 300})
        args = argparse.Namespace(all=True, yes=True)
        result = await cmd_delayed_flush(r, args)
        assert result == 0
        assert await r.exists("delayed:messages") == 0
        output = capsys.readouterr().out
        assert "flushed 3" in output

    @pytest.mark.asyncio
    async def test_delayed_flush__empty__info_message(self, r, capsys):
        """delayed-flush --all on empty set → info message."""
        args = argparse.Namespace(all=True, yes=True)
        result = await cmd_delayed_flush(r, args)
        assert result == 0
        assert "empty" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_delayed_flush__no_all__returns_error(self, r, capsys):
        """delayed-flush without --all → error."""
        args = argparse.Namespace(all=False, yes=True)
        result = await cmd_delayed_flush(r, args)
        assert result == 1
        assert "--all is required" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_delayed_flush__no_confirm__aborts(self, r, monkeypatch, capsys):
        """delayed-flush --all without confirmation aborts."""
        await r.zadd("delayed:messages", {"m1": 100})
        monkeypatch.setattr("builtins.input", lambda _: "n")
        args = argparse.Namespace(all=True, yes=False)
        result = await cmd_delayed_flush(r, args)
        assert result == 1
        assert "aborted" in capsys.readouterr().out
        # Entries remain
        assert await r.zcard("delayed:messages") == 1


# ---------------------------------------------------------------------------
# Dispatch: new commands
# ---------------------------------------------------------------------------


class TestDispatchNewCommands:
    """New commands dispatch correctly through _dispatch."""

    @pytest.mark.asyncio
    async def test_reset_stats_dispatches(self, monkeypatch):
        """main(['admin', 'reset-stats', 'Faker#KR1']) dispatches command='reset-stats'."""
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
            result = await main(["admin", "reset-stats", "Faker#KR1"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "reset-stats"
        assert args.riot_id == "Faker#KR1"

    @pytest.mark.asyncio
    async def test_clear_priority_all_dispatches(self, monkeypatch):
        """main(['admin', 'clear-priority', '--all']) dispatches correctly."""
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
            result = await main(["admin", "clear-priority", "--all"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "clear-priority"
        assert args.all is True

    @pytest.mark.asyncio
    async def test_delayed_list_dispatches(self, monkeypatch):
        """main(['admin', 'delayed-list']) dispatches correctly."""
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
            result = await main(["admin", "delayed-list"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "delayed-list"

    @pytest.mark.asyncio
    async def test_delayed_flush_dispatches(self, monkeypatch):
        """main(['admin', 'delayed-flush', '--all']) dispatches correctly."""
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
            result = await main(["admin", "delayed-flush", "--all"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "delayed-flush"
        assert args.all is True

    @pytest.mark.asyncio
    async def test_dlq_archive_list_dispatches(self, monkeypatch):
        """main(['admin', 'dlq', 'archive', 'list']) dispatches correctly."""
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
            result = await main(["admin", "dlq", "archive", "list"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "dlq"
        assert args.dlq_command == "archive"
        assert args.archive_command == "list"

    @pytest.mark.asyncio
    async def test_dlq_archive_clear_dispatches(self, monkeypatch):
        """main(['admin', 'dlq', 'archive', 'clear', '--all']) dispatches correctly."""
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
            result = await main(["admin", "dlq", "archive", "clear", "--all"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.command == "dlq"
        assert args.dlq_command == "archive"
        assert args.archive_command == "clear"
        assert args.all is True

    @pytest.mark.asyncio
    async def test_yes_flag_passed_through(self, monkeypatch):
        """main(['admin', '-y', 'system-halt']) passes yes=True to args."""
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
            result = await main(["admin", "-y", "system-halt"])
        assert result == 0
        args = mock_dispatch.call_args[0][3]
        assert args.yes is True


# ---------------------------------------------------------------------------
# Sprint 4: backfill-champions
# ---------------------------------------------------------------------------

try:
    import lupa  # noqa: F401

    _LUPA_AVAILABLE = True
except ImportError:
    _LUPA_AVAILABLE = False


class TestBackfillChampionsNoMatches:
    """Empty match:status:parsed → nothing to backfill."""

    @pytest.mark.asyncio
    async def test_backfill_champions_no_matches(self, r, cfg, capsys):
        """No parsed matches → info message, returns 0."""
        args = argparse.Namespace()
        from lol_admin.main import cmd_backfill_champions

        result = await cmd_backfill_champions(r, cfg, args)
        assert result == 0
        assert "No matches to backfill" in capsys.readouterr().out


class TestBackfillChampionsProcessesRanked:
    """Ranked match (queue_id=420) gets processed."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua scripts")
    async def test_backfill_champions_processes_ranked(self, r, cfg, capsys):
        """Ranked match with participants populates champion stats."""
        from lol_admin.main import cmd_backfill_champions

        # Seed a parsed ranked match
        await r.sadd("match:status:parsed", "NA1_100")
        await r.hset(
            "match:NA1_100",
            mapping={
                "queue_id": "420",
                "patch": "14.5",
                "game_start": "1710000000000",
                "game_mode": "CLASSIC",
            },
        )
        await r.hset(
            "participant:NA1_100:puuid-1",
            mapping={
                "champion_name": "Zed",
                "team_position": "MID",
                "win": "1",
                "kills": "10",
                "deaths": "3",
                "assists": "5",
                "gold_earned": "15000",
                "total_minions_killed": "200",
                "total_damage_dealt_to_champions": "25000",
                "vision_score": "30",
                "double_kills": "2",
                "triple_kills": "1",
                "quadra_kills": "0",
                "penta_kills": "0",
            },
        )
        args = argparse.Namespace()
        result = await cmd_backfill_champions(r, cfg, args)
        assert result == 0
        output = capsys.readouterr().out
        assert "Backfilled champion stats from 1 ranked" in output
        # Verify champion stats were written
        stats = await r.hgetall("champion:stats:Zed:14.5:MID")
        assert stats["games"] == "1"
        assert stats["wins"] == "1"
        assert stats["kills"] == "10"
        assert stats["deaths"] == "3"
        assert stats["assists"] == "5"
        # Verify champion index
        score = await r.zscore("champion:index:14.5", "Zed:MID")
        assert score is not None
        assert score == 1.0
        # Verify patch list
        assert await r.zscore("patch:list", "14.5") is not None
        # Verify backfill done tracking
        assert await r.sismember("champion:backfill:done", "NA1_100")


class TestBackfillChampionsSkipsNonRanked:
    """Non-ranked match skipped."""

    @pytest.mark.asyncio
    async def test_backfill_champions_skips_non_ranked(self, r, cfg, capsys):
        """Match with queue_id != 420 is skipped."""
        from lol_admin.main import cmd_backfill_champions

        await r.sadd("match:status:parsed", "NA1_200")
        await r.hset(
            "match:NA1_200",
            mapping={
                "queue_id": "450",  # ARAM
                "patch": "14.5",
                "game_start": "1710000000000",
            },
        )
        await r.hset(
            "participant:NA1_200:puuid-2",
            mapping={
                "champion_name": "Ahri",
                "team_position": "MID",
                "win": "0",
                "kills": "5",
                "deaths": "7",
                "assists": "10",
            },
        )
        args = argparse.Namespace()
        result = await cmd_backfill_champions(r, cfg, args)
        assert result == 0
        output = capsys.readouterr().out
        assert "Backfilled champion stats from 0 ranked" in output
        # No champion stats should exist
        assert await r.exists("champion:stats:Ahri:14.5:MID") == 0


class TestBackfillChampionsIdempotent:
    """Second run skips already-done matches."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa required for Lua scripts")
    async def test_backfill_champions_idempotent(self, r, cfg, capsys):
        """Second run with same parsed matches does nothing."""
        from lol_admin.main import cmd_backfill_champions

        await r.sadd("match:status:parsed", "NA1_300")
        await r.hset(
            "match:NA1_300",
            mapping={
                "queue_id": "420",
                "patch": "14.5",
                "game_start": "1710000000000",
            },
        )
        await r.hset(
            "participant:NA1_300:puuid-3",
            mapping={
                "champion_name": "Yasuo",
                "team_position": "MID",
                "win": "1",
                "kills": "8",
                "deaths": "4",
                "assists": "6",
                "gold_earned": "12000",
                "total_minions_killed": "180",
                "total_damage_dealt_to_champions": "20000",
                "vision_score": "25",
                "double_kills": "1",
                "triple_kills": "0",
                "quadra_kills": "0",
                "penta_kills": "0",
            },
        )
        # First run
        args = argparse.Namespace()
        result = await cmd_backfill_champions(r, cfg, args)
        assert result == 0
        first_output = capsys.readouterr().out
        assert "1 ranked" in first_output
        # Verify stats after first run
        stats1 = await r.hgetall("champion:stats:Yasuo:14.5:MID")
        assert stats1["games"] == "1"
        # Second run — same matches already done
        result2 = await cmd_backfill_champions(r, cfg, args)
        assert result2 == 0
        second_output = capsys.readouterr().out
        assert "No matches to backfill" in second_output
        # Stats should NOT have doubled
        stats2 = await r.hgetall("champion:stats:Yasuo:14.5:MID")
        assert stats2["games"] == "1"


# ---------------------------------------------------------------------------
# _relative_age — ISO timestamp to human-readable relative age
# ---------------------------------------------------------------------------


class TestRelativeAge:
    """_relative_age converts ISO timestamp to human-readable relative age."""

    def test_seconds_ago(self):
        """Timestamp 30 seconds ago returns '30s ago'."""
        from datetime import UTC, datetime, timedelta

        then = datetime.now(tz=UTC) - timedelta(seconds=30)
        result = _relative_age(then.isoformat())
        assert result.endswith("s ago")
        # Should be close to 30 (within 2s of test execution)
        num = int(result.replace("s ago", ""))
        assert 28 <= num <= 32

    def test_minutes_ago(self):
        """Timestamp 5 minutes ago returns '5m ago'."""
        from datetime import UTC, datetime, timedelta

        then = datetime.now(tz=UTC) - timedelta(minutes=5)
        result = _relative_age(then.isoformat())
        assert result == "5m ago"

    def test_hours_ago(self):
        """Timestamp 3 hours ago returns '3h ago'."""
        from datetime import UTC, datetime, timedelta

        then = datetime.now(tz=UTC) - timedelta(hours=3)
        result = _relative_age(then.isoformat())
        assert result == "3h ago"

    def test_days_ago(self):
        """Timestamp 2 days ago returns '2d ago'."""
        from datetime import UTC, datetime, timedelta

        then = datetime.now(tz=UTC) - timedelta(days=2)
        result = _relative_age(then.isoformat())
        assert result == "2d ago"

    def test_future_timestamp(self):
        """Timestamp in the future returns 'future'."""
        from datetime import UTC, datetime, timedelta

        then = datetime.now(tz=UTC) + timedelta(hours=1)
        result = _relative_age(then.isoformat())
        assert result == "future"

    def test_invalid_string(self):
        """Non-ISO string returns '?'."""
        assert _relative_age("not-a-timestamp") == "?"

    def test_empty_string(self):
        """Empty string returns '?'."""
        assert _relative_age("") == "?"

    def test_boundary_59_seconds(self):
        """59 seconds ago is still in the seconds range."""
        from datetime import UTC, datetime, timedelta

        then = datetime.now(tz=UTC) - timedelta(seconds=59)
        result = _relative_age(then.isoformat())
        assert result.endswith("s ago")

    def test_boundary_60_seconds(self):
        """Exactly 60 seconds ago crosses into minutes."""
        from datetime import UTC, datetime, timedelta

        then = datetime.now(tz=UTC) - timedelta(seconds=60)
        result = _relative_age(then.isoformat())
        assert result == "1m ago"

    def test_boundary_3599_seconds(self):
        """3599 seconds ago is still in minutes range."""
        from datetime import UTC, datetime, timedelta

        then = datetime.now(tz=UTC) - timedelta(seconds=3599)
        result = _relative_age(then.isoformat())
        assert result == "59m ago"

    def test_boundary_3600_seconds(self):
        """Exactly 3600 seconds ago crosses into hours."""
        from datetime import UTC, datetime, timedelta

        then = datetime.now(tz=UTC) - timedelta(seconds=3600)
        result = _relative_age(then.isoformat())
        assert result == "1h ago"
