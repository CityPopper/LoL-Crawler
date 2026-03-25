"""Language detection and switcher rendering helpers."""

from __future__ import annotations

import contextvars

from lol_ui._helpers import get_lang, set_lang_cookie
from lol_ui.strings import SUPPORTED_LANGUAGES

# Re-export for backwards compatibility
__all__ = [
    "_current_lang",
    "get_lang",
    "language_switcher_html",
    "set_lang_cookie",
]

_DEFAULT_LANG = "en"

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
