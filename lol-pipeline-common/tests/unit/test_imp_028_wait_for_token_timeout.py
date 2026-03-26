"""IMP-028: wait_for_token raises TimeoutError when deadline exceeded."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lol_pipeline.rate_limiter_client import wait_for_token


class TestWaitForTokenTimeout:
    async def test_expired_deadline_raises_timeout_error(self):
        """wait_for_token raises TimeoutError when deadline is exceeded."""
        # Token is never granted
        mock_response = httpx.Response(
            200, json={"granted": False, "retry_after_ms": 5000}
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch("lol_pipeline.rate_limiter_client._client", mock_client):
            with pytest.raises(TimeoutError, match="deadline exceeded"):
                # max_wait_s=0 means immediate deadline
                await wait_for_token("riot", "match", max_wait_s=0.0)

    async def test_granted_before_deadline_succeeds(self):
        """wait_for_token returns normally when token is granted before deadline."""
        mock_response = httpx.Response(200, json={"granted": True})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch("lol_pipeline.rate_limiter_client._client", mock_client):
            # Should not raise
            await wait_for_token("riot", "match", max_wait_s=10.0)
