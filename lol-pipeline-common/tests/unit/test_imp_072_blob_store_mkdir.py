"""IMP-072: BlobStore.write() must not call os.makedirs on the main thread.

The mkdir call must run inside _atomic_write (via asyncio.to_thread),
not synchronously on the event loop.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from lol_pipeline.sources.blob_store import BlobStore


class TestMkdirRunsInThread:
    """mkdir must happen inside _atomic_write, not on the event loop."""

    async def test_mkdir_not_called_on_main_thread(self, tmp_path) -> None:
        """Verify that os.makedirs (via Path.mkdir) only runs inside to_thread."""
        store = BlobStore(data_dir=str(tmp_path))

        main_thread_mkdirs = []
        original_atomic_write = BlobStore._atomic_write

        @staticmethod
        def tracking_atomic_write(tmp, final, data):
            # This runs in the thread — mkdir here is fine
            return original_atomic_write(tmp, final, data)

        # Patch Path.mkdir to detect calls on the main event loop
        original_mkdir = os.makedirs

        def tracking_makedirs(*args, **kwargs):
            import threading

            main_thread_mkdirs.append(threading.current_thread().name)
            return original_mkdir(*args, **kwargs)

        with patch("os.makedirs", side_effect=tracking_makedirs):
            await store.write("riot", "NA1_12345", b'{"test": true}')

        # All makedirs calls should be from a thread-pool thread, not MainThread
        for thread_name in main_thread_mkdirs:
            assert thread_name != "MainThread", (
                f"os.makedirs called on {thread_name} — should run in executor thread"
            )

    async def test_write_creates_directory_structure(self, tmp_path) -> None:
        """Verify the directory structure is still created correctly."""
        store = BlobStore(data_dir=str(tmp_path))
        await store.write("riot", "NA1_99999", b'{"data": true}')

        expected = tmp_path / "riot" / "NA1" / "NA1_99999.json"
        assert expected.exists()
        assert expected.read_bytes() == b'{"data": true}'

    async def test_atomic_write_creates_parent_dirs(self, tmp_path) -> None:
        """_atomic_write itself creates parent dirs before writing."""
        from pathlib import Path
        from uuid import uuid4

        final = tmp_path / "newdir" / "subdir" / "test.json"
        tmp = final.with_name(f".tmp_test_{os.getpid()}_{uuid4().hex}.json")

        BlobStore._atomic_write(tmp, final, b'{"ok": true}')

        assert final.exists()
        assert final.read_bytes() == b'{"ok": true}'
