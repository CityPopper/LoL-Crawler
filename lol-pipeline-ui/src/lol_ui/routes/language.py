"""Language switching route — sets the lang cookie and redirects back."""

from __future__ import annotations

from urllib.parse import urlparse

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
    # Prefer explicit ref query param (set by language_switcher_html), fall back to Referer header
    redirect_to = request.query_params.get("ref", "")
    if not redirect_to:
        referrer = request.headers.get("referer", "/")
        # Referer header may be an absolute URL — extract path+query only
        if referrer.startswith("http"):
            parsed = urlparse(referrer)
            referrer = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        redirect_to = referrer
    # Prevent open redirect: only allow relative paths (starts with "/" but not "//")
    if not redirect_to.startswith("/") or redirect_to.startswith("//"):
        redirect_to = "/"
    response = RedirectResponse(url=redirect_to, status_code=303)
    set_lang_cookie(response, lang)
    return response
