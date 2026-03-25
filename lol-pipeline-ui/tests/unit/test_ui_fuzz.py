"""Hypothesis property-based (fuzz) tests for UI helper functions."""

from __future__ import annotations

import html as html_mod
import json

from hypothesis import given, settings
from hypothesis import strategies as st

from lol_ui.constants import _BADGE_VARIANTS
from lol_ui.log_helpers import _parse_log_line
from lol_ui.rendering import _badge
from lol_ui.stats_helpers import _format_stat_value

# ---------------------------------------------------------------------------
# _format_stat_value fuzz tests
# ---------------------------------------------------------------------------

# Stat keys that trigger special formatting
_win_rate_key = st.just("win_rate")
_avg_keys = st.sampled_from(["avg_kills", "avg_deaths", "avg_assists", "avg_cs"])
_kda_key = st.just("kda")
_other_keys = st.text(min_size=0, max_size=50).filter(
    lambda k: k not in {"win_rate", "kda"} and not k.startswith("avg_")
)
_all_stat_keys = st.one_of(_win_rate_key, _avg_keys, _kda_key, _other_keys)

_stat_values = st.one_of(
    st.text(max_size=200),
    st.floats(allow_nan=True, allow_infinity=True).map(str),
    st.integers(min_value=-(2**53), max_value=2**53).map(str),
    st.just(""),
    st.just("nan"),
    st.just("inf"),
    st.just("-inf"),
    st.just("0"),
    st.just("-0.0"),
    st.just("999999999999"),
)


class TestFormatStatValueFuzz:
    @given(key=_all_stat_keys, value=_stat_values)
    @settings(max_examples=500)
    def test_format_stat_value__never_raises(self, key: str, value: str) -> None:
        """_format_stat_value never raises an unhandled exception for any input."""
        result = _format_stat_value(key, value)
        assert isinstance(result, str)

    @given(value=_stat_values)
    @settings(max_examples=100)
    def test_format_stat_value__win_rate__returns_string(self, value: str) -> None:
        """win_rate always returns a string; non-empty when input is non-empty."""
        result = _format_stat_value("win_rate", value)
        assert isinstance(result, str)
        # Empty input returns empty output (fallback to raw value)
        if value:
            assert len(result) > 0

    @given(key=_avg_keys, value=_stat_values)
    @settings(max_examples=100)
    def test_format_stat_value__avg_keys__returns_string(self, key: str, value: str) -> None:
        """avg_* keys always return a string; non-empty when input is non-empty."""
        result = _format_stat_value(key, value)
        assert isinstance(result, str)
        if value:
            assert len(result) > 0

    @given(value=_stat_values)
    @settings(max_examples=100)
    def test_format_stat_value__kda__returns_string(self, value: str) -> None:
        """kda key always returns a string; non-empty when input is non-empty."""
        result = _format_stat_value("kda", value)
        assert isinstance(result, str)
        if value:
            assert len(result) > 0

    @given(value=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100)
    def test_format_stat_value__win_rate_valid_float__has_percent(self, value: float) -> None:
        """Valid win_rate floats produce a string ending with %."""
        result = _format_stat_value("win_rate", str(value))
        assert result.endswith("%")

    @given(value=st.floats(allow_nan=False, allow_infinity=False))
    @settings(max_examples=100)
    def test_format_stat_value__kda_valid_float__has_decimal(self, value: float) -> None:
        """Valid kda floats produce a formatted decimal string."""
        result = _format_stat_value("kda", str(value))
        assert "." in result

    @given(key=_other_keys, value=_stat_values)
    @settings(max_examples=100)
    def test_format_stat_value__other_keys__returns_raw_value(self, key: str, value: str) -> None:
        """Keys other than win_rate, avg_*, and kda return the raw value unchanged."""
        result = _format_stat_value(key, value)
        assert result == value

    @given(
        value=st.sampled_from(["nan", "inf", "-inf"]),
        key=st.sampled_from(["win_rate", "kda", "avg_kills"]),
    )
    @settings(max_examples=30)
    def test_format_stat_value__non_finite_floats__returns_na(self, value: str, key: str) -> None:
        """Non-finite float strings (nan, inf, -inf) return 'N/A' for numeric keys."""
        result = _format_stat_value(key, value)
        assert result == "N/A"


