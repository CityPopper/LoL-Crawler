import os

import uvicorn

if os.environ.get("HOT_RELOAD", "").lower() in ("1", "true"):
    # Reload mode requires app as import string, not object
    uvicorn.run(
        "lol_ui.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8080,
        log_level="info",
        reload=True,
        reload_dirs=["/svc/src"],
    )
else:
    from lol_ui.main import app

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")  # noqa: S104
