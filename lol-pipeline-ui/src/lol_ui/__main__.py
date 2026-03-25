import os

import uvicorn

_port = int(os.environ.get("UI_PORT", "8080"))

if os.environ.get("HOT_RELOAD", "").lower() in ("1", "true"):
    # Reload mode requires app as import string, not object
    uvicorn.run(
        "lol_ui.main:app",
        host="0.0.0.0",  # noqa: S104
        port=_port,
        log_level="info",
        reload=True,
        reload_dirs=["/svc/src"],
    )
else:
    from lol_ui.main import app

    uvicorn.run(app, host="0.0.0.0", port=_port, log_level="info")  # noqa: S104
