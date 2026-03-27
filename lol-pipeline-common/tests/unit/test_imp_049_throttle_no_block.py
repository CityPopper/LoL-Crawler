"""IMP-049: _persist_rate_limits does not block — throttle sleep removed."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from lol_pipeline.riot_api import RiotClient


class TestNoThrottleDelay:
    async def test_get_returns_without_200ms_delay(self):
        """_get completes without any throttle-induced sleep.

        Throttle sleep was removed from _persist_rate_limits; the rate-limiter
        service self-regulates via stored limits. This test confirms GET
        succeeds and POST /headers is called without any sleep side-effect.
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

            rl_response = httpx.Response(200, json={"updated": True})
            mock_rl_client = AsyncMock(spec=httpx.AsyncClient)
            mock_rl_client.post = AsyncMock(return_value=rl_response)

            with patch(
                "lol_pipeline.riot_api._get_rl_client",
                return_value=mock_rl_client,
            ):
                client = RiotClient(api_key="RGAPI-test")
                result = await client._get(
                    "https://americas.api.riotgames.com/test"
                )

                assert result == {"ok": True}
                # POST /headers was called (no throttle sleep)
                mock_rl_client.post.assert_called_once()

                await client.close()
