"""Unit tests for discovery helpers — _parse_member default_region param (PRIN-DSC-02)."""

from __future__ import annotations

from lol_discovery._helpers import _parse_member, _should_skip_seeded


class TestParseMemberDefaultRegion:
    """PRIN-DSC-02: _parse_member takes explicit default_region parameter."""

    def test_parse_member__with_region__ignores_default(self):
        puuid, region = _parse_member("abc:euw1", default_region="kr")
        assert puuid == "abc"
        assert region == "euw1"

    def test_parse_member__without_region__uses_explicit_default(self):
        puuid, region = _parse_member("abc-def-123", default_region="kr")
        assert puuid == "abc-def-123"
        assert region == "kr"

    def test_parse_member__without_region__uses_euw1_default(self):
        puuid, region = _parse_member("xyz", default_region="euw1")
        assert puuid == "xyz"
        assert region == "euw1"

    def test_parse_member__empty_puuid_colon_region__falls_back(self):
        """':region' with empty puuid returns whole string with default."""
        puuid, region = _parse_member(":kr", default_region="jp1")
        assert puuid == ":kr"
        assert region == "jp1"

    def test_parse_member__puuid_with_colons__last_segment_is_region(self):
        puuid, region = _parse_member("a:b:c:jp1", default_region="na1")
        assert puuid == "a:b:c"
        assert region == "jp1"


class TestShouldSkipSeeded:
    """_should_skip_seeded decides whether to skip already-seeded players."""

    def test_no_recrawl_after__returns_true(self):
        assert _should_skip_seeded(None, 1000.0) is True

    def test_empty_recrawl_after__returns_true(self):
        assert _should_skip_seeded("", 1000.0) is True

    def test_future_recrawl_after__returns_true(self):
        assert _should_skip_seeded("2000.0", 1000.0) is True

    def test_past_recrawl_after__returns_none(self):
        """When recrawl_after has passed, allow re-promotion."""
        assert _should_skip_seeded("500.0", 1000.0) is None

    def test_invalid_recrawl_after__returns_none(self):
        """Non-numeric recrawl_after allows re-promotion."""
        assert _should_skip_seeded("not-a-number", 1000.0) is None
