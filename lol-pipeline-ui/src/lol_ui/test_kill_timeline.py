"""Tests for kill event timeline (T4-3)."""

from __future__ import annotations

from lol_ui.kill_timeline import (
    _champ_icon_xs,
    _format_timestamp,
    _kill_event_row_html,
    _kill_timeline_html,
)

# ---------------------------------------------------------------------------
# _format_timestamp
# ---------------------------------------------------------------------------


class TestFormatTimestamp:
    """_format_timestamp converts milliseconds to MM:SS."""

    def test_zero__returns_00_00(self):
        assert _format_timestamp(0) == "00:00"

    def test_one_minute(self):
        assert _format_timestamp(60000) == "01:00"

    def test_ninety_seconds(self):
        assert _format_timestamp(90000) == "01:30"

    def test_ten_minutes(self):
        assert _format_timestamp(600000) == "10:00"

    def test_negative__clamps_to_zero(self):
        assert _format_timestamp(-5000) == "00:00"

    def test_partial_seconds(self):
        # 65500ms = 1:05.5 -> 01:05
        assert _format_timestamp(65500) == "01:05"


# ---------------------------------------------------------------------------
# _champ_icon_xs
# ---------------------------------------------------------------------------


class TestChampIconXs:
    """_champ_icon_xs renders a small champion icon."""

    def test_with_version__returns_img(self):
        result = _champ_icon_xs("Ahri", "14.1.1")
        assert "<img" in result
        assert "Ahri" in result

    def test_without_version__returns_text(self):
        result = _champ_icon_xs("Ahri", None)
        assert "Ahri" in result
        assert "<img" not in result

    def test_empty_name__returns_question_mark(self):
        result = _champ_icon_xs("", "14.1.1")
        assert "?" in result

    def test_escapes_champion_name(self):
        result = _champ_icon_xs("<script>", "14.1.1")
        assert "<script>" not in result

    def test_has_xs_class(self):
        result = _champ_icon_xs("Ahri", "14.1.1")
        assert "champion-icon--xs" in result


# ---------------------------------------------------------------------------
# _kill_event_row_html
# ---------------------------------------------------------------------------


class TestKillEventRowHtml:
    """_kill_event_row_html renders a single kill event."""

    def test_basic_event__contains_timestamp(self):
        event = {"t": 120000, "killer": "Ahri", "victim": "Zed", "assists": []}
        result = _kill_event_row_html(event, "14.1.1")
        assert "02:00" in result

    def test_basic_event__contains_arrow(self):
        event = {"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": []}
        result = _kill_event_row_html(event, "14.1.1")
        assert "\u2192" in result

    def test_basic_event__contains_killer_and_victim(self):
        event = {"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": []}
        result = _kill_event_row_html(event, "14.1.1")
        assert "Ahri" in result
        assert "Zed" in result

    def test_with_assists__shows_assist_icons(self):
        event = {"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": ["Thresh", "Jinx"]}
        result = _kill_event_row_html(event, "14.1.1")
        assert "Thresh" in result
        assert "Jinx" in result
        assert "kill-event__assists" in result

    def test_no_assists__no_assist_section(self):
        event = {"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": []}
        result = _kill_event_row_html(event, "14.1.1")
        assert "kill-event__assists" not in result

    def test_missing_timestamp__defaults_to_zero(self):
        event: dict[str, object] = {"killer": "Ahri", "victim": "Zed", "assists": []}
        result = _kill_event_row_html(event, "14.1.1")
        assert "00:00" in result

    def test_has_event_class(self):
        event = {"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": []}
        result = _kill_event_row_html(event, "14.1.1")
        assert "kill-event" in result

    def test_non_list_assists__treated_as_empty(self):
        event = {"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": "bad"}
        result = _kill_event_row_html(event, "14.1.1")
        assert "kill-event__assists" not in result


# ---------------------------------------------------------------------------
# _kill_timeline_html
# ---------------------------------------------------------------------------


class TestKillTimelineHtml:
    """_kill_timeline_html renders grouped kill timeline."""

    def test_empty_events__shows_no_data_message(self):
        result = _kill_timeline_html([], "14.1.1")
        assert "No kill events" in result

    def test_single_event__has_minute_header(self):
        events = [{"t": 90000, "killer": "Ahri", "victim": "Zed", "assists": []}]
        result = _kill_timeline_html(events, "14.1.1")
        assert "1:00" in result  # minute header for minute 1

    def test_events_sorted_by_timestamp(self):
        events = [
            {"t": 300000, "killer": "Zed", "victim": "Ahri", "assists": []},
            {"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": []},
        ]
        result = _kill_timeline_html(events, "14.1.1")
        # First event should appear before second
        ahri_pos = result.index("01:00")
        zed_pos = result.index("05:00")
        assert ahri_pos < zed_pos

    def test_grouped_by_minute__two_events_same_minute(self):
        events = [
            {"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": []},
            {"t": 90000, "killer": "Thresh", "victim": "Jinx", "assists": []},
        ]
        result = _kill_timeline_html(events, "14.1.1")
        # Should have only one "1:00" minute header
        assert result.count("kill-timeline__minute-header") == 1

    def test_different_minutes__multiple_headers(self):
        events = [
            {"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": []},
            {"t": 180000, "killer": "Thresh", "victim": "Jinx", "assists": []},
        ]
        result = _kill_timeline_html(events, "14.1.1")
        assert result.count("kill-timeline__minute-header") == 2

    def test_has_timeline_wrapper(self):
        events = [{"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": []}]
        result = _kill_timeline_html(events, "14.1.1")
        assert "kill-timeline" in result

    def test_multiple_events__all_rendered(self):
        events = [
            {"t": 60000, "killer": "Ahri", "victim": "Zed", "assists": []},
            {"t": 120000, "killer": "Thresh", "victim": "Jinx", "assists": ["Ahri"]},
            {"t": 300000, "killer": "Zed", "victim": "Ahri", "assists": []},
        ]
        result = _kill_timeline_html(events, "14.1.1")
        assert result.count('kill-event"') == 3  # 3 kill-event divs
