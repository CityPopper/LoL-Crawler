#!/usr/bin/env python3
"""Download anonymized seed data from Hugging Face Datasets.

Downloads dump.rdb + NA1/*.jsonl.zst to local disk.
Called by `just download` and auto-called by `just up` if data is missing.

Usage:
    python scripts/download_seed.py
    just download
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "pipeline-data" / "riot-api" / "NA1"
REDIS_DIR = PROJECT_ROOT / "redis-data"
DUMP_PATH = REDIS_DIR / "dump.rdb"
DEFAULT_REPO = "CityPopper/lol-pipeline-seed"


def _load_env() -> None:
    """Load .env file if python-dotenv is available."""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except ImportError:
        pass


def _get_repo_id() -> str:
    """Read HF_DATASET_REPO from environment."""
    return os.environ.get("HF_DATASET_REPO", DEFAULT_REPO)


def _get_token() -> str | None:
    """Read optional HUGGINGFACE_TOKEN from environment."""
    token = os.environ.get("HUGGINGFACE_TOKEN", "")
    return token if token else None


def _already_downloaded() -> bool:
    """Check if seed data is already present on disk."""
    has_dump = DUMP_PATH.exists()
    has_zst = any(DATA_DIR.glob("*.jsonl.zst")) if DATA_DIR.exists() else False
    return has_dump and has_zst


def _validate_zst(path: Path) -> bool:
    """Validate that a file starts with zstd magic bytes."""
    try:
        with open(path, "rb") as f:
            header = f.read(4)
        return header == ZSTD_MAGIC
    except OSError:
        return False


def main() -> int:
    """Download seed data from Hugging Face Datasets."""
    parser = argparse.ArgumentParser(description="Download seed data from Hugging Face Datasets")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if files already exist",
    )
    args = parser.parse_args()

    _load_env()

    if not args.force and _already_downloaded():
        print("Seed data already present. Use --force to re-download.")
        return 0

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("ERROR: huggingface_hub not installed.")
        print("  pip install -r scripts/requirements.txt")
        return 1

    repo_id = _get_repo_id()
    token = _get_token()

    print(f"Downloading seed data from {repo_id}...")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            snapshot_download(
                repo_id=repo_id,
                repo_type="dataset",
                allow_patterns=["NA1/*.jsonl.zst", "dump.rdb"],
                local_dir=str(tmp_dir),
                token=token,
            )

            # Move NA1/*.jsonl.zst files
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp_na1 = Path(tmp_dir) / "NA1"
            zst_count = 0
            if tmp_na1.exists():
                for zst_file in sorted(tmp_na1.glob("*.jsonl.zst")):
                    dest = DATA_DIR / zst_file.name
                    shutil.move(str(zst_file), str(dest))
                    print(f"Downloaded: {zst_file.name}")
                    if not _validate_zst(dest):
                        print(f"  WARNING: {dest.name} may be corrupt (invalid zstd header)")
                    zst_count += 1

            # Move dump.rdb
            tmp_dump = Path(tmp_dir) / "dump.rdb"
            has_dump = False
            if tmp_dump.exists():
                REDIS_DIR.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tmp_dump), str(DUMP_PATH))
                print("Downloaded: dump.rdb")
                has_dump = True

            parts = []
            if zst_count > 0:
                parts.append(f"{zst_count} data files")
            if has_dump:
                parts.append("dump.rdb")
            print(f"Done — {' + '.join(parts)} ready.")

    except Exception as exc:
        exc_str = str(exc)
        if "404" in exc_str or "not found" in exc_str.lower():
            print(f"ERROR: Repository '{repo_id}' not found on Hugging Face.")
            print("  Check HF_DATASET_REPO in your .env file.")
            print("  To create the repo, run: just upload")
        else:
            print(f"ERROR: Download failed — {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
