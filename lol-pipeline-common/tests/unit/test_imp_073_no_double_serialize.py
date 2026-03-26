"""IMP-073: No double-serialization in the fetch pipeline.

RiotSource must pass raw HTTP response bytes as raw_blob instead of
re-serializing parsed data. The coordinator must use response.data
(pre-parsed) instead of json.loads(raw_blob) when available.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lol_pipeline.riot_api import RiotClient
from lol_pipeline.sources.base import MATCH, FetchContext, FetchResult
from lol_pipeline.sources.riot.source import RiotSource


@pytest.fixture
def match_data() -> dict:
    return {
        "metadata": {"matchId": "NA1_12345", "participants": ["p1"]},
        "info": {"gameDuration": 1800, "teams": []},
    }


@pytest.fixture
def match_bytes(match_data) -> bytes:
    """Raw bytes as they would come from the HTTP response."""
    return json.dumps(match_data).encode()


class TestRiotSourceNoReserialize:
    """RiotSource.fetch() must use raw HTTP bytes, not json.dumps(parsed)."""

    async def test_raw_blob_matches_original_response_bytes(
        self, match_data, match_bytes
    ) -> None:
        """raw_blob should be the exact bytes from the HTTP response."""
        mock_response = httpx.Response(
            200,
            content=match_bytes,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "https://example.com"),
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        riot = RiotClient("fake-key", client=mock_client)
        source = RiotSource(riot_client=riot)

        with patch("lol_pipeline.sources.riot.source.try_token", return_value=True):
            with patch.object(riot, "_persist_rate_limits", new_callable=AsyncMock):
                ctx = FetchContext(match_id="NA1_12345", puuid="abc", region="na1")
                response = await source.fetch(ctx, MATCH)

        assert response.result == FetchResult.SUCCESS
        assert response.raw_blob is not None
        # The raw_blob must be the original response bytes, not a re-serialization
        assert response.raw_blob == match_bytes

    async def test_data_field_is_parsed_dict(self, match_data, match_bytes) -> None:
        """response.data should be the pre-parsed dict."""
        mock_response = httpx.Response(
            200,
            content=match_bytes,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "https://example.com"),
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        riot = RiotClient("fake-key", client=mock_client)
        source = RiotSource(riot_client=riot)

        with patch("lol_pipeline.sources.riot.source.try_token", return_value=True):
            with patch.object(riot, "_persist_rate_limits", new_callable=AsyncMock):
                ctx = FetchContext(match_id="NA1_12345", puuid="abc", region="na1")
                response = await source.fetch(ctx, MATCH)

        assert response.data == match_data


class TestRiotClientGetMatchWithRaw:
    """RiotClient.get_match_with_raw returns both parsed and raw."""

    async def test_returns_parsed_and_raw(self, match_data, match_bytes) -> None:
        mock_response = httpx.Response(
            200,
            content=match_bytes,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "https://example.com"),
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        riot = RiotClient("fake-key", client=mock_client)

        with patch.object(riot, "_persist_rate_limits", new_callable=AsyncMock):
            data, raw = await riot.get_match_with_raw("NA1_12345", "na1")

        assert data == match_data
        assert raw == match_bytes

    async def test_raw_bytes_identical_to_response_content(self) -> None:
        """The raw bytes must be exactly resp.content, not a re-encode."""
        # Use non-standard but valid JSON formatting to prove no re-serialization
        original_bytes = b'{"metadata":  {"matchId":"X"},  "info":{"gameDuration":0}}'
        mock_response = httpx.Response(
            200,
            content=original_bytes,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "https://example.com"),
        )

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.get = AsyncMock(return_value=mock_response)

        riot = RiotClient("fake-key", client=mock_client)

        with patch.object(riot, "_persist_rate_limits", new_callable=AsyncMock):
            _data, raw = await riot.get_match_with_raw("X", "na1")

        # Extra spaces in original_bytes prove we got the original, not re-serialized
        assert raw == original_bytes
        assert b"  " in raw  # extra spaces preserved
