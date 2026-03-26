"""BlobStore -- per-blob disk cache for raw source responses.

Disk layout:
    {BLOB_DATA_DIR}/
      {source_name}/          # e.g. "riot", "opgg"
        {platform}/           # e.g. "NA1", "KR"
          {match_id}.json     # one file per blob

Atomic writes use the tmpfile-fsync-os.replace() pattern.
Write-once: a blob is never overwritten once it exists.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from uuid import uuid4

log = logging.getLogger(__name__)

_PLATFORM_RE = re.compile(r"^[A-Z0-9]+$")

MAX_BLOB_SIZE_BYTES: int = 2 * 1024 * 1024  # 2 MB


class BlobStore:
    def __init__(self, data_dir: str) -> None:
        self._data_dir: Path | None = Path(data_dir).resolve() if data_dir else None

    def _validate_platform(self, platform: str) -> None:
        if not _PLATFORM_RE.match(platform):
            raise ValueError(f"invalid platform segment: {platform!r}")

    def _blob_path(self, source_name: str, match_id: str) -> Path:
        """Construct and validate the blob file path.

        Path traversal prevention:
        1. source_name validated at SourceEntry construction (^[a-z0-9_]+$).
        2. platform validated against ^[A-Z0-9]+$ here.
        3. path.is_relative_to(self._data_dir) as defense-in-depth backstop.
        """
        assert self._data_dir is not None  # noqa: S101
        platform = match_id.split("_")[0]
        self._validate_platform(platform)
        path = (self._data_dir / source_name / platform / f"{match_id}.json").resolve()
        if not path.is_relative_to(self._data_dir):
            raise ValueError(f"path escapes BLOB_DATA_DIR: {path}")
        return path

    async def exists(self, source_name: str, match_id: str) -> bool:
        """O(1) stat call."""
        if self._data_dir is None:
            return False
        path = self._blob_path(source_name, match_id)
        return await asyncio.to_thread(path.exists)

    async def read(self, source_name: str, match_id: str) -> dict[str, str] | None:
        """Read and parse a blob. Returns parsed dict or None."""
        if self._data_dir is None:
            return None
        path = self._blob_path(source_name, match_id)
        if not await asyncio.to_thread(path.exists):
            return None
        data = await asyncio.to_thread(path.read_bytes)
        return json.loads(data)  # type: ignore[no-any-return]

    async def write(self, source_name: str, match_id: str, data: bytes | str) -> None:
        """Atomic write: tmpfile -> fsync -> os.replace(). Write-once semantics.

        Accepts bytes (from FetchResponse.raw_blob) or str.
        If str, encodes as UTF-8 before writing.
        If the blob already exists, returns without overwriting.
        """
        if self._data_dir is None:
            return
        path = self._blob_path(source_name, match_id)
        if await asyncio.to_thread(path.exists):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".tmp_{match_id}_{os.getpid()}_{uuid4().hex}.json")
        try:
            await asyncio.to_thread(self._atomic_write, tmp, path, data)
        except FileExistsError:
            # Another coroutine (same PID, same match_id via XAUTOCLAIM) already
            # created the tmp file. Treat as successful no-op -- the file will be
            # written by the other coroutine. The final os.replace() is atomic.
            log.debug("blob tmp collision for %s/%s, treating as no-op", source_name, match_id)

    @staticmethod
    def _atomic_write(tmp: Path, final: Path, data: bytes | str) -> None:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(final))

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
            blob_path = (self._data_dir / name / platform / f"{match_id}.json").resolve()
            if not blob_path.is_relative_to(self._data_dir):
                raise ValueError(f"blob path escapes data dir: {blob_path}")
            raw = await asyncio.to_thread(self._read_if_exists, blob_path)
            if raw is None:
                continue
            try:
                return (name, json.loads(raw))
            except json.JSONDecodeError:
                log.warning("corrupt blob at %s, treating as cache miss", blob_path)
                continue
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
