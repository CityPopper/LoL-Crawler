"""Tests for match_detail.py — dead code removal verified."""

from __future__ import annotations

import inspect

from lol_ui import match_detail


class TestDeadCodeRemoval:
    """_render_build_section should not exist (dead code removed)."""

    def test_render_build_section__removed(self):
        members = [name for name, _ in inspect.getmembers(match_detail, inspect.isfunction)]
        assert "_render_build_section" not in members

    def test_render_detail_player__still_exists(self):
        assert hasattr(match_detail, "_render_detail_player")
