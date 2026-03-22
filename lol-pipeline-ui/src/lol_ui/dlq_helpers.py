"""DLQ page helpers — summary analytics and replay envelope construction."""

from __future__ import annotations

import contextlib
import html

import redis.asyncio as aioredis
from lol_pipeline.models import DLQEnvelope, MessageEnvelope

from lol_ui.rendering import _badge, _time_ago


def _make_replay_envelope(dlq: DLQEnvelope, max_attempts: int) -> MessageEnvelope:
    """Reconstruct a MessageEnvelope from a DLQEnvelope for replay."""
    original_type = dlq.original_stream.removeprefix("stream:")
    return MessageEnvelope(
        source_stream=dlq.original_stream,
        type=original_type,
        payload=dlq.payload,
        max_attempts=max_attempts,
        enqueued_at=dlq.enqueued_at,
        dlq_attempts=dlq.dlq_attempts,
        priority=dlq.priority,
        correlation_id=dlq.correlation_id,
    )


async def _dlq_summary_html(r: aioredis.Redis) -> str:
    """Build an analytics summary card for the DLQ page."""
    dlq_depth = await r.xlen("stream:dlq")
    archive_depth = await r.xlen("stream:dlq:archive")

    # Read up to 500 entries for breakdown aggregation
    scan_entries: list[tuple[str, dict[str, str]]] = await r.xrange(
        "stream:dlq",
        min="-",
        max="+",
        count=500,
    )

    # Aggregate by failure_code and source_stream (original_stream)
    code_counts: dict[str, int] = {}
    stream_counts: dict[str, int] = {}
    oldest_ts_ms: int | None = None

    for entry_id, fields in scan_entries:
        fc = fields.get("failure_code", "unknown")
        code_counts[fc] = code_counts.get(fc, 0) + 1
        os = fields.get("original_stream", "unknown")
        stream_counts[os] = stream_counts.get(os, 0) + 1
        if oldest_ts_ms is None:
            # First entry is the oldest (XRANGE returns ascending)
            ts_part = entry_id.split("-", 1)[0]
            with contextlib.suppress(ValueError):
                oldest_ts_ms = int(ts_part)

    oldest_age = _time_ago(oldest_ts_ms) if oldest_ts_ms else "n/a"

    # Build failure-code breakdown rows
    code_rows = ""
    for fc, count in sorted(code_counts.items(), key=lambda x: x[1], reverse=True):
        code_rows += (
            f'<tr><td>{_badge("error", fc)}</td><td style="text-align:right">{count}</td></tr>'
        )

    # Build source-stream breakdown rows
    stream_rows = ""
    for os, count in sorted(stream_counts.items(), key=lambda x: x[1], reverse=True):
        stream_rows += (
            f'<tr><td>{html.escape(os)}</td><td style="text-align:right">{count}</td></tr>'
        )

    breakdowns = ""
    if code_rows:
        breakdowns += (
            '<div style="flex:1;min-width:200px">'
            '<h4 style="margin:0 0 var(--space-sm);color:var(--color-muted)">'
            "Failure Codes</h4>"
            '<table style="margin:0"><thead><tr>'
            '<th scope="col">Code</th><th scope="col" style="text-align:right">Count</th>'
            f"</tr></thead><tbody>{code_rows}</tbody></table></div>"
        )
    if stream_rows:
        breakdowns += (
            '<div style="flex:1;min-width:200px">'
            '<h4 style="margin:0 0 var(--space-sm);color:var(--color-muted)">'
            "Source Streams</h4>"
            '<table style="margin:0"><thead><tr>'
            '<th scope="col">Stream</th><th scope="col" style="text-align:right">Count</th>'
            f"</tr></thead><tbody>{stream_rows}</tbody></table></div>"
        )

    return f"""<div class="card">
  <h3 class="card__title">DLQ Analytics</h3>
  <div style="display:flex;gap:var(--space-xl);flex-wrap:wrap;margin-bottom:var(--space-md)">
    <div class="stat">
      <span class="stat__value">{dlq_depth}</span>
      <span class="stat__label">pending</span>
    </div>
    <div class="stat">
      <span class="stat__value">{archive_depth}</span>
      <span class="stat__label">archived</span>
    </div>
    <div class="stat">
      <span class="stat__value">{html.escape(oldest_age)}</span>
      <span class="stat__label">oldest message</span>
    </div>
  </div>
  <div style="display:flex;gap:var(--space-xl);flex-wrap:wrap">
    {breakdowns}
  </div>
</div>
"""
