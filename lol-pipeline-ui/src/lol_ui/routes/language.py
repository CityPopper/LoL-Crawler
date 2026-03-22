"""Language switching route — sets the lang cookie and redirects back."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import RedirectResponse, Response

from lol_ui.language import set_lang_cookie
from lol_ui.strings import SUPPORTED_LANGUAGES

router = APIRouter()


@router.get("/set-lang")
async def set_lang(request: Request) -> Response:
    """Set the language cookie and redirect to the referrer (or home)."""
    lang = request.query_params.get("lang", "en")
    if lang not in SUPPORTED_LANGUAGES:
        lang = "en"
    referrer = request.headers.get("referer", "/")
    response = RedirectResponse(url=referrer, status_code=303)
    set_lang_cookie(response, lang)
    return response
