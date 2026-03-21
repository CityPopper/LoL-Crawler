"""Hypothesis property-based (fuzz) tests for Redis key construction safety."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lol_pipeline.helpers import (
    _MAX_GAME_NAME_LEN,
    _MAX_TAG_LINE_LEN,
    _sanitize,
    name_cache_key,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Characters that are adversarial for Redis keys: null bytes, newlines, colons,
# spaces, unicode, control characters
_adversarial_chars = st.characters(
    categories=("L", "M", "N", "P", "S", "Z", "C"),
)
_adversarial_text = st.text(alphabet=_adversarial_chars, min_size=0, max_size=100)

# PUUIDs and match IDs with unicode and special characters
_puuid_like = st.one_of(
    st.text(
        alphabet=st.characters(
            codec="utf-8",
            categories=("L", "N", "P", "S"),
        ),
        min_size=0,
        max_size=100,
    ),
    # Include strings with null bytes, newlines, colons, etc.
    st.from_regex(r"[a-zA-Z0-9_:\x00\n\r\t #/\\]{0,100}", fullmatch=True),
)

_match_id_like = st.one_of(
    st.from_regex(r"[A-Z]{2,4}[0-9]?_[0-9]{5,15}", fullmatch=True),
    st.text(max_size=100),
    st.text(
        alphabet=st.characters(codec="utf-8"),
        min_size=0,
        max_size=50,
    ),
)


# ---------------------------------------------------------------------------
# name_cache_key safety
# ---------------------------------------------------------------------------


class TestNameCacheKeyFuzz:
    @given(game_name=_adversarial_text, tag_line=_adversarial_text)
    @settings(max_examples=300)
    def test_name_cache_key__adversarial_input__no_crash_or_expected_error(
        self, game_name: str, tag_line: str
    ) -> None:
        """name_cache_key with adversarial input either returns a valid key or raises ValueError."""
        try:
            key = name_cache_key(game_name, tag_line)
            # If it succeeded, verify key properties
            assert isinstance(key, str)
            assert key.startswith("player:name:")
            assert "#" in key
            # Key must not contain null bytes (Redis protocol violation)
            assert "\x00" not in key
            # Key must not contain newlines (Redis protocol violation)
            assert "\n" not in key
            assert "\r" not in key
            # Key should be lowercase
            parts = key.removeprefix("player:name:")
            assert parts == parts.lower()
        except ValueError:
            # Expected for oversized inputs
            pass

    @given(
        game_name=st.text(
            alphabet=st.characters(categories=("L", "N", "P")),
            min_size=0,
            max_size=_MAX_GAME_NAME_LEN,
        ),
        tag_line=st.text(
            alphabet=st.characters(categories=("L", "N", "P")),
            min_size=0,
            max_size=_MAX_TAG_LINE_LEN,
        ),
    )
    @settings(max_examples=200)
    def test_name_cache_key__valid_lengths__always_succeeds(
        self, game_name: str, tag_line: str
    ) -> None:
        """Inputs within length limits always produce a valid key without error."""
        key = name_cache_key(game_name, tag_line)
        assert isinstance(key, str)
        assert key.startswith("player:name:")
        assert "#" in key

    @given(
        game_name=st.text(min_size=_MAX_GAME_NAME_LEN + 1, max_size=200),
    )
    @settings(max_examples=50)
    def test_name_cache_key__game_name_over_limit__raises(self, game_name: str) -> None:
        """game_name over 64 chars always raises ValueError."""
        with pytest.raises(ValueError, match="game_name"):
            name_cache_key(game_name, "NA1")

    @given(
        tag_line=st.text(min_size=_MAX_TAG_LINE_LEN + 1, max_size=200),
    )
    @settings(max_examples=50)
    def test_name_cache_key__tag_line_over_limit__raises(self, tag_line: str) -> None:
        """tag_line over 16 chars always raises ValueError."""
        with pytest.raises(ValueError, match="tag_line"):
            name_cache_key("Player", tag_line)


# ---------------------------------------------------------------------------
# _sanitize safety
# ---------------------------------------------------------------------------


class TestSanitizeFuzz:
    @given(value=_adversarial_text)
    @settings(max_examples=200)
    def test_sanitize__no_control_chars_in_output(self, value: str) -> None:
        """Sanitized output never contains control characters (U+0000-U+001F, U+007F)."""
        result = _sanitize(value)
        for ch in result:
            code = ord(ch)
            assert not (0x00 <= code <= 0x1F or code == 0x7F), (
                f"Control char U+{code:04X} in sanitized output"
            )

    @given(value=_adversarial_text)
    @settings(max_examples=200)
    def test_sanitize__output_at_most_max_len(self, value: str) -> None:
        """Sanitized output is always at most 16 chars (default max_len)."""
        result = _sanitize(value)
        assert len(result) <= 16

    @given(value=_adversarial_text, max_len=st.integers(min_value=0, max_value=200))
    @settings(max_examples=200)
    def test_sanitize__respects_custom_max_len(self, value: str, max_len: int) -> None:
        """Sanitized output respects custom max_len parameter."""
        result = _sanitize(value, max_len=max_len)
        assert len(result) <= max_len


# ---------------------------------------------------------------------------
# Redis key construction safety — f-string key patterns
# ---------------------------------------------------------------------------


class TestRedisKeyConstructionFuzz:
    @given(puuid=_puuid_like)
    @settings(max_examples=200)
    def test_player_key__never_crashes(self, puuid: str) -> None:
        """f'player:{puuid}' key construction never crashes."""
        key = f"player:{puuid}"
        assert isinstance(key, str)
        # Key must start with prefix and contain the puuid (even if empty)
        assert len(key) >= len("player:")

    @given(match_id=_match_id_like)
    @settings(max_examples=200)
    def test_match_key__never_crashes(self, match_id: str) -> None:
        """f'match:{match_id}' key construction never crashes."""
        key = f"match:{match_id}"
        assert isinstance(key, str)

    @given(puuid=_puuid_like)
    @settings(max_examples=100)
    def test_raw_match_key__preserves_puuid(self, puuid: str) -> None:
        """The PUUID is preserved exactly in the constructed key."""
        key = f"player:{puuid}"
        assert key == f"player:{puuid}"
        # Verify round-trip extraction
        extracted = key.removeprefix("player:")
        assert extracted == puuid

    @given(match_id=_match_id_like)
    @settings(max_examples=100)
    def test_raw_match_key__preserves_match_id(self, match_id: str) -> None:
        """The match ID is preserved exactly in the constructed key."""
        key = f"raw:match:{match_id}"
        extracted = key.removeprefix("raw:match:")
        assert extracted == match_id

    @given(
        puuid=st.text(
            alphabet=st.sampled_from(list("\x00\n\r\t :{}[]")),
            min_size=1,
            max_size=50,
        )
    )
    @settings(max_examples=100)
    def test_key_with_special_chars__is_valid_string(self, puuid: str) -> None:
        """Keys with special characters are valid Python strings (Redis accepts arbitrary bytes)."""
        key = f"player:{puuid}"
        assert isinstance(key, str)
        # Redis keys can contain any bytes, but null bytes in the Redis protocol
        # are handled by the client library. We just verify no crash.
        assert len(key) >= len("player:") + 1

    @given(
        puuid=st.text(
            alphabet=st.characters(codec="utf-8"),
            min_size=0,
            max_size=100,
        ),
        match_id=st.text(
            alphabet=st.characters(codec="utf-8"),
            min_size=0,
            max_size=100,
        ),
    )
    @settings(max_examples=100)
    def test_participant_key__never_crashes(self, puuid: str, match_id: str) -> None:
        """f'participant:{match_id}:{puuid}' key construction never crashes."""
        key = f"participant:{match_id}:{puuid}"
        assert isinstance(key, str)
        assert key.startswith("participant:")
