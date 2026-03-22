"""Tests for the summoner icon module (summoner_icon.py)."""

from __future__ import annotations

from lol_ui.summoner_icon import _summoner_icon_html


class TestSummonerIconHtml:
    """_summoner_icon_html renders DDragon profile icon with level badge."""

    def test_renders_icon_with_version_and_id(self):
        html = _summoner_icon_html("4567", "100", "14.10.1")
        assert "ddragon.leagueoflegends.com" in html
        assert "profileicon/4567.png" in html
        assert "14.10.1" in html

    def test_renders_level_badge(self):
        html = _summoner_icon_html("4567", "250", "14.10.1")
        assert "250" in html

    def test_no_level__no_badge(self):
        html = _summoner_icon_html("4567", None, "14.10.1")
        assert "profileicon/4567.png" in html
        # No level badge div with bottom positioning
        assert "bottom:-4px" not in html

    def test_no_icon_id__renders_placeholder(self):
        html = _summoner_icon_html(None, "250", "14.10.1")
        assert "?" in html
        assert "profileicon" not in html

    def test_no_version__renders_placeholder(self):
        html = _summoner_icon_html("4567", "100", None)
        assert "?" in html
        assert "ddragon" not in html

    def test_both_none__placeholder_no_level(self):
        html = _summoner_icon_html(None, None, None)
        assert "?" in html
        assert "bottom:-4px" not in html

    def test_html_escapes_icon_id(self):
        html = _summoner_icon_html("<script>", "1", "14.10.1")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_html_escapes_level(self):
        result = _summoner_icon_html("1", "<img src=x>", "14.10.1")
        # The level badge text must be escaped; raw <img src=x> must NOT appear
        assert "&lt;img src=x&gt;" in result

    def test_empty_string_icon_id__placeholder(self):
        html = _summoner_icon_html("", "100", "14.10.1")
        assert "?" in html
        assert "profileicon" not in html
