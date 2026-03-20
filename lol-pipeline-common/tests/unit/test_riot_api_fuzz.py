"""Hypothesis property-based (fuzz) tests for riot_api._raise_for_status."""

from __future__ import annotations

import json

import httpx
from hypothesis import given, settings
from hypothesis import strategies as st

from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ServerError,
    _raise_for_status,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_status_codes = st.integers(min_value=100, max_value=599)

# HTTP headers must be ASCII-encodable. Restrict text strategy accordingly.
_ascii_text = st.text(
    alphabet=st.characters(codec="ascii", categories=("L", "N", "P", "S", "Z")),
    max_size=100,
)

_retry_after_values = st.one_of(
    st.just(None),  # no header
    st.integers(min_value=0, max_value=86400).map(str),  # integer seconds
    st.floats(min_value=0, max_value=86400, allow_nan=False, allow_infinity=False).map(str),
    st.just("0"),
    st.just("-1"),
    st.just(""),
    st.just("not-a-number"),
    st.just("Thu, 19 Mar 2026 12:00:00 GMT"),  # HTTP-date format
    st.just("inf"),
    st.just("-inf"),
    st.just("nan"),
    _ascii_text,  # arbitrary ASCII strings
)

_DUMMY_REQUEST = httpx.Request("GET", "https://example.com/test")


def _make_response(status_code: int, retry_after: str | None = None) -> httpx.Response:
    """Build a mock httpx.Response with the given status code and optional Retry-After.

    Attaches a dummy request so that resp.url does not raise RuntimeError.
    """
    headers = {}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    # 200 responses need a JSON body; others get text
    if status_code == 200:
        resp = httpx.Response(status_code, json={"ok": True}, headers=headers)
    else:
        resp = httpx.Response(status_code, text="response body", headers=headers)
    resp.request = _DUMMY_REQUEST
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRaiseForStatusFuzz:
    @given(status_code=_status_codes, retry_after=_retry_after_values)
    @settings(max_examples=500)
    def test_all_status_codes__only_expected_exceptions(
        self, status_code: int, retry_after: str | None
    ) -> None:
        """Every status code 100-599 either returns data or raises a RiotAPIError subclass."""
        resp = _make_response(status_code, retry_after)
        try:
            result = _raise_for_status(resp)
            # Only 200 should succeed
            assert status_code == 200, f"Non-200 status {status_code} did not raise"
            assert result == {"ok": True}
        except NotFoundError:
            assert status_code == 404
        except AuthError:
            assert status_code in (401, 403)
        except RateLimitError as exc:
            assert status_code == 429
            # retry_after_ms is either None or int
            assert exc.retry_after_ms is None or isinstance(exc.retry_after_ms, int)
        except ServerError:
            # ServerError is the catch-all for non-200 codes not handled above
            assert status_code not in (200, 404, 401, 403, 429)
        except json.JSONDecodeError:
            # 200 with malformed body — acceptable if our mock body is malformed
            assert status_code == 200

    @given(status_code=st.just(200))
    @settings(max_examples=10)
    def test_200__never_raises(self, status_code: int) -> None:
        """HTTP 200 always returns parsed JSON data."""
        resp = _make_response(status_code)
        result = _raise_for_status(resp)
        assert result == {"ok": True}

    @given(status_code=st.just(404))
    @settings(max_examples=10)
    def test_404__always_raises_not_found(self, status_code: int) -> None:
        """HTTP 404 always raises NotFoundError."""
        resp = _make_response(status_code)
        try:
            _raise_for_status(resp)
            raise AssertionError("404 did not raise")
        except NotFoundError:
            pass

    @given(status_code=st.sampled_from([401, 403]))
    @settings(max_examples=20)
    def test_401_403__always_raises_auth_error(self, status_code: int) -> None:
        """HTTP 401/403 always raises AuthError."""
        resp = _make_response(status_code)
        try:
            _raise_for_status(resp)
            raise AssertionError(f"{status_code} did not raise")
        except AuthError:
            pass

    @given(retry_after=_retry_after_values)
    @settings(max_examples=200)
    def test_429__always_raises_rate_limit__with_any_retry_after(
        self, retry_after: str | None
    ) -> None:
        """HTTP 429 always raises RateLimitError, regardless of Retry-After header value."""
        resp = _make_response(429, retry_after)
        try:
            _raise_for_status(resp)
            raise AssertionError("429 did not raise")
        except RateLimitError as exc:
            # retry_after_ms is always None or a valid int
            assert exc.retry_after_ms is None or isinstance(exc.retry_after_ms, int)
            # When retry_after_ms is set, it should be positive (includes +1000 jitter)
            if exc.retry_after_ms is not None:
                assert exc.retry_after_ms >= 1000, (
                    f"retry_after_ms={exc.retry_after_ms} below minimum jitter"
                )

    @given(
        status_code=st.integers(min_value=500, max_value=599),
        retry_after=_retry_after_values,
    )
    @settings(max_examples=100)
    def test_5xx__always_raises_server_error(
        self, status_code: int, retry_after: str | None
    ) -> None:
        """HTTP 5xx always raises ServerError."""
        resp = _make_response(status_code, retry_after)
        try:
            _raise_for_status(resp)
            raise AssertionError(f"{status_code} did not raise")
        except ServerError:
            pass

    @given(
        status_code=st.one_of(
            st.integers(min_value=100, max_value=199),
            st.integers(min_value=300, max_value=399),
            st.sampled_from([400, 402, 405, 406, 408, 409, 410, 413, 415, 422, 451]),
        )
    )
    @settings(max_examples=100)
    def test_other_codes__raise_server_error(self, status_code: int) -> None:
        """1xx, 3xx, and 4xx (except 401/403/404/429) all raise ServerError."""
        resp = _make_response(status_code)
        try:
            _raise_for_status(resp)
            raise AssertionError(f"{status_code} did not raise")
        except ServerError:
            pass

    @given(retry_after=_ascii_text.filter(lambda s: len(s) > 0))
    @settings(max_examples=200)
    def test_429__malformed_retry_after__never_unexpected_exception(self, retry_after: str) -> None:
        """Malformed Retry-After headers on 429 never cause unexpected exception types."""
        resp = _make_response(429, retry_after)
        try:
            _raise_for_status(resp)
            raise AssertionError("429 did not raise")
        except RateLimitError as exc:
            assert exc.retry_after_ms is None or isinstance(exc.retry_after_ms, int)
