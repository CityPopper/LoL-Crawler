"""Language detection and switcher rendering helpers."""

from __future__ import annotations

import contextvars
from typing import Any

from lol_ui.strings import SUPPORTED_LANGUAGES

_DEFAULT_LANG = "en"
_COOKIE_NAME = "lang"
_COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 year

# Context variable holding the active language for the current request.
# Set by middleware; read by t(), t_raw(), and _page() so call sites
# do not need to pass lang explicitly.
_current_lang: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_lang", default=_DEFAULT_LANG
)

_LANG_LABELS: dict[str, str] = {
    "en": "EN",
    "zh-CN": "\u4e2d\u6587",
}


def get_lang(request: Any) -> str:
    """Resolve the active language from request cookie or Accept-Language header.

    Priority: ``lang`` cookie > ``Accept-Language`` header > ``"en"`` default.
    """
    cookie_val: str = request.cookies.get(_COOKIE_NAME, "")
    if cookie_val in SUPPORTED_LANGUAGES:
        return cookie_val

    accept: str = request.headers.get("accept-language", "")
    for token in accept.split(","):
        tag = token.split(";")[0].strip()
        if tag in SUPPORTED_LANGUAGES:
            return tag
        # Match base language (e.g. "zh" -> "zh-CN")
        base = tag.split("-")[0]
        for supported in SUPPORTED_LANGUAGES:
            if supported.split("-")[0] == base and supported != _DEFAULT_LANG:
                return supported

    return _DEFAULT_LANG


def set_lang_cookie(response: Any, lang: str) -> None:
    """Set the ``lang`` cookie on *response*."""
    safe_lang = lang if lang in SUPPORTED_LANGUAGES else _DEFAULT_LANG
    response.set_cookie(
        key=_COOKIE_NAME,
        value=safe_lang,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def language_switcher_html(current_lang: str, path: str = "") -> str:
    """Render a compact language switcher (EN | 中文) as HTML links.

    *path* is the current page path (e.g. ``/players``).  When provided it is
    appended as ``&ref=<path>`` so the redirect lands back on the same page
    rather than following the ``Referer`` header (which may be an absolute URL
    that the safety check in ``/set-lang`` would reject).
    """
    from urllib.parse import quote

    parts: list[str] = []
    for lang_code in SUPPORTED_LANGUAGES:
        label = _LANG_LABELS.get(lang_code, lang_code)
        if lang_code == current_lang:
            parts.append(f'<span style="font-weight:700;color:var(--color-text)">{label}</span>')
        else:
            href = f"/set-lang?lang={lang_code}"
            if path:
                href += f"&ref={quote(path, safe='/?&=')}"
            parts.append(
                f'<a href="{href}"'
                f' style="color:var(--color-muted);text-decoration:none">{label}</a>'
            )
    inner = ' <span style="color:var(--color-muted)">|</span> '.join(parts)
    return (
        f'<div class="lang-switcher" style="float:right;'
        f'font-size:var(--font-size-sm);margin-top:4px">{inner}</div>'
    )
