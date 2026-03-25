"""RawStore abstraction — write-once key/value store for raw match JSON blobs."""

from __future__ import annotations

import asyncio
import io
import logging
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import redis.asyncio as aioredis
import zstandard as zstd

_KEY_PREFIX = "raw:match:"
# Configurable TTL for raw blobs.  Default 24h.
# Env: RAW_STORE_TTL_SECONDS (matches Config.raw_store_ttl_seconds).
_TTL_SECONDS: int = int(os.environ.get("RAW_STORE_TTL_SECONDS", "86400"))
_log = logging.getLogger("raw_store")


def _write_to_disk(path: Path, data: str) -> None:
    """Synchronous disk write helper — called via asyncio.to_thread."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(data)


class RawStore:
    """Write-through store for raw Riot API match JSON.

    Redis is the hot cache; when data_dir is set each match is also appended to
    a JSONL bundle at ``{data_dir}/{platform}/{YYYY-MM}.jsonl``.

    Compressed ``.jsonl.zst`` bundles (produced by the migration script) are
    also readable as a fallback.

    On a Redis miss, the JSONL bundle is scanned and Redis is repopulated
    automatically.  This ensures match data survives Redis resets.

    All writes are no-ops if the key/file already exists (write-once semantics).
    """

    def __init__(
        self, r: aioredis.Redis, data_dir: str = "", key_prefix: str = "raw:match:"
    ) -> None:
        self._r = r
        self._data_dir: Path | None = Path(data_dir) if data_dir else None
        self._key_prefix = key_prefix

    def _platform_dir(self, match_id: str) -> Path | None:
        """Return the platform directory for match_id, or None if disk storage is disabled."""
        if self._data_dir is None:
            return None
        platform = match_id.split("_")[0] if "_" in match_id else "UNKNOWN"
        return self._data_dir / platform

    def _bundle_path(self, match_id: str) -> Path | None:
        """Return the active (uncompressed) JSONL bundle path for the current month."""
        pdir = self._platform_dir(match_id)
        if pdir is None:
            return None
        month = datetime.now(tz=UTC).strftime("%Y-%m")
        return pdir / f"{month}.jsonl"

    def _legacy_path(self, match_id: str) -> Path | None:
        """Return the legacy individual JSON file path (for backward compat reads)."""
        pdir = self._platform_dir(match_id)
        if pdir is None:
            return None
        return pdir / f"{match_id}.json"

    def _search_bundles(self, match_id: str) -> str | None:
        """Scan all JSONL bundles (plain + compressed) for match_id. Returns data or None."""
        pdir = self._platform_dir(match_id)
        if pdir is None or not pdir.exists():
            return None

        # Search uncompressed bundles
        for bundle in pdir.glob("*.jsonl"):
            result = self._search_bundle_file(bundle, match_id)
            if result is not None:
                return result

        # Search compressed bundles
        for bundle in pdir.glob("*.jsonl.zst"):
            result = self._search_compressed_bundle(bundle, match_id)
            if result is not None:
                return result

        # Legacy: check individual JSON file
        legacy = self._legacy_path(match_id)
        if legacy is not None and legacy.exists():
            return legacy.read_text()

        return None

    @staticmethod
    def _find_in_lines(lines: Iterable[str], match_id: str) -> str | None:
        """Scan lines for a tab-prefixed match_id entry, return data or None."""
        prefix = match_id + "\t"
        for line in lines:
            line = line.rstrip("\n")
            if line.startswith(prefix):
                return line[len(prefix) :]
        return None

    @staticmethod
    def _search_bundle_file(path: Path, match_id: str) -> str | None:
        """Search an uncompressed JSONL bundle for match_id (line-by-line streaming)."""
        with path.open(encoding="utf-8") as f:
            return RawStore._find_in_lines(f, match_id)

    @staticmethod
    def _search_compressed_bundle(path: Path, match_id: str) -> str | None:
        """Search a zstd-compressed JSONL bundle for match_id (streaming)."""
        dctx = zstd.ZstdDecompressor()
        with path.open("rb") as fh, dctx.stream_reader(fh) as reader:
            text = io.TextIOWrapper(reader, encoding="utf-8")
            return RawStore._find_in_lines(text, match_id)

    def _exists_in_current_bundle(self, match_id: str) -> bool:
        """Check if match_id exists in the current month's JSONL bundle only.

        Used by set() for dedup on write.  Scanning only the active bundle is
        O(current-month lines) instead of O(all-time lines).  Historical
        duplicates are already blocked by the fetcher's ``match:status:fetched``
        Redis set, so a full-scan is unnecessary on the write path.
        """
        bp = self._bundle_path(match_id)
        if bp is None or not bp.exists():
            return False
        return self._search_bundle_file(bp, match_id) is not None

    def _exists_in_bundles(self, match_id: str) -> bool:
        """Check if match_id exists in any JSONL bundle."""
        return self._search_bundles(match_id) is not None

    async def exists(self, match_id: str) -> bool:
        """Return True if the raw blob is stored in Redis or on disk."""
        if bool(await self._r.exists(f"{self._key_prefix}{match_id}")):
            return True
        return await asyncio.to_thread(self._exists_in_bundles, match_id)

    async def get(self, match_id: str) -> str | None:
        """Return raw JSON string; tries Redis first, then disk (repopulates Redis on hit)."""
        data: str | None = await self._r.get(f"{self._key_prefix}{match_id}")
        if data is not None:
            return data
        data = await asyncio.to_thread(self._search_bundles, match_id)
        if data is not None:
            # Write-back: repopulate Redis so subsequent reads are fast
            await self._r.set(f"{self._key_prefix}{match_id}", data, nx=True, ex=_TTL_SECONDS)
            return data
        return None

    async def set(self, match_id: str, data: str) -> None:
        """Write raw JSON blob to Redis and disk. No-op if already stored."""
        was_set = await self._r.set(f"{self._key_prefix}{match_id}", data, nx=True, ex=_TTL_SECONDS)
        bp = self._bundle_path(match_id)
        if bp is None:
            return
        # Redis SET NX is the atomic coordinator: only the winner writes to disk.
        # Also check the current bundle for the Redis-restart case (key gone,
        # disk has it).  Only the active month's file is scanned — historical
        # duplicates are already blocked by match:status:fetched in Redis.
        if not was_set or await asyncio.to_thread(self._exists_in_current_bundle, match_id):
            return
        try:
            await asyncio.to_thread(_write_to_disk, bp, f"{match_id}\t{data}\n")
        except OSError as exc:
            # Remove Redis key so next attempt can retry both Redis + disk
            await self._r.delete(f"{self._key_prefix}{match_id}")
            _log.warning(
                "disk write failed — removed Redis key for retry",
                extra={"match_id": match_id, "error": str(exc)},
            )