# ---------------------------------------------------------------------------
# _badge fuzz tests
# ---------------------------------------------------------------------------

_valid_variants = st.sampled_from(sorted(_BADGE_VARIANTS))
_invalid_variants = st.text(min_size=0, max_size=50).filter(lambda v: v not in _BADGE_VARIANTS)

_badge_text = st.one_of(
    st.text(max_size=200),
    st.just(""),
    st.just("<script>alert('xss')</script>"),
    st.just('"><img src=x onerror=alert(1)>'),
    st.just("&amp; &lt; &gt; &quot;"),
    st.just("<b>bold</b>"),
)


class TestBadgeFuzz:
    @given(variant=_valid_variants, text=_badge_text)
    @settings(max_examples=200)
    def test_badge__valid_variant__returns_non_empty_html(self, variant: str, text: str) -> None:
        """Valid variants always produce non-empty HTML strings."""
        result = _badge(variant, text)
        assert isinstance(result, str)
        assert len(result) > 0
        assert result.startswith("<span")
        assert result.endswith("</span>")
        # The variant must appear in the class attribute
        assert f"badge--{variant}" in result

    @given(variant=_invalid_variants)
    @settings(max_examples=100)
    def test_badge__invalid_variant__raises_value_error(self, variant: str) -> None:
        """Invalid variants always raise ValueError."""
        try:
            _badge(variant, "test")
            raise AssertionError(f"Should have raised ValueError for variant={variant!r}")
        except ValueError:
            pass

    @given(variant=_valid_variants, text=_badge_text)
    @settings(max_examples=200)
    def test_badge__no_xss_injection(self, variant: str, text: str) -> None:
        """User-supplied text is HTML-escaped — no raw angle brackets in output."""
        result = _badge(variant, text)
        # The text portion should be html-escaped
        escaped_text = html_mod.escape(text)
        assert escaped_text in result
        # Extract text content between badge span tags — should not contain raw
        # unescaped angle brackets from the input
        if "<" in text:
            # The raw "<" from text should NOT appear unescaped in the badge output
            # (only as &lt;)
            badge_inner = result.split(">", 2)[-1].rsplit("<", 1)[0]
            assert "<" not in badge_inner or badge_inner == html_mod.escape(text)

    @given(text=st.text(alphabet=st.characters(categories=("L", "N", "P", "S", "Z")), max_size=100))
    @settings(max_examples=50)
    def test_badge__unicode_text__no_crash(self, text: str) -> None:
        """Unicode text in badges does not crash."""
        result = _badge("success", text)
        assert isinstance(result, str)
        assert html_mod.escape(text) in result


# ---------------------------------------------------------------------------
# _parse_log_line fuzz tests
# ---------------------------------------------------------------------------

_log_line_inputs = st.one_of(
    st.text(max_size=500),
    st.just(""),
    st.just("{}"),
    st.just('{"timestamp": "2024-01-01T00:00:00", "level": "INFO", "message": "ok"}'),
    st.just('{"level": "ERROR", "message": "fail", "_internal": "hidden"}'),
    st.just("<script>alert('xss')</script>"),
    st.just('{"nested": {"a": {"b": 1}}, "message": "deep"}'),
    st.just("not json at all"),
    st.just('{"timestamp": null, "level": null, "logger": null, "message": null}'),
    st.binary(max_size=100).map(lambda b: b.decode("utf-8", errors="replace")),
)

_json_log_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**32), max_value=2**32),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=100),
)

_json_log_values = st.recursive(
    _json_log_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(max_size=20), children, max_size=3),
    ),
    max_leaves=10,
)


