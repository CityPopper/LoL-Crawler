"""Tests for pure functions in scripts/download_seed.py (SEED-3)."""

from __future__ import annotations

from pathlib import Path

import pytest

import download_seed
from download_seed import (
    _already_downloaded,
    _get_repo_id,
    _get_token,
    _validate_zst,
)


# -- _validate_zst tests --


def test_validate_zst_good(tmp_path: Path) -> None:
    """_validate_zst returns True for valid zstd magic bytes."""
    good_file = tmp_path / "good.jsonl.zst"
    good_file.write_bytes(b"\x28\xb5\x2f\xfd" + b"\x00" * 100)
    assert _validate_zst(good_file) is True


def test_validate_zst_bad(tmp_path: Path) -> None:
    """_validate_zst returns False for wrong magic bytes."""
    bad_file = tmp_path / "bad.jsonl.zst"
    bad_file.write_bytes(b"\x00\x01\x02\x03some junk")
    assert _validate_zst(bad_file) is False


def test_validate_zst_missing_file(tmp_path: Path) -> None:
    """_validate_zst returns False when the file does not exist."""
    missing = tmp_path / "missing.jsonl.zst"
    assert _validate_zst(missing) is False


# -- _get_repo_id tests --


def test_get_repo_id_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns default repo when HF_DATASET_REPO is not set."""
    monkeypatch.delenv("HF_DATASET_REPO", raising=False)
    result = _get_repo_id()
    assert result == "CityPopper/LoL-Scraper"


def test_get_repo_id_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns the env var value when HF_DATASET_REPO is set."""
    monkeypatch.setenv("HF_DATASET_REPO", "myorg/custom-repo")
    result = _get_repo_id()
    assert result == "myorg/custom-repo"


# -- _get_token tests --


def test_get_token_empty_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_token returns None when HUGGINGFACE_TOKEN is empty string."""
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "")
    result = _get_token()
    assert result is None


def test_get_token_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_token returns None when HUGGINGFACE_TOKEN is not set at all."""
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    result = _get_token()
    assert result is None


def test_get_token_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_token returns the token string when set."""
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_abc123xyz")
    result = _get_token()
    assert result == "hf_abc123xyz"


# -- _already_downloaded tests --


def test_already_downloaded_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_already_downloaded returns False when no dump.rdb or zst files exist."""
    fake_data_dir = tmp_path / "data"
    fake_data_dir.mkdir()
    fake_redis_dir = tmp_path / "redis"
    fake_redis_dir.mkdir()
    fake_dump = fake_redis_dir / "dump.rdb"

    monkeypatch.setattr(download_seed, "DATA_DIR", fake_data_dir)
    monkeypatch.setattr(download_seed, "DUMP_PATH", fake_dump)

    assert _already_downloaded() is False


def test_already_downloaded_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_already_downloaded returns True when both dump.rdb and zst files exist."""
    fake_data_dir = tmp_path / "data"
    fake_data_dir.mkdir()
    (fake_data_dir / "2024-01.jsonl.zst").write_bytes(b"\x28\xb5\x2f\xfd")

    fake_redis_dir = tmp_path / "redis"
    fake_redis_dir.mkdir()
    fake_dump = fake_redis_dir / "dump.rdb"
    fake_dump.write_bytes(b"REDIS0011")

    monkeypatch.setattr(download_seed, "DATA_DIR", fake_data_dir)
    monkeypatch.setattr(download_seed, "DUMP_PATH", fake_dump)

    assert _already_downloaded() is True


def test_already_downloaded_only_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_already_downloaded returns False when only dump.rdb exists (no zst)."""
    fake_data_dir = tmp_path / "data"
    fake_data_dir.mkdir()
    # No zst files in data dir

    fake_redis_dir = tmp_path / "redis"
    fake_redis_dir.mkdir()
    fake_dump = fake_redis_dir / "dump.rdb"
    fake_dump.write_bytes(b"REDIS0011")

    monkeypatch.setattr(download_seed, "DATA_DIR", fake_data_dir)
    monkeypatch.setattr(download_seed, "DUMP_PATH", fake_dump)

    assert _already_downloaded() is False
