"""Tests for rank card, rank history, and profile header (rank.py)."""

from __future__ import annotations

from lol_ui.rank import _profile_header_html, _rank_card_html, _rank_history_html


class TestRankCardHtml:
    """_rank_card_html renders a rank card from hash data."""

    def test_renders_tier_and_division(self):
        rank = {"tier": "GOLD", "division": "II", "lp": "75", "wins": "60", "losses": "40"}
        html = _rank_card_html(rank)
        assert "GOLD" in html
        assert "II" in html
        assert "75 LP" in html

    def test_empty_rank__empty_string(self):
        assert _rank_card_html({}) == ""


class TestRankHistoryHtml:
    """_rank_history_html renders a table from ZRANGE data."""

    def test_renders_table_rows(self):
        entries = [("GOLD:II:75", 1700000000000.0)]
        html = _rank_history_html(entries)
        assert "<table>" in html
        assert "GOLD" in html
        assert "75 LP" in html

    def test_empty_entries__empty_string(self):
        assert _rank_history_html([]) == ""


class TestProfileHeaderHtml:
    """_profile_header_html renders profile with avatar and name."""

    def test_renders_name_and_tag(self):
        rank = {"tier": "PLATINUM", "division": "IV", "lp": "10"}
        html = _profile_header_html("Faker", "KR1", rank)
        assert "Faker" in html
        assert "#KR1" in html
        assert "PLATINUM IV" in html

    def test_unranked_fallback(self):
        html = _profile_header_html("TestUser", "NA1", {})
        assert "Unranked" in html

    def test_letter_circle_fallback__no_icon(self):
        html = _profile_header_html("Xerath", "EUW", {"tier": "GOLD"})
        # Should contain the letter "X" in the avatar
        assert ">X<" in html
        # Should NOT contain ddragon profileicon
        assert "profileicon" not in html

    def test_with_summoner_icon(self):
        rank = {"tier": "DIAMOND", "division": "I", "lp": "50"}
        html = _profile_header_html(
            "Faker",
            "KR1",
            rank,
            icon_id="4567",
            level="250",
            version="14.10.1",
        )
        assert "profileicon/4567.png" in html
        assert "250" in html
        # Should NOT contain the letter-circle fallback
        assert ">F<" not in html

    def test_icon_without_version__falls_back_to_letter(self):
        html = _profile_header_html(
            "Test",
            "NA1",
            {},
            icon_id="123",
            level="100",
            version=None,
        )
        # Fallback to letter
        assert ">T<" in html
        assert "profileicon" not in html

    def test_html_escapes_name(self):
        html = _profile_header_html("<script>", "tag", {})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_none_rank__unranked(self):
        html = _profile_header_html("Foo", "Bar", {})
        assert "Unranked" in html