@st.composite
def _structured_log_line(draw: st.DrawFn) -> str:
    """Generate a JSON log line with optional standard fields."""
    d: dict[str, object] = {}
    if draw(st.booleans()):
        d["timestamp"] = draw(st.one_of(st.text(max_size=30), st.none()))
    if draw(st.booleans()):
        d["level"] = draw(st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]))
    if draw(st.booleans()):
        d["logger"] = draw(st.text(max_size=50))
    if draw(st.booleans()):
        d["message"] = draw(st.text(max_size=200))
    # Extra fields — some with _ prefix (should be excluded from extra)
    extra_keys = draw(st.lists(st.text(min_size=1, max_size=20), min_size=0, max_size=5))
    for k in extra_keys:
        d[k] = draw(_json_log_values)
    return json.dumps(d)


class TestParseLogLineFuzz:
    @given(line=_log_line_inputs)
    @settings(max_examples=500)
    def test_parse_log_line__never_raises(self, line: str) -> None:
        """_parse_log_line never raises an unhandled exception for any string input."""
        result = _parse_log_line(line)
        assert isinstance(result, tuple)
        assert len(result) == 5
        ts, level, logger, msg, extra = result
        assert isinstance(ts, str)
        assert isinstance(level, str)
        assert isinstance(logger, str)
        assert isinstance(msg, str)
        assert isinstance(extra, str)

    @given(line=_structured_log_line())
    @settings(max_examples=300)
    def test_parse_log_line__valid_json__returns_5_strings(self, line: str) -> None:
        """Valid JSON log lines always return a 5-tuple of strings."""
        ts, level, logger, msg, extra = _parse_log_line(line)
        assert isinstance(ts, str)
        assert isinstance(level, str)
        assert isinstance(logger, str)
        assert isinstance(msg, str)
        assert isinstance(extra, str)

    @given(line=st.just(""))
    @settings(max_examples=5)
    def test_parse_log_line__empty_string__fallback(self, line: str) -> None:
        """Empty string uses fallback: level='INFO', others empty."""
        ts, level, logger, msg, extra = _parse_log_line(line)
        assert level == "INFO"
        assert ts == ""
        assert logger == ""
        assert extra == ""

    @given(line=st.text(max_size=200).filter(lambda s: not s.strip().startswith("{")))
    @settings(max_examples=100)
    def test_parse_log_line__non_json__fallback_returns_line_as_message(self, line: str) -> None:
        """Non-JSON strings: level='INFO', message=original line, others empty."""
        ts, level, logger, msg, extra = _parse_log_line(line)
        assert level == "INFO"
        assert ts == ""
        assert logger == ""
        assert extra == ""
        assert msg == line

    @given(
        ts_value=st.text(max_size=30),
        level_value=st.sampled_from(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
        logger_value=st.text(max_size=30),
        msg_value=st.text(max_size=200),
    )
    @settings(max_examples=200)
    def test_parse_log_line__known_fields__extracted_correctly(
        self,
        ts_value: str,
        level_value: str,
        logger_value: str,
        msg_value: str,
    ) -> None:
        """Known fields are extracted; timestamp truncated to 19 chars with T→space."""
        line = json.dumps(
            {
                "timestamp": ts_value,
                "level": level_value,
                "logger": logger_value,
                "message": msg_value,
            }
        )
        ts, level, logger, msg, extra = _parse_log_line(line)
        assert level == level_value
        assert logger == logger_value
        assert msg == msg_value
        assert ts == ts_value[:19].replace("T", " ")
        assert extra == ""  # no extra keys

    @given(line=st.just('{"_hidden": "x", "_internal": 1, "message": "hello"}'))
    @settings(max_examples=5)
    def test_parse_log_line__underscore_keys__excluded_from_extra(self, line: str) -> None:
        """Keys starting with _ are excluded from the extra field."""
        _, _, _, _, extra = _parse_log_line(line)
        assert "_hidden" not in extra
        assert "_internal" not in extra
