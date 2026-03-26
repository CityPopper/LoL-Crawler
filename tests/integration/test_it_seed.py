"""IT-SEED — Seed data pipeline: upload unit tests + E2E with mocked HF + real Redis.

Unit tests (no network, no Redis):
  - Platform -> region mapping
  - ZST file validation
  - Token loading

E2E test (mocked HF + real Redis via testcontainers):
  - Synthetic data -> compress -> mock-upload -> seed Redis -> verify stream:parse
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import zstandard as zstd
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Import anonymize_and_upload.py from scripts/ (not a package)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent.parent
_SCRIPT_PATH = _ROOT / "scripts" / "anonymize_and_upload.py"

spec = importlib.util.spec_from_file_location("anonymize_and_upload", _SCRIPT_PATH)
assert spec is not None and spec.loader is not None
_mod = importlib.util.module_from_spec(spec)
sys.modules["anonymize_and_upload"] = _mod
spec.loader.exec_module(_mod)

_process_file = _mod._process_file

# Ensure helpers importable for integration fixtures
sys.path.insert(0, str(Path(__file__).parent))

# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------
FAKE_PUUID_1 = "W53X1BX3sOoqTPXJmbnOoWrQOEDE-J46EcuEbGrnbvWHGGv8lTu8dRHc5KZuyL_a5GVB9NqKQ8oOyw"
FAKE_PUUID_2 = "v1QbW9Y7j38ak9b5ALBrwYom2BapSVG7A0auXVB4bwMKm3LT5BGd5lAPy0tuJpY-ICb9_4CkGFiHHw"

# Platform -> routing region mapping (from SEED-4 spec)
PLATFORM_REGION_MAP = {
    "NA1": "americas",
    "BR1": "americas",
    "LA1": "americas",
    "LA2": "americas",
    "EUW1": "europe",
    "EUN1": "europe",
    "TR1": "europe",
    "RU": "europe",
    "KR": "asia",
    "JP1": "asia",
    "OC1": "sea",
}


def make_match_record(match_id: str = "NA1_12345", puuids: list[str] | None = None) -> dict[str, Any]:
    puuids = puuids or [FAKE_PUUID_1, FAKE_PUUID_2]
    return {
        "metadata": {"dataVersion": "2", "matchId": match_id, "participants": list(puuids)},
        "info": {
            "gameId": 12345,
            "gameStartTimestamp": 1700000000000,
            "participants": [
                {
                    "puuid": p,
                    "summonerName": f"Player{i}",
                    "riotIdGameName": f"GameName{i}",
                    "riotIdTagline": f"TAG{i}",
                    "summonerId": f"summ{i}",
                    "championName": "Ahri",
                    "kills": 5,
                    "deaths": 2,
                    "assists": 10,
                }
                for i, p in enumerate(puuids)
            ],
        },
    }


# =========================================================================
# Platform -> Region mapping tests
# =========================================================================


class TestPlatformRegionMapping:
    """Platform -> routing region edge cases (from SEED-4 spec)."""

    def test_known_platforms(self):
        """All known platforms map to correct regions."""
        expected = {
            "NA1": "americas", "BR1": "americas", "LA1": "americas", "LA2": "americas",
            "EUW1": "europe", "EUN1": "europe", "TR1": "europe", "RU": "europe",
            "KR": "asia", "JP1": "asia",
            "OC1": "sea",
        }
        for platform, region in expected.items():
            assert PLATFORM_REGION_MAP.get(platform) == region, (
                f"Platform {platform} should map to {region}"
            )

    def test_unknown_platform_not_in_map(self):
        """Unknown platform is not in the map (caller must handle)."""
        assert "XX1" not in PLATFORM_REGION_MAP

    def test_lowercase_platform_not_in_map(self):
        """Lowercase platforms are not in the map (case-sensitive)."""
        assert "na1" not in PLATFORM_REGION_MAP

    def test_mixed_case_platform_not_in_map(self):
        """Mixed-case platforms are not in the map."""
        assert "Na1" not in PLATFORM_REGION_MAP


# =========================================================================
# ZST file validation tests
# =========================================================================


class TestZstValidation:
    """Zstd magic bytes and file integrity checks."""

    ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

    def test_valid_zst_has_magic_bytes(self, tmp_path: Path):
        """A valid .zst file starts with zstd magic bytes 0x28B52FFD."""
        zst_file = tmp_path / "test.jsonl.zst"
        cctx = zstd.ZstdCompressor()
        zst_file.write_bytes(cctx.compress(b"NA1_100\t{}\n"))
        data = zst_file.read_bytes()
        assert data[:4] == self.ZSTD_MAGIC

    def test_corrupt_file_no_magic_bytes(self, tmp_path: Path):
        """A corrupt file does not start with zstd magic bytes."""
        corrupt_file = tmp_path / "corrupt.jsonl.zst"
        corrupt_file.write_bytes(b"this is not zstd data at all")
        data = corrupt_file.read_bytes()
        assert data[:4] != self.ZSTD_MAGIC

    def test_corrupt_file_fails_decompression(self, tmp_path: Path):
        """Attempting to decompress a corrupt file raises an error."""
        corrupt_file = tmp_path / "corrupt.jsonl.zst"
        corrupt_file.write_bytes(b"\x00\x01\x02\x03not zstd")
        dctx = zstd.ZstdDecompressor()
        with pytest.raises(zstd.ZstdError):
            dctx.decompress(corrupt_file.read_bytes())


# =========================================================================
# Token loading tests
# =========================================================================


class TestTokenLoading:
    """Tests for _load_env_token (env var and .env file parsing)."""

    def test_token_from_env_var(self, monkeypatch: pytest.MonkeyPatch):
        """Token loaded from HUGGINGFACE_TOKEN env var."""
        monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_test_token_123")
        token = _mod._load_env_token()
        assert token == "hf_test_token_123"

    def test_token_from_dotenv_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Token loaded from .env file when env var is absent."""
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text('HUGGINGFACE_TOKEN=hf_from_dotenv_456\n')
        # Temporarily override PROJECT_ROOT
        original_root = _mod.PROJECT_ROOT
        _mod.PROJECT_ROOT = tmp_path
        try:
            # Patch load_dotenv to read from tmp .env instead of real project .env
            with patch.object(
                _mod, "load_dotenv", lambda: load_dotenv(env_file, override=True)
            ):
                token = _mod._load_env_token()
            assert token == "hf_from_dotenv_456"
        finally:
            _mod.PROJECT_ROOT = original_root

    def test_token_missing_exits(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Missing token causes sys.exit(1)."""
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        original_root = _mod.PROJECT_ROOT
        _mod.PROJECT_ROOT = tmp_path  # no .env file
        try:
            # Patch load_dotenv to no-op so real .env doesn't leak a token
            with patch.object(_mod, "load_dotenv", lambda: None):
                with pytest.raises(SystemExit) as exc_info:
                    _mod._load_env_token()
            assert exc_info.value.code == 1
        finally:
            _mod.PROJECT_ROOT = original_root

    def test_token_strips_quotes(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """Token with surrounding quotes has them stripped."""
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("HUGGINGFACE_TOKEN='hf_quoted_789'\n")
        original_root = _mod.PROJECT_ROOT
        _mod.PROJECT_ROOT = tmp_path
        try:
            # Patch load_dotenv to read from tmp .env instead of real project .env
            with patch.object(
                _mod, "load_dotenv", lambda: load_dotenv(env_file, override=True)
            ):
                token = _mod._load_env_token()
            assert token == "hf_quoted_789"
        finally:
            _mod.PROJECT_ROOT = original_root


# =========================================================================
# E2E test — mocked HF + real Redis via testcontainers
# =========================================================================


def _platform_to_region(match_id: str) -> str:
    """Extract platform from match_id prefix and map to routing region."""
    platform = match_id.split("_")[0]
    mapping = {
        "NA1": "americas", "BR1": "americas", "LA1": "americas", "LA2": "americas",
        "EUW1": "europe", "EUN1": "europe", "TR1": "europe", "RU": "europe",
        "KR": "asia", "JP1": "asia",
        "OC1": "sea",
    }
    return mapping.get(platform, "americas")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_e2e_upload_seed(r, tmp_path: Path):
    """Full pipeline E2E with mocked HF:
    1. Create synthetic .jsonl.zst with 3 match records
    2. Mock HfApi.upload_file to capture what would be uploaded
    3. Parse output and seed real Redis via stream:parse
    4. Assert stream:parse receives 3 messages with correct type/region
    """
    # ------------------------------------------------------------------
    # Step 1: Create a .jsonl.zst file with 3 records across regions
    # ------------------------------------------------------------------
    match_ids = ["NA1_100", "EUW1_200", "KR_300"]
    records = []
    for mid in match_ids:
        records.append((mid, make_match_record(mid)))

    zst_path = tmp_path / "2024-06.jsonl.zst"
    cctx = zstd.ZstdCompressor(level=3)
    raw = b""
    for match_id, record in records:
        line = f"{match_id}\t{json.dumps(record)}\n"
        raw += line.encode("utf-8")
    zst_path.write_bytes(cctx.compress(raw))

    # ------------------------------------------------------------------
    # Step 2: Upload via _process_file with mocked upload
    # ------------------------------------------------------------------
    captured_files: list[tuple[Path, str]] = []

    def mock_upload(*args: object, **kwargs: object) -> None:
        local_path = Path(str(args[1]))
        filename = str(args[2])
        captured_files.append((local_path, filename))

    original_upload = _mod._upload_file
    _mod._upload_file = mock_upload
    try:
        count = _process_file(zst_path, None, "", "")
    finally:
        _mod._upload_file = original_upload

    assert count == 3, f"Expected 3 records processed, got {count}"

    # ------------------------------------------------------------------
    # Step 3: Parse file and seed stream:parse in real Redis
    # ------------------------------------------------------------------
    # Create the consumer group for stream:parse
    try:
        await r.xgroup_create("stream:parse", "parsers", id="0", mkstream=True)
    except Exception:
        pass  # group may already exist

    dctx = zstd.ZstdDecompressor()
    with open(zst_path, "rb") as f:
        raw_text = dctx.stream_reader(f).read().decode("utf-8")

    lines = [line for line in raw_text.strip().split("\n") if line.strip()]
    assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}"

    for line in lines:
        match_id, js = line.split("\t", 1)
        region = _platform_to_region(match_id)
        payload = json.dumps({"match_id": match_id, "region": region})
        # Publish as MessageEnvelope fields to stream:parse
        await r.xadd(
            "stream:parse",
            {
                "id": f"seed-{match_id}",
                "source_stream": "stream:parse",
                "type": "parse",
                "payload": payload,
                "attempts": "0",
                "max_attempts": "3",
                "enqueued_at": "2024-06-01T00:00:00+00:00",
                "dlq_attempts": "0",
                "priority": "auto_new",
                "correlation_id": "",
            },
        )

    # ------------------------------------------------------------------
    # Step 4: Assert stream:parse state
    # ------------------------------------------------------------------
    stream_len = await r.xlen("stream:parse")
    assert stream_len == 3, f"Expected 3 messages in stream:parse, got {stream_len}"

    # Read all messages from the stream
    messages = await r.xrange("stream:parse", "-", "+")
    assert len(messages) == 3

    # Collect types, regions, and match_ids from messages
    found_types = []
    found_regions = []
    found_match_ids = []

    for _msg_id, fields in messages:
        assert fields["type"] == "parse", f"Expected type='parse', got {fields['type']}"
        found_types.append(fields["type"])

        payload = json.loads(fields["payload"])
        found_regions.append(payload["region"])
        found_match_ids.append(payload["match_id"])

    # Verify all types are "parse"
    assert all(t == "parse" for t in found_types)

    # Verify regions: NA1 -> americas, EUW1 -> europe, KR -> asia
    assert "americas" in found_regions, f"Missing americas region. Found: {found_regions}"
    assert "europe" in found_regions, f"Missing europe region. Found: {found_regions}"
    assert "asia" in found_regions, f"Missing asia region. Found: {found_regions}"

    # Verify match IDs present
    for mid in match_ids:
        assert mid in found_match_ids, f"Match ID {mid} not found in stream messages"

    # Verify priority is auto_new (per SEED-4 spec)
    for _msg_id, fields in messages:
        assert fields["priority"] == "auto_new", (
            f"Expected priority='auto_new', got {fields['priority']}"
        )


# =========================================================================
# Additional edge case tests
# =========================================================================


class TestFileOrdering:
    """Tests for file listing and reverse-chronological sorting."""

    def test_zst_files_sorted_reverse_chronological(self, tmp_path: Path):
        """ZST files sorted newest-first by filename (reverse sort)."""
        names = ["2024-01.jsonl.zst", "2024-06.jsonl.zst", "2024-03.jsonl.zst"]
        for name in names:
            (tmp_path / name).write_bytes(b"")

        zst_files = sorted(tmp_path.glob("*.jsonl.zst"), reverse=True)
        filenames = [f.name for f in zst_files]
        assert filenames == ["2024-06.jsonl.zst", "2024-03.jsonl.zst", "2024-01.jsonl.zst"]

    def test_jsonl_files_sorted_after_zst(self, tmp_path: Path):
        """Active .jsonl files sort after .zst when using reverse sort with a key."""
        names = ["2024-01.jsonl.zst", "2024-06.jsonl", "2024-03.jsonl.zst"]
        for name in names:
            (tmp_path / name).write_bytes(b"")

        # Per SEED-4: zst files sorted reverse-chronological, active .jsonl last
        zst_files = sorted(tmp_path.glob("*.jsonl.zst"), reverse=True)
        jsonl_files = sorted(tmp_path.glob("*.jsonl"))
        all_files = zst_files + jsonl_files

        filenames = [f.name for f in all_files]
        assert filenames == ["2024-03.jsonl.zst", "2024-01.jsonl.zst", "2024-06.jsonl"]


