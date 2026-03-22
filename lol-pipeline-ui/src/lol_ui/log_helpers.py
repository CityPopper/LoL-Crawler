"""Log viewer helpers — tail, parse, render, and merge structured log files."""

from __future__ import annotations

import collections
import heapq
import html
import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants (will move to constants.py when that module is created)
# ---------------------------------------------------------------------------

_LOG_LINES = 50
_LOG_LEVEL_CSS: dict[str, str] = {
    "CRITICAL": "log-critical",
    "ERROR": "log-error",
    "WARNING": "log-warning",
    "DEBUG": "log-debug",
}
_EST_BYTES_PER_LOG_LINE = 600  # heuristic for JSON structured log lines


# ---------------------------------------------------------------------------
# Inline rendering helper (will import from rendering.py when that module
# is created)
# ---------------------------------------------------------------------------


def _empty_state(title: str, body_html: str) -> str:
    """Render an empty-state message. Both params are raw HTML -- callers MUST
    pre-escape any dynamic content with html.escape().
    """
    return f'<div class="empty-state"><p><strong>{title}</strong></p><p>{body_html}</p></div>'


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
    rows: list[str] = []
    for line in raw_lines:
        ts, level, logger, msg, extra = _parse_log_line(line)
        line_cls = _LOG_LEVEL_CSS.get(level, "")
        badge_cls = _LOG_LEVEL_CSS.get(level, "log-info")
        rows.append(
            f'<div class="log-line {line_cls}">'
            f'<span class="log-ts">{html.escape(ts)}</span>'
            f'<span class="log-badge {badge_cls}">{html.escape(level)}</span>'
            f'<span class="log-svc">{html.escape(logger)}</span>'
            f'<span class="log-msg">{html.escape(msg)}</span>'
            + (f'<span class="log-extra">{html.escape(extra)}</span>' if extra else "")
            + "</div>"
        )
    return (
        "\n".join(rows)
        if rows
        else _empty_state("No log entries", "Services may not have written any logs yet.")
    )


def _merged_log_lines(log_dir: Path, n: int) -> list[str]:
    """Read last n lines from ALL log files, merge by timestamp, return newest n.

    Each per-file tail is already sorted (log files are append-only), so we
    use ``heapq.merge`` on the pre-sorted iterables instead of a full sort.
    We then take the last *n* items with ``collections.deque(maxlen=n)``
    to bound memory when the merged stream is large.
    """
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
