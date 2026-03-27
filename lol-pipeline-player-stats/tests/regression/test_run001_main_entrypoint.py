"""RUN-001 regression: player-stats main() entrypoint must exist and be callable.

Bug: lol_player_stats/__main__.py does ``from lol_player_stats.main import main``
but main() was missing from main.py, causing an ImportError on startup.

This test catches the regression by verifying:
1. The import succeeds (no ImportError).
2. ``main`` is an async callable (coroutine function).
"""

from __future__ import annotations

import asyncio
import inspect


def test_main__importable__no_import_error():
    """RUN-001: ``from lol_player_stats.main import main`` must not raise ImportError."""
    from lol_player_stats.main import main  # noqa: F401


def test_main__is_coroutine_function():
    """RUN-001: main() must be an async def (coroutine function)."""
    from lol_player_stats.main import main

    assert inspect.iscoroutinefunction(main), (
        f"main is {type(main)}, expected a coroutine function (async def)"
    )
