#!/usr/bin/env python3
"""Consolidate individual match JSON files into JSONL bundles + zstd compression.

Groups matches by platform and month (from gameCreation timestamp), writes
{platform}/{YYYY-MM}.jsonl bundles, then compresses completed months to .jsonl.zst.

Usage:
    python scripts/consolidate_match_data.py [--data-dir PATH] [--delete-originals]

Defaults to lol-pipeline-fetcher/match-data/.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import zstandard as zstd


def _month_from_json(data: str) -> str:
    """Extract YYYY-MM from gameCreation in the match JSON."""
    try:
        parsed = json.loads(data)
        ts_ms = parsed["info"]["gameCreation"]
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
        return dt.strftime("%Y-%m")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return "unknown"


def _load_existing_ids(platform_dir: Path) -> set[str]:
    """Build a set of match_ids already present in JSONL bundles."""
    existing: set[str] = set()
    for bundle in platform_dir.glob("*.jsonl"):
        for line in bundle.read_text().splitlines():
            if "\t" in line:
                existing.add(line.split("\t", 1)[0])
    for bundle in platform_dir.glob("*.jsonl.zst"):
        dctx = zstd.ZstdDecompressor()
        text = dctx.decompress(bundle.read_bytes()).decode()
        for line in text.splitlines():
            if "\t" in line:
                existing.add(line.split("\t", 1)[0])
    return existing


def consolidate(data_dir: Path, delete_originals: bool = False) -> dict[str, int]:
    """Consolidate individual JSON files into JSONL bundles.

    Returns a dict of {bundle_path: count} for bundles written.
    """
    stats: dict[str, int] = {}

    for platform_dir in sorted(data_dir.iterdir()):
        if not platform_dir.is_dir():
            continue

        json_files = sorted(platform_dir.glob("*.json"))
        if not json_files:
            continue

        print(f"  {platform_dir.name}: {len(json_files)} individual files")

        # Load existing IDs once (O(n) instead of O(n²))
        existing_ids = _load_existing_ids(platform_dir)

        # Group by month
        groups: dict[str, list[tuple[Path, str, str]]] = defaultdict(list)
        for jf in json_files:
            match_id = jf.stem
            if match_id in existing_ids:
                if delete_originals:
                    jf.unlink()
                continue
            data = jf.read_text()
            month = _month_from_json(data)
            groups[month].append((jf, match_id, data))

        for month, entries in sorted(groups.items()):
            bundle_path = platform_dir / f"{month}.jsonl"
            written = 0
            with bundle_path.open("a") as f:
                for jf, match_id, data in entries:
                    # Compact: remove whitespace from JSON
                    try:
                        compact = json.dumps(json.loads(data), separators=(",", ":"))
                    except json.JSONDecodeError:
                        compact = data
                    f.write(f"{match_id}\t{compact}\n")
                    written += 1
                    if delete_originals:
                        jf.unlink()

            if written:
                stats[str(bundle_path)] = written
                print(f"    {bundle_path.name}: {written} matches")

    return stats


def compress_old_bundles(data_dir: Path) -> list[str]:
    """Compress JSONL bundles for past months to .jsonl.zst."""
    current_month = datetime.now(tz=UTC).strftime("%Y-%m")
    compressed = []

    for platform_dir in sorted(data_dir.iterdir()):
        if not platform_dir.is_dir():
            continue
        for bundle in sorted(platform_dir.glob("*.jsonl")):
            month = bundle.stem  # e.g. "2024-03"
            if month >= current_month:
                continue  # don't compress active month
            zst_path = bundle.with_suffix(".jsonl.zst")
            if zst_path.exists():
                continue  # already compressed
            cctx = zstd.ZstdCompressor(level=19)
            raw = bundle.read_bytes()
            zst_path.write_bytes(cctx.compress(raw))
            orig_kb = len(raw) / 1024
            comp_kb = zst_path.stat().st_size / 1024
            ratio = orig_kb / comp_kb if comp_kb > 0 else 0
            print(
                f"  compressed {bundle.name} → {zst_path.name} ({orig_kb:.0f}KB → {comp_kb:.0f}KB, {ratio:.1f}x)"
            )
            bundle.unlink()
            compressed.append(str(zst_path))

    return compressed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Consolidate match data into JSONL+zstd bundles"
    )
    parser.add_argument(
        "--data-dir",
        default="lol-pipeline-fetcher/match-data",
        help="Path to match data directory (default: lol-pipeline-fetcher/match-data)",
    )
    parser.add_argument(
        "--delete-originals",
        action="store_true",
        help="Delete individual JSON files after consolidation",
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="Skip zstd compression of old months",
    )
    args = parser.parse_args()
    data_dir = Path(args.data_dir)

    if not data_dir.exists():
        print(f"Error: {data_dir} does not exist")
        return 1

    print(f"Consolidating match data in {data_dir}...")
    stats = consolidate(data_dir, delete_originals=args.delete_originals)

    if not stats:
        print("No new matches to consolidate.")
    else:
        total = sum(stats.values())
        print(f"Consolidated {total} matches into {len(stats)} bundles.")

    if not args.no_compress:
        print("Compressing old month bundles...")
        compressed = compress_old_bundles(data_dir)
        if compressed:
            print(f"Compressed {len(compressed)} bundles.")
        else:
            print("No bundles to compress.")

    if args.delete_originals:
        print("Original JSON files deleted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
