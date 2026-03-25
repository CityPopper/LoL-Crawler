"""Unit tests for lol_crawler._helpers — key-builder functions (PRIN-CRW-01)."""

from __future__ import annotations

from lol_crawler._helpers import _key_crawl_cursor, _key_player, _key_player_matches


class TestKeyCrawlCursor:
    def test_key_crawl_cursor__valid_puuid__returns_prefixed_key(self):
        assert _key_crawl_cursor("abc-123") == "crawl:cursor:abc-123"

    def test_key_crawl_cursor__empty_puuid__returns_prefix_only(self):
        assert _key_crawl_cursor("") == "crawl:cursor:"

    def test_key_crawl_cursor__puuid_with_colons__preserves_colons(self):
        assert _key_crawl_cursor("a:b:c") == "crawl:cursor:a:b:c"


class TestKeyPlayer:
    def test_key_player__valid_puuid__returns_prefixed_key(self):
        assert _key_player("puuid-xyz") == "player:puuid-xyz"

    def test_key_player__empty_puuid__returns_prefix_only(self):
        assert _key_player("") == "player:"


class TestKeyPlayerMatches:
    def test_key_player_matches__valid_puuid__returns_prefixed_key(self):
        assert _key_player_matches("puuid-abc") == "player:matches:puuid-abc"

    def test_key_player_matches__empty_puuid__returns_prefix_only(self):
        assert _key_player_matches("") == "player:matches:"
