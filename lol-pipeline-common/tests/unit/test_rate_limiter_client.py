"""Unit tests for lol_pipeline.rate_limiter_client — HTTP-based rate limiter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lol_pipeline.rate_limiter_client import try_token, wait_for_token


class TestWaitForTokenGranted:
    """wait_for_token returns immediately when the service grants a token."""

    @pytest.mark.asyncio
    async def test_wait_for_token_granted__returns_immediately(self) -> None:
        mock_response = httpx.Response(200, json={"granted": True})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch("lol_pipeline.rate_limiter_client._client", mock_client):
            await wait_for_token("riot", "match")

        mock_client.post.assert_called_once_with(
            "/token/acquire",
            json={"source": "riot", "endpoint": "match"},
        )


class TestTryTokenDenied:
    """try_token returns False when the service denies a token."""

    @pytest.mark.asyncio
    async def test_try_token_denied__returns_false(self) -> None:
        mock_response = httpx.Response(200, json={"granted": False, "retry_after_ms": 500})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch("lol_pipeline.rate_limiter_client._client", mock_client):
            result = await try_token("riot", "match")

        assert result is False


class TestFailOpenOnConnectionError:
    """wait_for_token fails open when the service is unreachable."""

    @pytest.mark.asyncio
    async def test_fail_open_on_connection_error(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.is_closed = False

        with patch("lol_pipeline.rate_limiter_client._client", mock_client):
            # Should NOT raise — fails open
            await wait_for_token("riot", "match")


class TestUnknownSourceFailsOpen:
    """wait_for_token fails open when the service returns 404 (unknown source)."""

    @pytest.mark.asyncio
    async def test_unknown_source_fails_open(self) -> None:
        mock_response = httpx.Response(404, json={"error": "unknown source"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch("lol_pipeline.rate_limiter_client._client", mock_client):
            # Should NOT raise — fails open
            await wait_for_token("unknown_source", "endpoint")


class TestTryTokenFailsOpen:
    """try_token returns True (fail open) when the service is unreachable."""

    @pytest.mark.asyncio
    async def test_try_token_fail_open_on_connection_error(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.is_closed = False

        with patch("lol_pipeline.rate_limiter_client._client", mock_client):
            result = await try_token("riot", "match")

        assert result is True

    @pytest.mark.asyncio
    async def test_try_token_unknown_source_fails_open(self) -> None:
        mock_response = httpx.Response(404, json={"error": "unknown source"})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch("lol_pipeline.rate_limiter_client._client", mock_client):
            result = await try_token("unknown_source", "endpoint")

        assert result is True
