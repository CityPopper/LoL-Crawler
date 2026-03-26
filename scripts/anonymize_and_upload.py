#!/usr/bin/env python3
"""Upload Riot match data to Hugging Face Datasets (private repo).

Iterates all pipeline-data/riot-api/NA1/*.jsonl.zst files and uploads
them directly to the configured HF dataset repo. No anonymization needed
since the dataset is private.

Usage:
    pip install -r scripts/requirements.txt
    python scripts/anonymize_and_upload.py
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
from pathlib import Path

import zstandard as zstd
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "pipeline-data" / "riot-api" / "NA1"
DEFAULT_REPO = "CityPopper/LoL-Scraper"


def _load_env_token() -> str:
    """Load HUGGINGFACE_TOKEN from environment or .env file."""
    load_dotenv()
    token = os.environ.get("HUGGINGFACE_TOKEN", "")
    if token:
        return token

    print("ERROR: HUGGINGFACE_TOKEN not found in environment or .env file")
    sys.exit(1)


def _is_anomalous_date(filename: str) -> bool:
    """Check if a filename has an anomalous date bucket (year < 2020)."""
    match = re.match(r"(\d{4})-\d{2}", Path(filename).stem)
    if match:
        year = int(match.group(1))
        return year < 2020
    return False


def _process_file(
    zst_path: Path,
    api: object,
    repo_id: str,
    token: str,
) -> int:
    """Upload a single .jsonl.zst file to HF. Returns record count."""
    dctx = zstd.ZstdDecompressor()
    record_count = 0
    with open(zst_path, "rb") as f:
        with dctx.stream_reader(f) as reader:
            for line in io.TextIOWrapper(reader, encoding="utf-8"):
                if line.strip():
                    record_count += 1
    _upload_file(api, zst_path, zst_path.name, repo_id, token)
    return record_count


def _upload_file(
    api: object,
    local_path: Path,
    filename: str,
    repo_id: str,
    token: str,
) -> None:
    """Upload a file to Hugging Face Datasets."""
    api.upload_file(  # type: ignore[union-attr]
        path_or_fileobj=str(local_path),
        path_in_repo=f"NA1/{filename}",
        repo_id=repo_id,
        repo_type="dataset",
        token=token,
    )


def main() -> int:
    """Upload all .jsonl.zst files to Hugging Face."""
    from huggingface_hub import HfApi

    if not DATA_DIR.exists():
        print(f"ERROR: Data directory not found: {DATA_DIR}")
        return 1

    zst_files = sorted(DATA_DIR.glob("*.jsonl.zst"))
    if not zst_files:
        print("No .jsonl.zst files found.")
        return 0

    # Load token and resolve repo once
    token = _load_env_token()
    api = HfApi(token=token)
    repo_id = os.environ.get("HF_DATASET_REPO") or DEFAULT_REPO

    # Ensure the HF repo exists before uploading
    api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)

    # Filter out anomalous date files
    filtered_files: list[Path] = []
    for zst_path in zst_files:
        if _is_anomalous_date(zst_path.name):
            print(f"WARNING: Skipping anomalous file {zst_path.name} — unexpected date bucket")
        else:
            filtered_files.append(zst_path)

    total = len(filtered_files)
    print(f"Found {total} files to process in {DATA_DIR}")

    processed = 0

    for i, zst_path in enumerate(filtered_files, 1):
        start = time.monotonic()
        try:
            records = _process_file(zst_path, api, repo_id, token)
            elapsed = time.monotonic() - start
            processed += 1
            print(f"Uploaded {i}/{total}: {zst_path.name} ({records} records, {elapsed:.1f}s)")
        except Exception as exc:
            print(f"FAILED {i}/{total}: {zst_path.name} — {exc}")
            return 1

    print(f"\nDone. Uploaded: {processed}, Total: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
