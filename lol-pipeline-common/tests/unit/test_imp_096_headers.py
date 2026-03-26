"""IMP-096: parse_rate_limit_header exists in lol_pipeline.headers."""

from __future__ import annotations

from lol_pipeline.headers import parse_rate_limit_header
from lol_pipeline.riot_api import _parse_rate_limit_header as riot_parse


class TestHeadersModule:
    def test_function_exists(self):
        """parse_rate_limit_header is importable from lol_pipeline.headers."""
        assert callable(parse_rate_limit_header)

    def test_same_output_as_riot_api_version(self):
        """Common parse_rate_limit_header produces same output as riot_api version."""
        test_cases = [
            "20:1,100:120",
            "100:1,1000:120",
            "100:120,20:1",
            "",
            "bad",
            "20:1",
            "100:120",
        ]
        for header in test_cases:
            assert parse_rate_limit_header(header) == riot_parse(header), (
                f"Mismatch for header={header!r}: "
                f"common={parse_rate_limit_header(header)}, "
                f"riot_api={riot_parse(header)}"
            )

    def test_custom_windows(self):
        """Custom window sizes produce same result as riot_api version."""
        header = "500:10,30000:600"
        assert parse_rate_limit_header(
            header, short_window_s=10, long_window_s=600
        ) == riot_parse(header, short_window_s=10, long_window_s=600)

    def test_malformed_returns_none(self):
        """Malformed input returns None."""
        assert parse_rate_limit_header("garbage") is None

    def test_empty_returns_none(self):
        """Empty string returns None."""
        assert parse_rate_limit_header("") is None

    def test_valid_dev_key(self):
        """Standard dev key header parses correctly."""
        result = parse_rate_limit_header("20:1,100:120")
        assert result == (20, 100)
