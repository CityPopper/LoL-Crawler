"""Streams page helpers — group cells and fragment HTML builder."""

from __future__ import annotations

import html
from typing import Any

from lol_pipeline.priority import has_priority_players

from lol_ui.constants import (
    _DEPTH_BADGE_BACKLOG_THRESHOLD,
    _DEPTH_BADGE_BUSY_THRESHOLD,
    _HALT_BANNER,
    _STREAM_KEYS,
)
from lol_ui.rendering import _depth_badge
from lol_ui.strings import t


def _depth_bar_html(stream_name: str, depth: int) -> str:
    """Render a narrow progress bar showing stream depth relative to backlog threshold."""
    if stream_name == "stream:dlq":
        # DLQ: any depth > 0 is an error; cap visual at 10 entries
        cap = max(depth, 10) if depth > 0 else 10
        pct = min(round(depth / cap * 100), 100) if depth > 0 else 0
        cls = "depth-bar__fill--backlog" if depth > 0 else "depth-bar__fill--ok"
    else:
        pct = min(round(depth / _DEPTH_BADGE_BACKLOG_THRESHOLD * 100), 100)
        if depth >= _DEPTH_BADGE_BACKLOG_THRESHOLD:
            cls = "depth-bar__fill--backlog"
        elif depth >= _DEPTH_BADGE_BUSY_THRESHOLD:
            cls = "depth-bar__fill--busy"
        else:
            cls = "depth-bar__fill--ok"
    return (
        f'<div class="depth-bar">'
        f'<div class="{cls} depth-bar__fill" style="width:{pct}%"></div></div>'
    )


def _translate_group_name(raw_name: str) -> str:
    """Return the localized label for a consumer group name, falling back to the raw name.

    Returns unescaped text; callers are responsible for HTML-escaping before rendering.
    """
    key = f"group_{raw_name}"
    translated = t(key)
    # t() returns html.escape(key) as fallback for unknown keys — detect that case
    # by comparing against the escaped key, and return the raw name instead so the
    # caller can apply a single html.escape() pass.
    if translated == html.escape(key):
        return raw_name
    return translated


def _translate_stream_key(raw_key: str) -> str:
    """Return the localized label for a Redis stream key, falling back to the raw key."""
    key = f"stream_key_{raw_key}"
    translated = t(key)
    return translated if translated != key else raw_key


def _format_group_cells(groups: list[dict[str, Any]]) -> str:
    """Render Group / Pending / Lag table cells from XINFO GROUPS output."""
    if not groups:
        return (
            '<td class="text-muted">&mdash;</td>'
            '<td class="text-right text-muted">0</td>'
            '<td class="text-right text-muted">0</td>'
        )
    parts: list[str] = []
    for g in groups:
        raw_name = str(g.get("name", ""))
        name = html.escape(_translate_group_name(raw_name))
        pending = g.get("pending", 0) or 0
        lag = g.get("lag")
        lag_display = str(lag) if lag is not None else "?"
        parts.append(
            f"<td>{name}</td>"
            f'<td class="text-right">{pending}</td>'
            f'<td class="text-right">{lag_display}</td>'
        )
    return "".join(parts)


async def _streams_fragment_html(r: Any) -> str:
    """Build the inner HTML for the streams table + status (no page wrapper).

    Uses a single Redis pipeline round-trip for all calls
    (6 XLEN + 6 XINFO GROUPS + 1 ZCARD + 1 GET).
    """
    async with r.pipeline(transaction=False) as pipe:
        for s in _STREAM_KEYS:
            pipe.xlen(s)
        for s in _STREAM_KEYS:
            pipe.xinfo_groups(s)
        pipe.zcard("delayed:messages")
        pipe.get("system:halted")
        results = await pipe.execute(raise_on_error=False)

    n = len(_STREAM_KEYS)
    # Unpack: N XLEN, N XINFO GROUPS, 1 ZCARD, 1 GET
    stream_lengths: list[int] = results[:n]
    group_infos_raw: list[Any] = results[n : 2 * n]
    delayed: int = results[2 * n]
    halted = results[2 * n + 1]

    # Normalise XINFO GROUPS results: ResponseError → empty list
    group_infos: list[list[dict[str, Any]]] = []
    for info in group_infos_raw:
        if isinstance(info, Exception):
            group_infos.append([])
        else:
            group_infos.append(info)

    has_priority = await has_priority_players(r)

    rows = ""
    for s, length, groups in zip(_STREAM_KEYS, stream_lengths, group_infos, strict=True):
        status_badge = _depth_badge(s, length)
        key_label = html.escape(_translate_stream_key(s))
        group_cells = _format_group_cells(groups)
        depth_bar = _depth_bar_html(s, length)
        length_cell = f'<td class="text-right">{length}{depth_bar}</td>'
        if not groups:
            rows += (
                f"<tr><td>{key_label}</td>{length_cell}{group_cells}<td>{status_badge}</td></tr>"
            )
        else:
            for i, g in enumerate(groups):
                raw_name = str(g.get("name", ""))
                name = html.escape(_translate_group_name(raw_name))
                pending = g.get("pending", 0) or 0
                lag = g.get("lag")
                lag_display = str(lag) if lag is not None else "?"
                if i == 0:
                    rowspan = f' rowspan="{len(groups)}"' if len(groups) > 1 else ""
                    rows += (
                        f"<tr><td{rowspan}>{key_label}</td>"
                        f'<td class="text-right"{rowspan}>{length}{depth_bar}</td>'
                        f"<td>{name}</td>"
                        f'<td class="text-right">{pending}</td>'
                        f'<td class="text-right">{lag_display}</td>'
                        f"<td{rowspan}>{status_badge}</td></tr>"
                    )
                else:
                    rows += (
                        f"<tr><td>{name}</td>"
                        f'<td class="text-right">{pending}</td>'
                        f'<td class="text-right">{lag_display}</td></tr>'
                    )

    delayed_label = html.escape(_translate_stream_key("delayed:messages"))
    delayed_badge = _depth_badge("delayed:messages", delayed)
    delayed_bar = _depth_bar_html("delayed:messages", delayed)
    rows += (
        f"<tr><td>{delayed_label}</td>"
        f'<td class="text-right">{delayed}{delayed_bar}</td>'
        f'<td class="text-muted">&mdash;</td>'
        f'<td class="text-right text-muted">0</td>'
        f'<td class="text-right text-muted">0</td>'
        f"<td>{delayed_badge}</td></tr>"
    )

    status = (
        _HALT_BANNER
        if halted
        else f'<div class="banner banner--success">&#10003; {t("streams_system_running")}</div>'
    )

    priority_display = t("streams_yes") if has_priority else t("streams_no")

    return f"""{status}
<p>{t("streams_priority_label")} <strong>{priority_display}</strong></p>
<div class="table-scroll">
<table class="streams streams--full">
  <thead><tr><th scope="col">{t("streams_col_key")}</th>\
<th scope="col" class="text-right">{t("streams_col_length")}</th>\
<th scope="col">{t("streams_col_group")}</th>\
<th scope="col" class="text-right">{t("streams_col_pending")}</th>\
<th scope="col" class="text-right">{t("streams_col_lag")}</th>\
<th scope="col">{t("streams_col_status")}</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
</div>
"""
