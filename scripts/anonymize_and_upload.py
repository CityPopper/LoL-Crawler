#!/usr/bin/env python3
"""Anonymize PII from Riot match data and upload to Hugging Face Datasets.

Iterates all pipeline-data/riot-api/NA1/*.jsonl.zst files, replaces PUUIDs with
deterministic anon_ hashes, strips summoner names / Riot IDs / summoner IDs,
re-compresses, uploads to HF, and overwrites the local file with the anonymized version.

Usage:
    pip install -r scripts/requirements.txt
    python scripts/anonymize_and_upload.py
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import zstandard as zstd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "pipeline-data" / "riot-api" / "NA1"

PII_KEYS_TO_REMOVE = {"summonerName", "riotIdGameName", "riotIdTagline", "summonerId"}


def _load_env_token() -> str:
    """Load HUGGINGFACE_TOKEN from environment or .env file."""
    token = os.environ.get("HUGGINGFACE_TOKEN")
    if token:
        return token

    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "HUGGINGFACE_TOKEN":
                value = value.strip().strip("'\"")
                if value:
                    return value

    print("ERROR: HUGGINGFACE_TOKEN not found in environment or .env file")
    sys.exit(1)


def _anon_puuid(puuid: str, cache: dict[str, str]) -> str:
    """Map a PUUID to anon_{sha256[:16]}, caching for consistency."""
    if puuid in cache:
        return cache[puuid]
    hashed = hashlib.sha256(puuid.encode()).hexdigest()[:16]
    anon = f"anon_{hashed}"
    cache[puuid] = anon
    return anon


def _is_already_anonymized(record: dict) -> bool:
    """Check if the first participant PUUID already starts with anon_."""
    participants = record.get("info", {}).get("participants", [])
    if not participants:
        return False
    first_puuid = participants[0].get("puuid", "")
    return first_puuid.startswith("anon_")


def _anonymize_record(record: dict, cache: dict[str, str]) -> dict:
    """Anonymize a single match record in-place and return it."""
    # metadata.participants[] — replace PUUIDs
    meta_participants = record.get("metadata", {}).get("participants", [])
    for i, puuid in enumerate(meta_participants):
        meta_participants[i] = _anon_puuid(puuid, cache)

    # info.participants[] — replace puuid, remove PII keys
    for participant in record.get("info", {}).get("participants", []):
        puuid = participant.get("puuid", "")
        if puuid:
            participant["puuid"] = _anon_puuid(puuid, cache)
        for key in PII_KEYS_TO_REMOVE:
            participant.pop(key, None)

    return record


def _process_file(zst_path: Path, cache: dict[str, str]) -> tuple[int, bool]:
    """Process a single .jsonl.zst file. Returns (record_count, was_skipped)."""
    dctx = zstd.ZstdDecompressor()

    # First pass: check if already anonymized
    with open(zst_path, "rb") as f:
        with dctx.stream_reader(f) as reader:
            text_reader = io.TextIOWrapper(reader, encoding="utf-8")
            first_line = text_reader.readline()
            if not first_line.strip():
                return 0, True
            _, js = first_line.split("\t", 1)
            record = json.loads(js)
            if _is_already_anonymized(record):
                return 0, True

    # Full pass: anonymize all records into a temp file
    cctx = zstd.ZstdCompressor(level=3)
    record_count = 0

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jsonl.zst", dir=zst_path.parent)
    try:
        with open(zst_path, "rb") as f_in:
            with dctx.stream_reader(f_in) as reader:
                text_in = io.TextIOWrapper(reader, encoding="utf-8")
                with open(tmp_fd, "wb") as f_out:
                    with cctx.stream_writer(f_out) as writer:
                        for line in text_in:
                            line = line.rstrip("\n")
                            if not line:
                                continue
                            match_id, js = line.split("\t", 1)
                            record = json.loads(js)
                            _anonymize_record(record, cache)
                            out_line = f"{match_id}\t{json.dumps(record, separators=(',', ':'))}\n"
                            writer.write(out_line.encode("utf-8"))
                            record_count += 1

        # Upload to Hugging Face
        _upload_file(Path(tmp_path), zst_path.name)

        # Overwrite original with anonymized version
        os.replace(tmp_path, zst_path)
    except Exception:
        # Clean up temp file on failure
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    return record_count, False


def _upload_file(local_path: Path, filename: str) -> None:
    """Upload a file to Hugging Face Datasets."""
    from huggingface_hub import HfApi

    token = _load_env_token()
    api = HfApi()
    user_info = api.whoami(token=token)
    repo_id = f"{user_info['name']}/lol-pipeline-seed"

    api.upload_file(
        path_or_fileobj=str(local_path),
        path_in_repo=f"NA1/{filename}",
        repo_id=repo_id,
        repo_type="dataset",
        token=token,
    )


def main() -> int:
    """Anonymize all .jsonl.zst files and upload to Hugging Face."""
    if not DATA_DIR.exists():
        print(f"ERROR: Data directory not found: {DATA_DIR}")
        return 1

    zst_files = sorted(DATA_DIR.glob("*.jsonl.zst"))
    if not zst_files:
        print("No .jsonl.zst files found.")
        return 0

    total = len(zst_files)
    print(f"Found {total} files to process in {DATA_DIR}")

    cache: dict[str, str] = {}
    processed = 0
    skipped = 0

    for i, zst_path in enumerate(zst_files, 1):
        start = time.monotonic()
        try:
            records, was_skipped = _process_file(zst_path, cache)
            elapsed = time.monotonic() - start
            if was_skipped:
                skipped += 1
                print(f"Skipped {i}/{total}: {zst_path.name} (already anonymized)")
            else:
                processed += 1
                print(
                    f"Processed {i}/{total}: {zst_path.name}"
                    f" ({records} records, {elapsed:.1f}s)"
                )
        except Exception as exc:
            print(f"FAILED {i}/{total}: {zst_path.name} — {exc}")
            return 1

    print(f"\nDone. Processed: {processed}, Skipped: {skipped}, Total: {total}")
    print(f"Unique PUUIDs anonymized: {len(cache)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
