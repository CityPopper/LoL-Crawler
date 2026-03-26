"""Entry point for ``python -m lol_admin_ui``."""

import os

import uvicorn

_port = int(os.environ.get("ADMIN_UI_PORT", "8081"))
_host = os.environ.get("ADMIN_UI_HOST", "0.0.0.0")  # noqa: S104
_debug = os.environ.get("DEBUG", "").lower() in ("1", "true")

if os.environ.get("HOT_RELOAD", "").lower() in ("1", "true"):
    _reload_dirs: list[str] | None = ["/svc/src"] if _debug else None
    uvicorn.run(
        "lol_admin_ui.main:app",
        host=_host,
        port=_port,
        log_level="info",
        reload=True,
        reload_dirs=_reload_dirs,
    )
else:
    from lol_admin_ui.main import app

    uvicorn.run(app, host=_host, port=_port, log_level="info")
