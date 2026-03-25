import os

import uvicorn

_port = int(os.environ.get("UI_PORT", "8080"))
_host = os.environ.get("UI_HOST", "0.0.0.0")  # noqa: S104
_debug = os.environ.get("DEBUG", "").lower() in ("1", "true")

if os.environ.get("HOT_RELOAD", "").lower() in ("1", "true"):
    # Reload mode requires app as import string, not object
    _reload_dirs: list[str] | None = ["/svc/src"] if _debug else None
    uvicorn.run(
        "lol_ui.main:app",
        host=_host,
        port=_port,
        log_level="info",
        reload=True,
        reload_dirs=_reload_dirs,
    )
else:
    from lol_ui.main import app

    uvicorn.run(app, host=_host, port=_port, log_level="info")
