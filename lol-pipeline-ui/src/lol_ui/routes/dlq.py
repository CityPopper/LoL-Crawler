"""DLQ routes — GET /dlq, POST /dlq/replay/{entry_id}."""

from __future__ import annotations

import html
import json
from urllib.parse import quote as _url_quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from lol_pipeline.config import Config
from lol_pipeline.constants import VALID_REPLAY_STREAMS
from lol_pipeline.helpers import is_system_halted
from lol_pipeline.log import get_logger
from lol_pipeline.models import DLQEnvelope
from lol_pipeline.streams import replay_from_dlq
from starlette.responses import Response

from lol_ui.constants import (
    _DLQ_DEFAULT_PER_PAGE,
    _DLQ_MAX_PER_PAGE,
    _HALT_BANNER,
    _STREAM_ENTRY_ID_RE,
)
from lol_ui.dlq_helpers import _dlq_summary_html, _make_replay_envelope
from lol_ui.rendering import _badge, _empty_state, _page
from lol_ui.strings import t

_log = get_logger("ui")

router = APIRouter()


@router.get("/dlq", response_class=HTMLResponse)
async def show_dlq(request: Request) -> HTMLResponse:
    """Display dead-letter queue entries with cursor-based pagination."""
    r = request.app.state.r
    halted = await is_system_halted(r)
    halt_html = _HALT_BANNER if halted else ""
    summary_html = await _dlq_summary_html(r)
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

    total_count: int = await r.xlen("stream:dlq")

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
        replay_form = (
            f'<form method="post" action="/dlq/replay/{_url_quote(entry_id)}"'
            f' style="display:inline">'
            f'<button type="submit" class="btn-sm"'
            f' aria-label="{t("dlq_replay")} {safe_id}">'
            f"{t('dlq_replay')}</button></form>"
        )
        # Full envelope JSON for detail expansion
        full_json = json.dumps(dlq.payload, indent=2)
        safe_full = html.escape(full_json)
        reason = html.escape(dlq.failure_reason or "")
        rows += (
            f'<tr class="dlq-row" onclick="this.nextElementSibling.classList.toggle(\'open\')">'
            f"<td>{safe_id}</td><td>{fc_badge}</td>"
            f"<td>{orig_stream}</td><td>{service}</td><td>{attempts}</td>"
            f'<td class="cell-wrap"><code>{payload_preview}</code></td>'
            f"<td>{replay_form}</td></tr>"
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
    pagination = (
        f'<p style="display:flex;gap:var(--space-md);align-items:center">{next_link}</p>'
        if next_link
        else ""
    )

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


@router.post("/dlq/replay/{entry_id:path}")
async def dlq_replay(request: Request, entry_id: str) -> Response:
    """Replay a single DLQ entry back to its original stream."""
    if not _STREAM_ENTRY_ID_RE.match(entry_id):
        raise HTTPException(status_code=400, detail="Invalid entry ID format")
    r = request.app.state.r
    cfg: Config = request.app.state.cfg
    entries: list[tuple[str, dict[str, str]]] = await r.xrange(
        "stream:dlq", min=entry_id, max=entry_id, count=1
    )
    if not entries:
        safe_id = html.escape(entry_id)
        body = (
            f"<h2>{t('page_dlq_replay_failed')}</h2>"
            f'<div class="banner banner--error">{safe_id} {t("dlq_entry_not_found")}</div>'
            f'<p><a href="/dlq">&larr; {t("dlq_back")}</a></p>'
        )
        return HTMLResponse(_page(t("page_dlq_replay_failed"), body, path="/dlq"), status_code=404)
    _eid, fields = entries[0]
    try:
        dlq = DLQEnvelope.from_redis_fields(fields)
    except Exception:
        _log.warning("corrupt DLQ entry during replay", extra={"entry_id": entry_id})
        safe_id = html.escape(entry_id)
        body = (
            f"<h2>{t('page_dlq_replay_failed')}</h2>"
            f'<div class="banner banner--error">{safe_id} {t("dlq_entry_corrupt")}'
            f" {t('dlq_remove_hint')}"
            f" <code>just admin dlq clear --all</code>.</div>"
            f'<p><a href="/dlq">&larr; {t("dlq_back")}</a></p>'
        )
        return HTMLResponse(_page(t("page_dlq_replay_failed"), body, path="/dlq"), status_code=422)
    if dlq.original_stream not in VALID_REPLAY_STREAMS:
        safe_id = html.escape(entry_id)
        safe_stream = html.escape(dlq.original_stream)
        body = (
            f"<h2>{t('page_dlq_replay_failed')}</h2>"
            f'<div class="banner banner--error">{safe_id} {t("dlq_invalid_stream")}'
            f" <code>{safe_stream}</code> \u2014 {t('dlq_replay_refused')}"
            f" {t('dlq_remove_hint')}"
            f" <code>just admin dlq clear --all</code>.</div>"
            f'<p><a href="/dlq">&larr; {t("dlq_back")}</a></p>'
        )
        return HTMLResponse(_page(t("page_dlq_replay_failed"), body, path="/dlq"), status_code=422)
    envelope = _make_replay_envelope(dlq, cfg.max_attempts)
    await replay_from_dlq(r, entry_id, dlq.original_stream, envelope)
    return RedirectResponse("/dlq", status_code=303)
