# Shared test fixtures for lol-pipeline-crawler tests
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_wait_for_token():
    """Auto-mock wait_for_token so tests don't hit the HTTP rate-limiter service.

    Individual tests can override by patching lol_crawler.main.wait_for_token
    with their own side_effect inside a ``with patch(...)`` block.
    """
    with patch("lol_crawler.main.wait_for_token", new_callable=AsyncMock):
        yield
