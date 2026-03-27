"""Unit tests for lol_admin._blob_cleanup — MF-3c: blob cleanup logic.

Tests cover:
- ``parse_duration``: converts human-friendly duration strings to seconds.
- ``cleanup_blobs``: walks blob directory, deletes old JSON files, prunes empty dirs.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from lol_admin._blob_cleanup import cleanup_blobs, parse_duration


# ---------------------------------------------------------------------------
# parse_duration — valid inputs
# ---------------------------------------------------------------------------


class TestParseDurationValid:
    """parse_duration converts recognised duration strings to seconds."""

    def test_parse_duration__90d__returns_7776000(self) -> None:
        assert parse_duration("90d") == 7776000

    def test_parse_duration__24h__returns_86400(self) -> None:
        assert parse_duration("24h") == 86400

    def test_parse_duration__30m__returns_1800(self) -> None:
        assert parse_duration("30m") == 1800

    def test_parse_duration__3600s__returns_3600(self) -> None:
        assert parse_duration("3600s") == 3600


# ---------------------------------------------------------------------------
# parse_duration — invalid inputs
# ---------------------------------------------------------------------------


class TestParseDurationInvalid:
    """parse_duration raises ValueError on unrecognised formats."""

    def test_parse_duration__foo__raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unrecognized duration format"):
            parse_duration("foo")

    def test_parse_duration__empty_string__raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unrecognized duration format"):
            parse_duration("")

    def test_parse_duration__90x__raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unrecognized duration format"):
            parse_duration("90x")


# ---------------------------------------------------------------------------
# Helpers for cleanup_blobs tests
# ---------------------------------------------------------------------------

_OLD_MTIME = time.time() - 200 * 86400  # 200 days ago
_NEW_MTIME = time.time() - 1 * 86400  # 1 day ago


def _create_blob(
    base: Path,
    source: str,
    platform: str,
    filename: str,
    content: str = '{"test": true}',
    mtime: float = _OLD_MTIME,
) -> Path:
    """Create a blob file with a specific mtime and return its path."""
    d = base / source / platform
    d.mkdir(parents=True, exist_ok=True)
    f = d / filename
    f.write_text(content)
    os.utime(f, (mtime, mtime))
    return f


# ---------------------------------------------------------------------------
# cleanup_blobs — file deletion
# ---------------------------------------------------------------------------


class TestCleanupBlobsDeletion:
    """cleanup_blobs deletes old .json files and keeps recent ones."""

    def test_cleanup_blobs__old_file__deleted(self, tmp_path: Path) -> None:
        """Files older than threshold are deleted."""
        _create_blob(tmp_path, "riot", "na1", "old_match.json", mtime=_OLD_MTIME)

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400)

        assert result["deleted"] == 1
        assert not (tmp_path / "riot" / "na1" / "old_match.json").exists()

    def test_cleanup_blobs__new_file__kept(self, tmp_path: Path) -> None:
        """Files newer than threshold are kept."""
        _create_blob(tmp_path, "riot", "na1", "new_match.json", mtime=_NEW_MTIME)

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400)

        assert result["deleted"] == 0
        assert (tmp_path / "riot" / "na1" / "new_match.json").exists()

    def test_cleanup_blobs__mixed_old_and_new__only_old_deleted(self, tmp_path: Path) -> None:
        """Old files are deleted, new files are kept in the same directory."""
        _create_blob(tmp_path, "riot", "na1", "old.json", mtime=_OLD_MTIME)
        _create_blob(tmp_path, "riot", "na1", "new.json", mtime=_NEW_MTIME)

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400)

        assert result["deleted"] == 1
        assert not (tmp_path / "riot" / "na1" / "old.json").exists()
        assert (tmp_path / "riot" / "na1" / "new.json").exists()


# ---------------------------------------------------------------------------
# cleanup_blobs — .tmp_ files skipped
# ---------------------------------------------------------------------------


class TestCleanupBlobsTmpSkip:
    """cleanup_blobs skips .tmp_* files even if they are old."""

    def test_cleanup_blobs__tmp_file__skipped_and_counted(self, tmp_path: Path) -> None:
        """.tmp_* files are not deleted even if old; counted in skipped_tmp."""
        _create_blob(tmp_path, "riot", "na1", ".tmp_writing.json", mtime=_OLD_MTIME)

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400)

        assert result["deleted"] == 0
        assert result["skipped_tmp"] == 1
        assert (tmp_path / "riot" / "na1" / ".tmp_writing.json").exists()


# ---------------------------------------------------------------------------
# cleanup_blobs — non-.json files skipped
# ---------------------------------------------------------------------------


class TestCleanupBlobsNonJsonSkip:
    """cleanup_blobs ignores files that do not end with .json."""

    def test_cleanup_blobs__non_json_file__skipped_entirely(self, tmp_path: Path) -> None:
        """Non-.json files are neither deleted nor counted in skipped_tmp."""
        _create_blob(tmp_path, "riot", "na1", "notes.txt", mtime=_OLD_MTIME)

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400)

        assert result["deleted"] == 0
        assert result["skipped_tmp"] == 0
        assert (tmp_path / "riot" / "na1" / "notes.txt").exists()


# ---------------------------------------------------------------------------
# cleanup_blobs — directory pruning
# ---------------------------------------------------------------------------


class TestCleanupBlobsDirPruning:
    """cleanup_blobs prunes empty directories after file deletion."""

    def test_cleanup_blobs__all_files_deleted__dirs_pruned(self, tmp_path: Path) -> None:
        """Source and platform dirs are removed when all their files are deleted."""
        _create_blob(tmp_path, "riot", "na1", "match1.json", mtime=_OLD_MTIME)

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400)

        assert result["deleted"] == 1
        assert result["dirs_pruned"] >= 1
        # Both the platform dir and source dir should be gone
        assert not (tmp_path / "riot" / "na1").exists()
        assert not (tmp_path / "riot").exists()

    def test_cleanup_blobs__dir_still_has_files__not_pruned(self, tmp_path: Path) -> None:
        """Directories with remaining files are not pruned."""
        _create_blob(tmp_path, "riot", "na1", "old.json", mtime=_OLD_MTIME)
        _create_blob(tmp_path, "riot", "na1", "new.json", mtime=_NEW_MTIME)

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400)

        assert result["deleted"] == 1
        assert result["dirs_pruned"] == 0
        assert (tmp_path / "riot" / "na1").is_dir()


# ---------------------------------------------------------------------------
# cleanup_blobs — dry_run mode
# ---------------------------------------------------------------------------


class TestCleanupBlobsDryRun:
    """cleanup_blobs with dry_run=True reports counts but does not delete."""

    def test_cleanup_blobs__dry_run__no_files_deleted(self, tmp_path: Path) -> None:
        """dry_run=True does not delete any files."""
        _create_blob(tmp_path, "riot", "na1", "old.json", mtime=_OLD_MTIME)

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400, dry_run=True)

        assert result["deleted"] == 1  # reported as would-be-deleted
        assert (tmp_path / "riot" / "na1" / "old.json").exists()

    def test_cleanup_blobs__dry_run__returns_correct_counts(self, tmp_path: Path) -> None:
        """dry_run=True returns the correct deleted count and bytes_freed."""
        content = '{"match": "data", "large": true}'
        f = _create_blob(
            tmp_path, "riot", "na1", "old.json", content=content, mtime=_OLD_MTIME
        )
        expected_size = f.stat().st_size

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400, dry_run=True)

        assert result["deleted"] == 1
        assert result["bytes_freed"] == expected_size

    def test_cleanup_blobs__dry_run__dirs_not_pruned(self, tmp_path: Path) -> None:
        """dry_run=True does not prune empty directories."""
        _create_blob(tmp_path, "riot", "na1", "old.json", mtime=_OLD_MTIME)

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400, dry_run=True)

        assert result["dirs_pruned"] == 0
        assert (tmp_path / "riot" / "na1").is_dir()


# ---------------------------------------------------------------------------
# cleanup_blobs — bytes_freed
# ---------------------------------------------------------------------------


class TestCleanupBlobsBytesCounting:
    """cleanup_blobs accurately tracks bytes_freed."""

    def test_cleanup_blobs__bytes_freed__reflects_actual_file_sizes(
        self, tmp_path: Path
    ) -> None:
        """bytes_freed is the sum of actual sizes of deleted files."""
        content_a = '{"a": 1}'
        content_b = '{"b": 2, "extra": "data"}'
        fa = _create_blob(tmp_path, "riot", "na1", "a.json", content=content_a, mtime=_OLD_MTIME)
        fb = _create_blob(tmp_path, "riot", "na1", "b.json", content=content_b, mtime=_OLD_MTIME)
        expected = fa.stat().st_size + fb.stat().st_size

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400)

        assert result["bytes_freed"] == expected
        assert result["deleted"] == 2


# ---------------------------------------------------------------------------
# cleanup_blobs — non-existent directory
# ---------------------------------------------------------------------------


class TestCleanupBlobsNonExistentDir:
    """cleanup_blobs handles a non-existent blob_data_dir gracefully."""

    def test_cleanup_blobs__nonexistent_dir__returns_zeros(self, tmp_path: Path) -> None:
        """Non-existent directory does not raise; returns all-zero result."""
        missing = tmp_path / "does_not_exist"

        result = cleanup_blobs(missing, older_than_seconds=90 * 86400)

        assert result == {
            "deleted": 0,
            "skipped_tmp": 0,
            "bytes_freed": 0,
            "dirs_pruned": 0,
        }


# ---------------------------------------------------------------------------
# cleanup_blobs — .json.zst files
# ---------------------------------------------------------------------------


class TestCleanupBlobsZstFiles:
    """cleanup_blobs handles zstd-compressed .json.zst files."""

    def test_cleanup_finds_zst_files(self, tmp_path: Path) -> None:
        """Old .json.zst files are included in deletion."""
        f = _create_blob(
            tmp_path, "riot", "NA1", "NA1_40004.json.zst",
            content="fake-compressed-data", mtime=_OLD_MTIME,
        )

        result = cleanup_blobs(tmp_path, older_than_seconds=90 * 86400)

        assert result["deleted"] == 1
        assert not f.exists()
