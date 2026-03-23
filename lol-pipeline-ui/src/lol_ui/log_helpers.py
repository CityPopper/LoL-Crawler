"""Log viewer helpers — tail, parse, render, and merge structured log files."""

from __future__ import annotations

import collections
import heapq
import html
import json
from pathlib import Path
from typing import Any

from lol_ui.constants import _EST_BYTES_PER_LOG_LINE, _LOG_LEVEL_CSS
from lol_ui.rendering import _empty_state

# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------


def _tail_file(path: Path, n: int) -> list[str]:
    """Read last n non-empty lines from a file efficiently (byte-seeks from end)."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return []
            read_bytes = min(n * _EST_BYTES_PER_LOG_LINE, size)
            f.seek(size - read_bytes)
            raw = f.read()
        parts = raw.split(b"\n")
        if size > read_bytes:
            parts = parts[1:]
        lines = [p.decode("utf-8", errors="replace") for p in parts if p.strip()]
        return lines[-n:]
    except OSError:
        return []


def _parse_log_line(line: str) -> tuple[str, str, str, str, str]:
    """Return (timestamp, level, logger, message, extra_kv) from a JSON log line."""
    try:
        d: dict[str, Any] = json.loads(line)
        ts = str(d.pop("timestamp", ""))[:19].replace("T", " ")
        level = str(d.pop("level", "INFO"))
        logger = str(d.pop("logger", ""))
        msg = str(d.pop("message", line))
        extra = "  ".join(f"{k}={v}" for k, v in d.items() if not str(k).startswith("_"))
        return ts, level, logger, msg, extra
    except (json.JSONDecodeError, TypeError, AttributeError):
        return "", "INFO", "", line, ""


def _render_log_lines(raw_lines: list[str]) -> str:
    """Render log lines as expandable HTML divs.

    Each log line is clickable. Clicking toggles a detail section showing the
    full extra key-value fields. Uses the same ``classList.toggle('open')``
    pattern as match detail.
    """
    rows: list[str] = []
    for line in raw_lines:
        ts, level, logger, msg, extra = _parse_log_line(line)
        line_cls = _LOG_LEVEL_CSS.get(level, "")
        badge_cls = _LOG_LEVEL_CSS.get(level, "log-info")
        detail_html = ""
        if extra:
            detail_html = (
                f'<div class="log-detail">'
                f'<pre class="log-detail__pre">{html.escape(extra)}</pre>'
                f"</div>"
            )
        rows.append(
            f'<div class="log-entry {line_cls}">'
            f'<div class="log-line" onclick="this.parentElement.classList.toggle(\'open\')">'
            f'<span class="log-ts">{html.escape(ts)}</span>'
            f'<span class="log-badge {badge_cls}">{html.escape(level)}</span>'
            f'<span class="log-svc">{html.escape(logger)}</span>'
            f'<span class="log-msg">{html.escape(msg)}</span>'
            + ('<span class="log-expand-hint">&#9654;</span>' if extra else "")
            + "</div>"
            + detail_html
            + "</div>"
        )
    return (
        "\n".join(rows)
        if rows
        else _empty_state("No log entries", "Services may not have written any logs yet.")
    )


def _merged_log_lines(
    log_dir: Path,
    n: int,
    service_filter: str = "",
) -> list[str]:
    """Read last n lines from log files, merge by timestamp, return newest n.

    When *service_filter* is non-empty, only the matching ``<service>.log``
    file is read.  Otherwise all ``*.log`` files are merged.

    Each per-file tail is already sorted (log files are append-only), so we
    use ``heapq.merge`` on the pre-sorted iterables instead of a full sort.
    We then take the last *n* items with ``collections.deque(maxlen=n)``
    to bound memory when the merged stream is large.
    """
    if service_filter:
        target = log_dir / f"{service_filter}.log"
        log_files = [target] if target.exists() else []
    else:
        log_files = list(log_dir.glob("*.log"))
    per_file = max(n // len(log_files) + 1, 10) if log_files else 0

    def _keyed(f: Path) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for line in _tail_file(f, per_file):
            try:
                d = json.loads(line)
                ts = str(d.get("timestamp", ""))
            except (json.JSONDecodeError, TypeError):
                ts = ""
            result.append((ts, line))
        return result

    per_file_iters = [_keyed(f) for f in log_files]
    merged = heapq.merge(*per_file_iters, key=lambda x: x[0])
    tail: collections.deque[tuple[str, str]] = collections.deque(merged, maxlen=n)
    return [line for _, line in tail]
