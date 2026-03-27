"""BlobStore -- per-blob disk cache for raw source responses.

Disk layout:
    {BLOB_DATA_DIR}/
      {source_name}/          # e.g. "riot", "opgg"
        {platform}/           # e.g. "NA1", "KR"
          {match_id}.json.zst # one file per blob (zstd-compressed)

Atomic writes use the tmpfile-fsync-os.replace() pattern.
Write-once: a blob is never overwritten once it exists.
Reads fall back to uncompressed .json for pre-compression blobs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from uuid import uuid4

import zstandard

log = logging.getLogger(__name__)

_PLATFORM_RE = re.compile(r"^[A-Z0-9]+$")

MAX_BLOB_SIZE_BYTES: int = 2 * 1024 * 1024  # 2 MB

_ZST_COMPRESSOR = zstandard.ZstdCompressor(level=3)
_ZST_DECOMPRESSOR = zstandard.ZstdDecompressor()


class BlobStore:
    def __init__(self, data_dir: str) -> None:
        self._data_dir: Path | None = Path(data_dir).resolve() if data_dir else None

    def _validate_platform(self, platform: str) -> None:
        if not _PLATFORM_RE.match(platform):
            raise ValueError(f"invalid platform segment: {platform!r}")

    def _blob_path(self, source_name: str, match_id: str) -> Path:
        """Construct and validate the blob file path (compressed .json.zst).

        Path traversal prevention:
        1. source_name validated at SourceEntry construction (^[a-z0-9_]+$).
        2. platform validated against ^[A-Z0-9]+$ here.
        3. path.is_relative_to(self._data_dir) as defense-in-depth backstop.
        """
        assert self._data_dir is not None  # noqa: S101
        platform = match_id.split("_")[0]
        self._validate_platform(platform)
        path = (self._data_dir / source_name / platform / f"{match_id}.json.zst").resolve()
        if not path.is_relative_to(self._data_dir):
            raise ValueError(f"path escapes BLOB_DATA_DIR: {path}")
        return path

    def _legacy_blob_path(self, source_name: str, match_id: str) -> Path:
        """Return the legacy uncompressed .json path for fallback reads."""
        assert self._data_dir is not None  # noqa: S101
        platform = match_id.split("_")[0]
        return (self._data_dir / source_name / platform / f"{match_id}.json").resolve()

    async def exists(self, source_name: str, match_id: str) -> bool:
        """O(1) stat call. Checks compressed (.json.zst) then legacy (.json)."""
        if self._data_dir is None:
            return False
        path = self._blob_path(source_name, match_id)
        if await asyncio.to_thread(path.exists):
            return True
        legacy = self._legacy_blob_path(source_name, match_id)
        return await asyncio.to_thread(legacy.exists)

    async def read(self, source_name: str, match_id: str) -> dict[str, str] | None:
        """Read and parse a blob. Tries .json.zst first, falls back to .json."""
        if self._data_dir is None:
            return None
        path = self._blob_path(source_name, match_id)
        raw = await asyncio.to_thread(self._read_if_exists, path)
        if raw is not None:
            data = _ZST_DECOMPRESSOR.decompress(raw)
            return json.loads(data)  # type: ignore[no-any-return]
        legacy = self._legacy_blob_path(source_name, match_id)
        raw = await asyncio.to_thread(self._read_if_exists, legacy)
        if raw is not None:
            return json.loads(raw)  # type: ignore[no-any-return]
        return None

    async def write(self, source_name: str, match_id: str, data: bytes | str) -> None:
        """Atomic write: tmpfile -> fsync -> os.replace(). Write-once semantics.

        Accepts bytes (from FetchResponse.raw_blob) or str.
        If str, encodes as UTF-8 before writing.
        Data is zstd-compressed before writing to disk.
        If the blob already exists (.json.zst or legacy .json), returns without overwriting.
        """
        if self._data_dir is None:
            return
        path = self._blob_path(source_name, match_id)
        if await asyncio.to_thread(path.exists):
            return
        legacy = self._legacy_blob_path(source_name, match_id)
        if await asyncio.to_thread(legacy.exists):
            return
        tmp = path.with_name(f".tmp_{match_id}_{os.getpid()}_{uuid4().hex}.json.zst")
        try:
            await asyncio.to_thread(self._atomic_write, tmp, path, data)
        except FileExistsError:
            # Another coroutine (same PID, same match_id via XAUTOCLAIM) already
            # created the tmp file. Treat as successful no-op -- the file will be
            # written by the other coroutine. The final os.replace() is atomic.
            log.debug("blob tmp collision for %s/%s, treating as no-op", source_name, match_id)

    @staticmethod
    def _atomic_write(tmp: Path, final: Path, data: bytes | str) -> None:
        final.parent.mkdir(parents=True, exist_ok=True)
        raw = data.encode("utf-8") if isinstance(data, str) else data
        compressed = _ZST_COMPRESSOR.compress(raw)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, compressed)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(final))

    async def delete(self, source_name: str, match_id: str) -> None:
        """Delete a cached blob. Removes both .json.zst and legacy .json if present."""
        if self._data_dir is None:
            return
        path = self._blob_path(source_name, match_id)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        legacy = self._legacy_blob_path(source_name, match_id)
        await asyncio.to_thread(legacy.unlink, missing_ok=True)

    async def find_any(
        self, match_id: str, source_names: list[str]
    ) -> tuple[str, dict[str, str]] | None:
        """Check source subdirectories for a cached blob, in registry priority order.

        source_names must be the registry's priority-ordered list (highest-priority
        first). This ensures the highest-fidelity blob is always preferred when
        multiple sources have cached data for the same match_id.

        Each name in source_names comes from the trusted SourceEntry registry
        (validated at construction against ^[a-z0-9_]+$) -- no re-validation needed.

        Returns (source_name, parsed_blob_dict) or None.
        Corrupt blobs (JSONDecodeError) are treated as cache misses.
        """
        if self._data_dir is None or not self._data_dir.exists():
            return None
        platform = match_id.split("_")[0]
        try:
            self._validate_platform(platform)
        except ValueError:
            return None
        for name in source_names:
            for ext in (".json.zst", ".json"):
                blob_path = (self._data_dir / name / platform / f"{match_id}{ext}").resolve()
                if not blob_path.is_relative_to(self._data_dir):
                    raise ValueError(f"blob path escapes data dir: {blob_path}")
                raw = await asyncio.to_thread(self._read_if_exists, blob_path)
                if raw is None:
                    continue
                if len(raw) > MAX_BLOB_SIZE_BYTES:
                    log.warning(
                        "oversized blob at %s (%d bytes), treating as cache miss",
                        blob_path,
                        len(raw),
                    )
                    break  # skip this source entirely
                try:
                    data = _ZST_DECOMPRESSOR.decompress(raw) if ext == ".json.zst" else raw
                    return (name, json.loads(data))
                except json.JSONDecodeError:
                    log.warning("corrupt blob at %s, treating as cache miss", blob_path)
                    break  # skip this source entirely
        return None

    @staticmethod
    def _read_if_exists(path: Path) -> bytes | None:
        """Read file bytes if the file exists, otherwise return None.

        Runs in a single thread dispatch to avoid two separate
        ``asyncio.to_thread()`` calls for exists + read.
        """
        if not path.exists():
            return None
        return path.read_bytes()
