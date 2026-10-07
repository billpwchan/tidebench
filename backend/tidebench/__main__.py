import os

import uvicorn

from .config import Settings

settings = Settings()
bind_address = os.getenv("TIDEBENCH_BIND", "127.0.0.1")
if bind_address not in {"127.0.0.1", "::1", "localhost"}:
    if not settings.auth_enabled or max(len(settings.api_token), len(settings.bootstrap_token)) < 32:
        raise SystemExit(
            "Remote binding requires authentication and a random API or bootstrap token of at least 32 characters."
        )
uvicorn.run(
    "tidebench.main:app",
    host=bind_address,
    port=int(os.getenv("TIDEBENCH_PORT", "8000")),
    workers=1,
    access_log=False,
    proxy_headers=False,
)
