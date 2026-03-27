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
            json={"domain": "riot", "endpoint": "match", "is_ui": False, "priority": 0},
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
            with patch("lol_pipeline.rate_limiter_client._RATE_LIMITER_CONNECT_RETRIES", 1):
                # Should NOT raise — fails open after 1 retry
                await wait_for_token("riot", "match")


class TestWaitForTokenRetriesBeforeFailingOpen:
    """wait_for_token retries N times before failing open (RL-PROXY-1c)."""

    @pytest.mark.asyncio
    async def test_wait_for_token__retries_before_failing_open(self) -> None:
        """Service unreachable 3 times then succeeds on 4th; assert all 4 calls made."""
        granted_response = httpx.Response(200, json={"granted": True})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(
            side_effect=[
                httpx.ConnectError("refused"),
                httpx.ConnectError("refused"),
                httpx.ConnectError("refused"),
                granted_response,
            ]
        )
        mock_client.is_closed = False

        sleep_mock = AsyncMock()
        with (
            patch("lol_pipeline.rate_limiter_client._client", mock_client),
            patch("lol_pipeline.rate_limiter_client._RATE_LIMITER_CONNECT_RETRIES", 4),
            patch("lol_pipeline.rate_limiter_client.asyncio.sleep", sleep_mock),
        ):
            await wait_for_token("riot", "match")

        # All 4 calls should have been made (3 failures + 1 success)
        assert mock_client.post.call_count == 4
        # 3 retry sleeps of 0.5s each
        assert sleep_mock.call_count == 3

    @pytest.mark.asyncio
    async def test_wait_for_token__fails_open_after_retries_exhausted(self) -> None:
        """All retries fail => fails open (returns without raising)."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.is_closed = False

        sleep_mock = AsyncMock()
        with (
            patch("lol_pipeline.rate_limiter_client._client", mock_client),
            patch("lol_pipeline.rate_limiter_client._RATE_LIMITER_CONNECT_RETRIES", 3),
            patch("lol_pipeline.rate_limiter_client.asyncio.sleep", sleep_mock),
        ):
            # Should NOT raise — fails open after 3 retries
            await wait_for_token("riot", "match")

        # 3 retries: attempts 1, 2, 3 (fails open on 3rd)
        assert mock_client.post.call_count == 3
        # 2 retry sleeps (after attempt 1 and 2; attempt 3 fails open immediately)
        assert sleep_mock.call_count == 2


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
