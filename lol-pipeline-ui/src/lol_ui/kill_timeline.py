"""Kill event timeline — chronological list grouped by minute.

Provides:
- ``_kill_event_row_html(event, version)`` — single event row
- ``_kill_timeline_html(events, version)`` — full timeline list
"""

from __future__ import annotations

import html

from lol_ui.strings import t


def _format_timestamp(ms: int) -> str:
    """Format a millisecond timestamp as MM:SS."""
    total_s = max(ms, 0) // 1000
    minutes = total_s // 60
    seconds = total_s % 60
    return str(minutes).zfill(2) + ":" + str(seconds).zfill(2)


def _champ_icon_xs(champion_name: str, version: str | None) -> str:
    """Render a tiny champion icon for timeline events.

    Falls back to escaped text when version is unavailable.
    """
    if not version or not champion_name:
        safe = html.escape(champion_name or "?")
        return '<span class="kill-event__champ-text">' + safe + "</span>"
    safe_name = html.escape(champion_name)
    safe_version = html.escape(version)
    url = (
        "https://ddragon.leagueoflegends.com/cdn/"
        + safe_version
        + "/img/champion/"
        + safe_name
        + ".png"
    )
    return (
        '<img src="' + url + '" alt="' + safe_name + '" class="champion-icon champion-icon--xs"'
        ' loading="lazy"'
        " onerror=\"this.style.display='none'\">"
    )


def _kill_event_row_html(event: dict[str, object], version: str | None) -> str:
    """Render a single kill event row.

    Expected event keys:
        - t: int (timestamp in ms)
        - killer: str (champion name)
        - victim: str (champion name)
        - assists: list[str] (champion names, may be empty)

    Returns an HTML div with timestamp, killer icon, arrow, victim icon,
    and optional assist icons.
    """
    timestamp = event.get("t", 0)
    if not isinstance(timestamp, int):
        timestamp = 0
    time_str = _format_timestamp(timestamp)

    killer = str(event.get("killer", "?"))
    victim = str(event.get("victim", "?"))
    assists = event.get("assists", [])
    if not isinstance(assists, list):
        assists = []

    killer_icon = _champ_icon_xs(killer, version)
    victim_icon = _champ_icon_xs(victim, version)

    assists_html = ""
    if assists:
        assist_icons = "".join(_champ_icon_xs(str(a), version) for a in assists)
        assists_html = '<span class="kill-event__assists">(+' + assist_icons + ")" + "</span>"

    return (
        '<div class="kill-event">'
        '<span class="kill-event__time">'
        + time_str
        + "</span>"
        + killer_icon
        + '<span class="kill-event__arrow">\u2192</span>'
        + victim_icon
        + assists_html
        + "</div>"
    )


def _kill_timeline_html(
    events: list[dict[str, object]],
    version: str | None,
) -> str:
    """Render a chronological kill timeline grouped by minute.

    Events are sorted by timestamp ascending. Minute headers separate groups.
    Returns a "no kill data" message when events is empty.
    """
    if not events:
        return '<p class="warning">' + t("no_kill_data") + "</p>"

    # Sort by timestamp
    def _event_time(e: dict[str, object]) -> int:
        ts = e.get("t", 0)
        return ts if isinstance(ts, int) else 0

    sorted_events = sorted(events, key=_event_time)

    parts: list[str] = []
    current_minute = -1

    for event in sorted_events:
        timestamp = event.get("t", 0)
        if not isinstance(timestamp, int):
            timestamp = 0
        minute = timestamp // 60000

        if minute != current_minute:
            current_minute = minute
            parts.append('<div class="kill-timeline__minute-header">' + str(minute) + ":00</div>")

        parts.append(_kill_event_row_html(event, version))

    body = "".join(parts)
    return '<div class="kill-timeline">' + body + "</div>"
