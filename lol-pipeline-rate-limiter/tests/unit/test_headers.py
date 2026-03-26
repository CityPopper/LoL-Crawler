"""Unit tests for rate-limiter header parsing error paths (IMP-079).

Tests that malformed, missing, or wrong-type rate-limit headers are handled
gracefully (return None) rather than raising unhandled exceptions.
"""

from __future__ import annotations

import pytest

from lol_rate_limiter._headers import parse_rate_limit_header


class TestHeaderParseHappyPath:
    """Valid headers parse correctly."""

    def test_valid_header__returns_tuple(self):
        result = parse_rate_limit_header("20:1,100:120")
        assert result == (20, 100)

    def test_valid_header__custom_windows(self):
        result = parse_rate_limit_header(
            "50:2,200:60", short_window_s=2, long_window_s=60
        )
        assert result == (50, 200)


class TestHeaderParseErrorPaths:
    """Malformed headers return None instead of raising."""

    def test_empty_string__returns_none(self):
        assert parse_rate_limit_header("") is None

    def test_completely_garbage__returns_none(self):
        """Non-numeric, no colons — triggers ValueError in split/int()."""
        assert parse_rate_limit_header("garbage-data") is None

    def test_missing_colon__returns_none(self):
        """Entry without colon separator — triggers ValueError in unpack."""
        assert parse_rate_limit_header("20,100") is None

    def test_non_numeric_count__returns_none(self):
        """Count portion is not an integer — triggers ValueError in int()."""
        assert parse_rate_limit_header("abc:1,100:120") is None

    def test_non_numeric_window__returns_none(self):
        """Window portion is not an integer — triggers ValueError in int()."""
        assert parse_rate_limit_header("20:abc,100:120") is None

    def test_missing_long_window__returns_none(self):
        """Only short window present — long is None, returns None."""
        assert parse_rate_limit_header("20:1") is None

    def test_missing_short_window__returns_none(self):
        """Only long window present — short is None, returns None."""
        assert parse_rate_limit_header("100:120") is None

    def test_extra_colons__returns_none(self):
        """Too many colons in an entry — triggers ValueError in unpack."""
        assert parse_rate_limit_header("20:1:extra,100:120") is None

    def test_float_values__returns_none(self):
        """Float values — triggers ValueError in int()."""
        assert parse_rate_limit_header("20.5:1,100:120") is None

    def test_negative_values__parses_as_int(self):
        """Negative values are valid ints; result depends on window match."""
        # -1 is a valid int, but window -1 won't match default 1 or 120
        assert parse_rate_limit_header("-20:-1,-100:-120") is None

    def test_whitespace_entries__returns_none(self):
        """Whitespace-only entries — triggers ValueError in split/int()."""
        assert parse_rate_limit_header("  ,  ") is None

    def test_none_like_string__returns_none(self):
        """String 'None' — triggers ValueError in int()."""
        assert parse_rate_limit_header("None:None") is None


class TestHeaderParseViaRoute:
    """Headers route handles malformed headers gracefully (returns updated=True, throttle=False)."""

    @pytest.mark.asyncio
    async def test_malformed_headers__route_returns_no_throttle(self):
        from unittest.mock import AsyncMock

        from httpx import ASGITransport, AsyncClient

        from lol_rate_limiter.config import Config
        from lol_rate_limiter.main import app

        app.state.cfg = Config()
        app.state.r = AsyncMock()
        app.state.r.set = AsyncMock(return_value=True)
        app.state.r.aclose = AsyncMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/headers",
                json={
                    "rate_limit": "not-valid-at-all",
                    "rate_limit_count": "also:broken:data",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] is True
        assert data["throttle"] is False

    @pytest.mark.asyncio
    async def test_empty_headers__route_returns_no_throttle(self):
        from unittest.mock import AsyncMock

        from httpx import ASGITransport, AsyncClient

        from lol_rate_limiter.config import Config
        from lol_rate_limiter.main import app

        app.state.cfg = Config()
        app.state.r = AsyncMock()
        app.state.r.aclose = AsyncMock()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/headers",
                json={"rate_limit": "", "rate_limit_count": ""},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] is True
        assert data["throttle"] is False
