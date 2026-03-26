"""Tests for pure functions in scripts/seed_from_disk.py (SEED-4)."""

from __future__ import annotations

from pathlib import Path

import pytest

import seed_from_disk
from seed_from_disk import (
    _PLATFORM_TO_REGION,
    _extract_match_id,
    _platform_from_match_id,
    _validate_zst,
)


# -- Platform-to-region mapping tests --


def test_platform_to_region_na1() -> None:
    """NA1 maps to americas."""
    assert _PLATFORM_TO_REGION["NA1"] == "americas"


def test_platform_to_region_euw1() -> None:
    """EUW1 maps to europe."""
    assert _PLATFORM_TO_REGION["EUW1"] == "europe"


def test_platform_to_region_kr() -> None:
    """KR maps to asia."""
    assert _PLATFORM_TO_REGION["KR"] == "asia"


def test_platform_to_region_oc1() -> None:
    """OC1 maps to sea."""
    assert _PLATFORM_TO_REGION["OC1"] == "sea"


# -- _extract_match_id tests --


def test_extract_match_id_normal() -> None:
    """Tab-separated line returns the match_id before the tab."""
    result = _extract_match_id("NA1_12345\t{}")
    assert result == "NA1_12345"


def test_extract_match_id_no_tab() -> None:
    """Line without a tab returns None."""
    result = _extract_match_id("badline")
    assert result is None


# -- _platform_from_match_id tests --


def test_platform_from_match_id() -> None:
    """NA1_12345 returns NA1."""
    result = _platform_from_match_id("NA1_12345")
    assert result == "NA1"


def test_platform_from_match_id_no_underscore() -> None:
    """Match ID without underscore returns None."""
    result = _platform_from_match_id("BADMATCHID")
    assert result is None


# -- _validate_zst tests --


def test_validate_zst_corrupt(tmp_path: Path) -> None:
    """_validate_zst calls sys.exit(1) when file has wrong magic bytes."""
    corrupt_file = tmp_path / "corrupt.jsonl.zst"
    corrupt_file.write_bytes(b"\x00\x00\x00\x00some junk data")

    with pytest.raises(SystemExit) as exc_info:
        _validate_zst(corrupt_file)
    assert exc_info.value.code == 1


def test_validate_zst_good(tmp_path: Path) -> None:
    """_validate_zst does not exit for valid zstd magic bytes."""
    good_file = tmp_path / "good.jsonl.zst"
    # Write valid zstd magic bytes followed by dummy data
    good_file.write_bytes(b"\x28\xb5\x2f\xfd" + b"\x00" * 100)

    # Should not raise
    _validate_zst(good_file)


# -- _discover_files tests --


def test_discover_files_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_discover_files returns zst files newest-first, then jsonl files."""
    # Create temp directory with 3 zst files named by month
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    (data_dir / "2024-01.jsonl.zst").write_bytes(b"\x28\xb5\x2f\xfd")
    (data_dir / "2024-03.jsonl.zst").write_bytes(b"\x28\xb5\x2f\xfd")
    (data_dir / "2024-02.jsonl.zst").write_bytes(b"\x28\xb5\x2f\xfd")

    # Monkeypatch _DATA_DIR to point to our temp dir
    monkeypatch.setattr(seed_from_disk, "_DATA_DIR", data_dir)

    from seed_from_disk import _discover_files

    result = _discover_files()
    names = [p.name for p in result]

    # Should be reverse-sorted (newest first)
    assert names == ["2024-03.jsonl.zst", "2024-02.jsonl.zst", "2024-01.jsonl.zst"]


def test_discover_files_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_discover_files returns empty list when _DATA_DIR does not exist."""
    nonexistent = tmp_path / "does-not-exist"
    monkeypatch.setattr(seed_from_disk, "_DATA_DIR", nonexistent)

    from seed_from_disk import _discover_files

    result = _discover_files()
    assert result == []
