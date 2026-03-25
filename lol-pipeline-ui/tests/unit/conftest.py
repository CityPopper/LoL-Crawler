"""Shared fixtures for UI unit tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_mem_caches() -> None:
    """Clear in-memory caches between tests to prevent cross-test leakage."""
    from lol_ui.ddragon import _mem_cache

    _mem_cache.clear()

    from lol_ui.routes.stats import _fragment_cache

    _fragment_cache.clear()
