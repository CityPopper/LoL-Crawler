"""DLQ routes — GET /dlq (read-only view)."""

from __future__ import annotations

import html
import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from lol_pipeline._helpers import is_system_halted
from lol_pipeline.log import get_logger
from lol_pipeline.models import DLQEnvelope

from lol_ui.constants import (
    _DLQ_DEFAULT_PER_PAGE,
    _DLQ_MAX_PER_PAGE,
    _HALT_BANNER,
    _STREAM_ENTRY_ID_RE,
)
from lol_ui.dlq_helpers import _dlq_summary_html
from lol_ui.rendering import _badge, _empty_state, _page
from lol_ui.strings import t

_log = get_logger("ui")

router = APIRouter()


@router.get("/dlq", response_class=HTMLResponse)
async def show_dlq(request: Request) -> HTMLResponse:
    """Display dead-letter queue entries with cursor-based pagination (read-only)."""
    r = request.app.state.r
    halted = await is_system_halted(r)
    halt_html = _HALT_BANNER if halted else ""
    summary_html, total_count = await _dlq_summary_html(r)
    try:
        per_page = min(
            int(request.query_params.get("per_page", str(_DLQ_DEFAULT_PER_PAGE))), _DLQ_MAX_PER_PAGE
        )
    except ValueError:
        per_page = _DLQ_DEFAULT_PER_PAGE
    per_page = max(per_page, 1)

    cursor = request.query_params.get("cursor", "-")
    if cursor != "-" and not _STREAM_ENTRY_ID_RE.match(cursor):
        cursor = "-"

    entries: list[tuple[str, dict[str, str]]] = await r.xrange(
        "stream:dlq", min=cursor, max="+", count=per_page + 1
    )
    if not entries:
        body = (
            halt_html
            + f"<h2>{t('page_dlq')}</h2>"
            + summary_html
            + _empty_state(
                t("dlq_empty"),
                t("dlq_healthy"),
            )
        )
        return HTMLResponse(_page(t("page_dlq"), body, path="/dlq"))

    has_next = len(entries) > per_page
    page_entries = entries[:per_page]
    next_cursor = entries[per_page][0] if has_next else None

    rows = ""
    for entry_id, fields in page_entries:
        try:
            dlq = DLQEnvelope.from_redis_fields(fields)
        except Exception:
            _log.warning("skipping corrupt DLQ entry", extra={"entry_id": entry_id})
            continue
        safe_id = html.escape(entry_id)
        fc_badge = _badge("error", dlq.failure_code)
        service = html.escape(dlq.failed_by or "?")
        attempts = html.escape(str(dlq.dlq_attempts))
        raw_payload = json.dumps(dlq.payload)
        truncated = raw_payload[:80]
        payload_preview = html.escape(truncated)
        if len(raw_payload) > 80:
            payload_preview += "..."
        orig_stream = html.escape(dlq.original_stream or "?")
        # Replay is now handled by admin-ui; show entry ID for reference
        action_cell = f'<span class="muted" title="Use admin-ui to replay">{safe_id}</span>'
        # Full envelope JSON for detail expansion
        full_json = json.dumps(dlq.payload, indent=2)
        safe_full = html.escape(full_json)
        reason = html.escape(dlq.failure_reason or "")
        rows += (
            f'<tr class="dlq-row" onclick="this.nextElementSibling.classList.toggle(\'open\')">'
            f"<td>{safe_id}</td><td>{fc_badge}</td>"
            f"<td>{orig_stream}</td><td>{service}</td><td>{attempts}</td>"
            f'<td class="cell-wrap"><code>{payload_preview}</code></td>'
            f"<td>{action_cell}</td></tr>"
            f'<tr class="dlq-detail"><td colspan="7">'
            f'<div class="dlq-detail__body">'
            f"<p><strong>{t('dlq_failure_reason')}</strong> {reason}</p>"
            f"<pre>{safe_full}</pre>"
            f"</div></td></tr>"
        )

    next_link = (
        f'<a class="page-link" href="/dlq?cursor={html.escape(next_cursor)}'
        f'&amp;per_page={per_page}">Next &rarr;</a>'
        if next_cursor
        else ""
    )
    pagination = f'<p class="pagination">{next_link}</p>' if next_link else ""

    body = f"""{halt_html}<h2>{t("page_dlq")}</h2>
{summary_html}
<p>{t("dlq_total_entries")} {total_count}.
{t("dlq_showing_per_page").replace("{n}", str(per_page))}</p>
<div class="table-scroll">
<table>
  <thead><tr><th scope="col">{t("dlq_col_entry_id")}</th>\
<th scope="col">{t("dlq_col_failure_code")}</th>
      <th scope="col">{t("dlq_col_original_stream")}</th>\
<th scope="col">{t("dlq_col_service")}</th>\
<th scope="col">{t("dlq_col_attempts")}</th>
      <th scope="col">{t("dlq_col_payload")}</th>\
<th scope="col">{t("dlq_col_action")}</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
</div>
{pagination}
"""
    return HTMLResponse(_page(t("page_dlq"), body, path="/dlq"))
