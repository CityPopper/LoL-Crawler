"""Core blob cleanup logic and duration parser.

Pure filesystem operations -- no Redis, no admin CLI imports.
Used by cmd_blob.py (MF-3b) to implement the ``blob-cleanup`` admin command.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"^(\d+)([dhms])$")

_UNIT_SECONDS: dict[str, int] = {
    "d": 86400,
    "h": 3600,
    "m": 60,
    "s": 1,
}


def parse_duration(s: str) -> int:
    """Parse a human-friendly duration string to seconds.

    Supported formats: ``90d``, ``24h``, ``30m``, ``3600s``
    (days, hours, minutes, seconds).

    Raises:
        ValueError: If the string does not match a recognized format.
    """
    m = _DURATION_RE.match(s.strip())
    if not m:
        raise ValueError(
            f"unrecognized duration format: {s!r}  "
            f"(expected <int><unit>, e.g. 90d, 24h, 30m, 3600s)"
        )
    value = int(m.group(1))
    unit = m.group(2)
    return value * _UNIT_SECONDS[unit]


def cleanup_blobs(
    blob_data_dir: Path,
    older_than_seconds: int,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Walk the blob directory and delete (or report) old JSON files.

    Layout expected::

        {blob_data_dir}/{source_name}/{platform}/{match_id}.json

    Skips ``.tmp_*`` files (in-progress atomic writes) and non-``.json`` files.
    After deletion, prunes empty directories bottom-up.

    Returns:
        dict with keys ``deleted``, ``skipped_tmp``, ``bytes_freed``, ``dirs_pruned``.
    """
    if not blob_data_dir.is_dir():
        log.warning("blob data dir does not exist: %s", blob_data_dir)
        return _make_result(0, 0, 0, 0)

    cutoff = time.time() - older_than_seconds
    deleted = 0
    skipped_tmp = 0
    bytes_freed = 0
    dirs_pruned = 0

    # Walk bottom-up so we can prune empty dirs after deleting files.
    for dirpath, _dirnames, filenames in os.walk(blob_data_dir, topdown=False):
        d, s, b = _process_files(Path(dirpath), filenames, cutoff, dry_run=dry_run)
        deleted += d
        skipped_tmp += s
        bytes_freed += b

        dirs_pruned += _try_prune_dir(Path(dirpath), blob_data_dir, dry_run=dry_run)

    return _make_result(deleted, skipped_tmp, bytes_freed, dirs_pruned)


def _process_files(
    dirpath: Path,
    filenames: list[str],
    cutoff: float,
    *,
    dry_run: bool,
) -> tuple[int, int, int]:
    """Process files in a single directory. Returns (deleted, skipped_tmp, bytes_freed)."""
    deleted = 0
    skipped_tmp = 0
    bytes_freed = 0

    for fname in filenames:
        if fname.startswith(".tmp_"):
            skipped_tmp += 1
            continue
        if not (fname.endswith(".json") or fname.endswith(".json.zst")):
            continue

        fpath = dirpath / fname
        stat = fpath.stat()
        if stat.st_mtime >= cutoff:
            continue

        size = stat.st_size
        if dry_run:
            log.info("[dry-run] would delete %s (%d bytes)", fpath, size)
        else:
            fpath.unlink()
            log.info("deleted %s (%d bytes)", fpath, size)
        deleted += 1
        bytes_freed += size

    return deleted, skipped_tmp, bytes_freed


def _try_prune_dir(dirpath: Path, root: Path, *, dry_run: bool) -> int:
    """Prune a directory if it is empty. Never prunes the root. Returns 1 if pruned, else 0."""
    if dirpath == root:
        return 0
    if dry_run or not _is_empty_dir(dirpath):
        return 0
    dirpath.rmdir()
    log.info("pruned empty dir %s", dirpath)
    return 1


def _is_empty_dir(path: Path) -> bool:
    """Return True if the directory exists and contains no entries."""
    try:
        return not any(path.iterdir())
    except OSError:
        return False


def _make_result(
    deleted: int, skipped_tmp: int, bytes_freed: int, dirs_pruned: int
) -> dict[str, int]:
    return {
        "deleted": deleted,
        "skipped_tmp": skipped_tmp,
        "bytes_freed": bytes_freed,
        "dirs_pruned": dirs_pruned,
    }
