import os
import sys
from pathlib import Path

import uvicorn

# Ensure the project root is on sys.path
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def _get_bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    log_level = os.getenv("LOG_LEVEL", "info").lower()
    reload = _get_bool_env("RELOAD", False)
    access_log = _get_bool_env("ACCESS_LOG", True)

    workers_env = os.getenv("WORKERS")
    workers = int(workers_env) if workers_env and workers_env.isdigit() else None

    uvicorn_kwargs = {
        "app": "app.main:app",
        "host": host,
        "port": port,
        "log_level": log_level,
        "reload": reload,
        "access_log": access_log,
        "proxy_headers": True,
        "forwarded_allow_ips": "*",
    }

    # Uvicorn does not allow reload and workers together
    if workers and not reload:
        uvicorn_kwargs["workers"] = workers

    uvicorn.run(**uvicorn_kwargs)