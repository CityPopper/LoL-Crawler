"""Key-builder helpers for lol_crawler."""

from __future__ import annotations


def _key_crawl_cursor(puuid: str) -> str:
    return f"crawl:cursor:{puuid}"


def _key_player(puuid: str) -> str:
    return f"player:{puuid}"


def _key_player_matches(puuid: str) -> str:
    return f"player:matches:{puuid}"
