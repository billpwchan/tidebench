import os

import uvicorn

from .config import Settings

settings = Settings()
bind_address = os.getenv("TIDEBENCH_BIND", "127.0.0.1")
if bind_address not in {"127.0.0.1", "::1", "localhost"} and len(settings.api_token) < 32:
    raise SystemExit("Set a TIDEBENCH_API_TOKEN of at least 32 characters before binding beyond loopback.")
uvicorn.run(
    "tidebench.main:app",
    host=bind_address,
    port=int(os.getenv("TIDEBENCH_PORT", "8000")),
    workers=1,
    access_log=False,
    proxy_headers=False,
)
