#!/usr/bin/env python3
"""Seed Redis from local .jsonl.zst files (pipeline rebuild fallback).

Called by `just up` if Redis player data is empty after dump.rdb restore.
Prefers dump.rdb path (Redis auto-loads on start) -- this script is the fallback.

Usage: python scripts/seed_from_disk.py
"""
from __future__ import annotations

import asyncio
import io
import os
import sys
from pathlib import Path

import redis.asyncio as aioredis
import zstandard

from lol_pipeline.constants import STREAM_PARSE
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import PRIORITY_AUTO_NEW
from lol_pipeline.streams import DEFAULT_STREAM_MAXLEN

# Zstd magic bytes
_ZST_MAGIC = b"\x28\xb5\x2f\xfd"

_BATCH_SIZE = 200
_BACKPRESSURE_THRESHOLD = DEFAULT_STREAM_MAXLEN // 2
_BACKPRESSURE_SLEEP_S = 2

_PLATFORM_TO_REGION: dict[str, str] = {
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

# Project root: scripts/ is one level below
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _PROJECT_ROOT / "pipeline-data" / "riot-api" / "NA1"


def _discover_files() -> list[Path]:
    """Find .jsonl.zst and .jsonl files, newest first.

    Compressed files sorted reverse-chronologically, then active .jsonl files appended.
    """
    if not _DATA_DIR.exists():
        return []

    zst_files = sorted(_DATA_DIR.glob("*.jsonl.zst"), reverse=True)
    jsonl_files = sorted(_DATA_DIR.glob("*.jsonl"), reverse=True)
    return zst_files + jsonl_files


def _validate_zst(path: Path) -> None:
    """Abort if a .zst file does not start with zstd magic bytes."""
    with path.open("rb") as f:
        header = f.read(4)
    if header != _ZST_MAGIC:
        print(
            f"ERROR: {path.name} appears corrupt. "
            "Run 'python scripts/download_seed.py' first.",
            file=sys.stderr,
        )
        sys.exit(1)


def _extract_match_id(line: str) -> str | None:
    """Extract match_id from a tab-separated JSONL line."""
    tab_idx = line.find("\t")
    if tab_idx == -1:
        return None
    return line[:tab_idx]


def _platform_from_match_id(match_id: str) -> str | None:
    """Extract platform prefix from match_id (e.g. NA1_12345 -> NA1)."""
    underscore_idx = match_id.find("_")
    if underscore_idx == -1:
        return None
    return match_id[:underscore_idx]


def _iter_lines_zst(path: Path):  # noqa: ANN201
    """Yield lines from a .zst compressed file via streaming decompression."""
    dctx = zstandard.ZstdDecompressor()
    with path.open("rb") as f, dctx.stream_reader(f) as reader:
        text = io.TextIOWrapper(reader, encoding="utf-8")
        yield from text


def _iter_lines_jsonl(path: Path):  # noqa: ANN201
    """Yield lines from a plain .jsonl file."""
    with path.open(encoding="utf-8") as f:
        yield from f


async def main() -> None:
    """Seed stream:parse from local JSONL files."""
    # Load .env if python-dotenv is available
    try:
        from dotenv import load_dotenv

        load_dotenv(_PROJECT_ROOT / ".env")
    except ImportError:
        pass

    host = os.environ.get("REDIS_HOST", "localhost")
    port = os.environ.get("REDIS_PORT", "6379")
    r = aioredis.from_url(f"redis://{host}:{port}", decode_responses=True)

    try:
        # Empty check: if Redis already has player data, skip
        players_count: int = await r.zcard("players:all")  # type: ignore[misc]
        if players_count > 0:
            print("Redis already has data. Skipping.")
            return

        files = _discover_files()
        if not files:
            print(f"No data files found in {_DATA_DIR}", file=sys.stderr)
            sys.exit(1)

        # Validate all .zst files before processing
        for f in files:
            if f.suffix == ".zst":
                _validate_zst(f)

        total = 0
        batch_n = 0

        for filepath in files:
            is_zst = filepath.name.endswith(".jsonl.zst")
            lines = _iter_lines_zst(filepath) if is_zst else _iter_lines_jsonl(filepath)

            batch: list[dict[str, str]] = []

            for line in lines:
                line = line.rstrip("\n")
                if not line:
                    continue

                match_id = _extract_match_id(line)
                if match_id is None:
                    continue

                platform = _platform_from_match_id(match_id)
                if platform is None:
                    continue

                region = _PLATFORM_TO_REGION.get(platform)
                if region is None:
                    print(f"WARNING: Unknown platform '{platform}' in {match_id}, skipping")
                    continue

                envelope = MessageEnvelope(
                    source_stream=STREAM_PARSE,
                    type="parse",
                    payload={"match_id": match_id, "region": region},
                    max_attempts=5,
                    priority=PRIORITY_AUTO_NEW,
                )
                batch.append(envelope.to_redis_fields())

                if len(batch) >= _BATCH_SIZE:
                    # Throttle: check stream depth
                    stream_len: int = await r.xlen(STREAM_PARSE)  # type: ignore[misc]
                    while stream_len > _BACKPRESSURE_THRESHOLD:
                        await asyncio.sleep(_BACKPRESSURE_SLEEP_S)
                        stream_len = await r.xlen(STREAM_PARSE)  # type: ignore[misc]

                    for fields in batch:
                        await r.xadd(STREAM_PARSE, fields)  # type: ignore[misc]

                    total += len(batch)
                    batch_n += 1
                    print(
                        f"Batch {batch_n}: {len(batch)} msgs "
                        f"({total} total, {filepath.name})",
                        flush=True,
                    )
                    batch = []

            # Flush remaining batch for this file
            if batch:
                stream_len = await r.xlen(STREAM_PARSE)  # type: ignore[misc]
                while stream_len > _BACKPRESSURE_THRESHOLD:
                    await asyncio.sleep(_BACKPRESSURE_SLEEP_S)
                    stream_len = await r.xlen(STREAM_PARSE)  # type: ignore[misc]

                for fields in batch:
                    await r.xadd(STREAM_PARSE, fields)  # type: ignore[misc]

                total += len(batch)
                batch_n += 1
                print(
                    f"Batch {batch_n}: {len(batch)} msgs "
                    f"({total} total, {filepath.name})",
                    flush=True,
                )

        print(f"Done. Seeded {total} messages to {STREAM_PARSE}.")
    finally:
        await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
