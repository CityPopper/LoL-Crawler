"""Tests for pure functions in scripts/download_seed.py (SEED-3)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import download_seed
from download_seed import (
    _already_downloaded,
    _get_repo_id,
    _get_token,
    _validate_zst,
    main,
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


# -- main() tests (HFV-4) --


ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def test_main_skips_when_already_downloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 0 without calling snapshot_download when data exists."""
    monkeypatch.setattr("sys.argv", ["download_seed.py"])
    monkeypatch.setattr(download_seed, "_already_downloaded", lambda: True)

    # If snapshot_download is reached, the test fails
    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.snapshot_download = MagicMock(side_effect=AssertionError("should not be called"))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    assert main() == 0


def test_main_force_flag_bypasses_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() with --force calls snapshot_download even when data exists."""
    monkeypatch.setattr("sys.argv", ["download_seed.py", "--force"])
    monkeypatch.setattr(download_seed, "_already_downloaded", lambda: True)

    mock_sd = MagicMock()
    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.snapshot_download = mock_sd  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    # main will call snapshot_download, then look for files in tmp_dir — that's fine,
    # it just won't find any NA1/*.jsonl.zst or dump.rdb to move, but still returns 0.
    assert main() == 0
    mock_sd.assert_called_once()


def test_main_moves_zst_files_and_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() moves zst files from temp dir to DATA_DIR and returns 0."""
    monkeypatch.setattr("sys.argv", ["download_seed.py"])
    monkeypatch.setattr(download_seed, "_already_downloaded", lambda: False)

    fake_data_dir = tmp_path / "data" / "NA1"
    fake_redis_dir = tmp_path / "redis"
    fake_dump_path = fake_redis_dir / "dump.rdb"
    monkeypatch.setattr(download_seed, "DATA_DIR", fake_data_dir)
    monkeypatch.setattr(download_seed, "REDIS_DIR", fake_redis_dir)
    monkeypatch.setattr(download_seed, "DUMP_PATH", fake_dump_path)

    # The controlled temp dir where snapshot_download "writes" files
    controlled_tmp = tmp_path / "hf_tmp"
    controlled_tmp.mkdir()

    def fake_snapshot_download(**kwargs: object) -> str:
        local_dir = kwargs["local_dir"]
        na1_dir = Path(local_dir) / "NA1"
        na1_dir.mkdir(parents=True, exist_ok=True)
        (na1_dir / "2024-01.jsonl.zst").write_bytes(ZSTD_MAGIC + b"\x00" * 100)
        return str(local_dir)

    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.snapshot_download = fake_snapshot_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    # Monkeypatch TemporaryDirectory to use our controlled dir
    class FakeTempDir:
        def __enter__(self) -> str:
            return str(controlled_tmp)

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr("download_seed.tempfile.TemporaryDirectory", FakeTempDir)

    result = main()

    assert result == 0
    assert (fake_data_dir / "2024-01.jsonl.zst").exists()


def test_main_warns_on_corrupt_zst(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() prints WARNING for corrupt zst files but still returns 0."""
    monkeypatch.setattr("sys.argv", ["download_seed.py"])
    monkeypatch.setattr(download_seed, "_already_downloaded", lambda: False)

    fake_data_dir = tmp_path / "data" / "NA1"
    fake_redis_dir = tmp_path / "redis"
    fake_dump_path = fake_redis_dir / "dump.rdb"
    monkeypatch.setattr(download_seed, "DATA_DIR", fake_data_dir)
    monkeypatch.setattr(download_seed, "REDIS_DIR", fake_redis_dir)
    monkeypatch.setattr(download_seed, "DUMP_PATH", fake_dump_path)

    controlled_tmp = tmp_path / "hf_tmp"
    controlled_tmp.mkdir()

    def fake_snapshot_download(**kwargs: object) -> str:
        local_dir = kwargs["local_dir"]
        na1_dir = Path(local_dir) / "NA1"
        na1_dir.mkdir(parents=True, exist_ok=True)
        # Write corrupt bytes — not valid zstd
        (na1_dir / "corrupt.jsonl.zst").write_bytes(b"not zstd data")
        return str(local_dir)

    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.snapshot_download = fake_snapshot_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    class FakeTempDir:
        def __enter__(self) -> str:
            return str(controlled_tmp)

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr("download_seed.tempfile.TemporaryDirectory", FakeTempDir)

    result = main()

    assert result == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.out


def test_main_missing_huggingface_hub_returns_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() returns 1 when huggingface_hub is not installed."""
    monkeypatch.setattr("sys.argv", ["download_seed.py"])
    monkeypatch.setattr(download_seed, "_already_downloaded", lambda: False)

    # Remove huggingface_hub from sys.modules so the import fails
    monkeypatch.delitem(sys.modules, "huggingface_hub", raising=False)

    # Make the import raise ImportError
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__  # type: ignore[union-attr]

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "huggingface_hub":
            raise ImportError("No module named 'huggingface_hub'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert main() == 1


def test_main_404_error_returns_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() returns 1 when snapshot_download raises a 404 error."""
    monkeypatch.setattr("sys.argv", ["download_seed.py"])
    monkeypatch.setattr(download_seed, "_already_downloaded", lambda: False)

    fake_hf = types.ModuleType("huggingface_hub")
    fake_hf.snapshot_download = MagicMock(side_effect=Exception("404 not found"))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hf)

    assert main() == 1
