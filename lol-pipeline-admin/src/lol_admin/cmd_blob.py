"""Admin CLI: blob-cleanup command (filesystem-only, no Redis)."""

from __future__ import annotations

import argparse
from pathlib import Path

from lol_pipeline.config import Config

from lol_admin._blob_cleanup import cleanup_blobs, parse_duration
from lol_admin._helpers import _confirm, _print_error, _print_info, _print_ok


def _format_bytes(n: int) -> str:
    """Format byte count as a human-readable string."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.1f} GB"


def _validate_blob_args(
    cfg: Config, args: argparse.Namespace
) -> tuple[Path, int] | None:
    """Validate config and CLI args. Returns (blob_dir, seconds) or None on error."""
    if not cfg.blob_data_dir:
        _print_error("blob_data_dir is not configured (BLOB_DATA_DIR env var)")
        return None
    blob_dir = Path(cfg.blob_data_dir)
    if not blob_dir.is_dir():
        _print_error(f"blob data dir does not exist: {blob_dir}")
        return None
    try:
        older_than_seconds = parse_duration(args.older_than)
    except ValueError as exc:
        _print_error(str(exc))
        return None
    return blob_dir, older_than_seconds


async def cmd_blob_cleanup(cfg: Config, args: argparse.Namespace) -> int:
    """Delete old blob JSON files from the blob data directory."""
    validated = _validate_blob_args(cfg, args)
    if validated is None:
        return 1
    blob_dir, older_than_seconds = validated

    # Always run a dry-run preview first
    preview = cleanup_blobs(blob_dir, older_than_seconds, dry_run=True)
    _print_info(
        f"found {preview['deleted']} files "
        f"({_format_bytes(preview['bytes_freed'])}) older than {args.older_than}"
    )

    if preview["deleted"] == 0:
        _print_ok("nothing to clean up")
        return 0

    if args.dry_run:
        _print_info("dry-run mode — no files deleted")
        return 0

    if not args.force and not _confirm(
        f"Delete {preview['deleted']} files "
        f"({_format_bytes(preview['bytes_freed'])})? [y/N]: ",
        args,
    ):
        _print_info("aborted")
        return 1

    result = cleanup_blobs(blob_dir, older_than_seconds, dry_run=False)
    _print_ok(
        f"deleted {result['deleted']} files, "
        f"freed {_format_bytes(result['bytes_freed'])}, "
        f"pruned {result['dirs_pruned']} empty dirs"
    )
    return 0
