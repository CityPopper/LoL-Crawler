"""IMP-049: _persist_rate_limits throttle sleep does not block indefinitely."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from lol_pipeline.riot_api import RiotClient


class TestNoThrottleDelay:
    async def test_get_returns_without_200ms_delay(self):
        """_get completes without a real wall-clock sleep when throttle=true.

        We mock asyncio.sleep so the test is deterministic (no wall-clock
        dependency) and verify the sleep is called with the expected small
        throttle value (0.2s) rather than a full rate-limit wait.
        """
        with respx.mock(assert_all_called=False) as mock:
            mock.get("https://americas.api.riotgames.com/test").mock(
                return_value=httpx.Response(
                    200,
                    json={"ok": True},
                    headers={
                        "X-App-Rate-Limit": "20:1,100:120",
                        "X-App-Rate-Limit-Count": "19:1,99:120",
                    },
                )
            )

            # Mock the rate-limiter POST to simulate throttle=true (which would
            # normally trigger a 200ms sleep)
            rl_response = httpx.Response(200, json={"throttle": True})
            mock_rl_client = AsyncMock(spec=httpx.AsyncClient)
            mock_rl_client.post = AsyncMock(return_value=rl_response)

            with (
                patch(
                    "lol_pipeline.riot_api._get_rl_client",
                    return_value=mock_rl_client,
                ),
                patch(
                    "lol_pipeline.riot_api.asyncio.sleep",
                    new_callable=AsyncMock,
                ) as mock_sleep,
            ):
                client = RiotClient(api_key="RGAPI-test")
                result = await client._get(
                    "https://americas.api.riotgames.com/test"
                )

                assert result == {"ok": True}
                # The throttle path calls asyncio.sleep(0.2) — not a full
                # rate-limit wait (which would be many seconds).
                mock_sleep.assert_awaited_once_with(0.2)

                await client.close()
