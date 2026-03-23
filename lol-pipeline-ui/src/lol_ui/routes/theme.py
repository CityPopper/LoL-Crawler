"""Theme switching route -- sets the theme cookie and redirects back."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import RedirectResponse, Response

from lol_ui.themes import SUPPORTED_THEMES, set_theme_cookie

router = APIRouter()


@router.get("/set-theme")
async def set_theme(request: Request) -> Response:
    """Set the theme cookie and redirect to the referrer (or home)."""
    theme = request.query_params.get("theme", "default")
    if theme not in SUPPORTED_THEMES:
        theme = "default"
    # Prefer explicit ref query param (set by JS), fall back to Referer header
    redirect_to = request.query_params.get("ref", "")
    if not redirect_to:
        redirect_to = request.headers.get("referer", "/")
    # Prevent open redirect: only allow relative paths (starts with "/" but not "//")
    if not redirect_to.startswith("/") or redirect_to.startswith("//"):
        redirect_to = "/"
    response = RedirectResponse(url=redirect_to, status_code=303)
    set_theme_cookie(response, theme)
    return response
