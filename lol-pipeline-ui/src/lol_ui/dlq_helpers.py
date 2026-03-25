"""DLQ page helpers — summary analytics and replay envelope construction."""

from __future__ import annotations

import contextlib
import html

import redis.asyncio as aioredis
from lol_pipeline.models import DLQEnvelope, MessageEnvelope, make_replay_envelope

from lol_ui.rendering import _badge, _time_ago
from lol_ui.strings import t as _t


_make_replay_envelope = make_replay_envelope


async def _dlq_summary_html(r: aioredis.Redis) -> tuple[str, int]:
    """Build an analytics summary card for the DLQ page.

    Returns a tuple of ``(html_string, dlq_depth)`` so callers can
    reuse the depth without a redundant ``XLEN`` call.
    """
    dlq_depth: int = await r.xlen("stream:dlq")
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
            f"{_t('dlq_breakdown_failure_codes')}</h4>"
            '<table style="margin:0"><thead><tr>'
            f'<th scope="col">{_t("dlq_breakdown_col_code")}</th>'
            f'<th scope="col" style="text-align:right">{_t("dlq_breakdown_col_count")}</th>'
            f"</tr></thead><tbody>{code_rows}</tbody></table></div>"
        )
    if stream_rows:
        breakdowns += (
            '<div style="flex:1;min-width:200px">'
            '<h4 style="margin:0 0 var(--space-sm);color:var(--color-muted)">'
            f"{_t('dlq_breakdown_source_streams')}</h4>"
            '<table style="margin:0"><thead><tr>'
            f'<th scope="col">{_t("dlq_breakdown_col_stream")}</th>'
            f'<th scope="col" style="text-align:right">{_t("dlq_breakdown_col_count")}</th>'
            f"</tr></thead><tbody>{stream_rows}</tbody></table></div>"
        )

    summary = f"""<div class="card">
  <h3 class="card__title">{_t("dlq_analytics_title")}</h3>
  <div style="display:flex;gap:var(--space-xl);flex-wrap:wrap;margin-bottom:var(--space-md)">
    <div class="stat">
      <span class="stat__value">{dlq_depth}</span>
      <span class="stat__label">{_t("dlq_stat_pending")}</span>
    </div>
    <div class="stat">
      <span class="stat__value">{archive_depth}</span>
      <span class="stat__label">{_t("dlq_stat_archived")}</span>
    </div>
    <div class="stat">
      <span class="stat__value">{html.escape(oldest_age)}</span>
      <span class="stat__label">{_t("dlq_stat_oldest")}</span>
    </div>
  </div>
  <div style="display:flex;gap:var(--space-xl);flex-wrap:wrap">
    {breakdowns}
  </div>
</div>
"""
    return summary, dlq_depth
