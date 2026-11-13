"""Launcher: `python -m server` from the project root.

Serves API + WebSocket + (built) frontend on one port. Default
http://127.0.0.1:8000 — override with env VL_PORT / VL_HOST.
"""

import os
import sys


def _configure_utf8_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main():
    _configure_utf8_console()
    # ensure the project root is importable regardless of invocation dir
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)

    import uvicorn

    host = os.environ.get("VL_HOST", "127.0.0.1")
    port = int(os.environ.get("VL_PORT", "8000"))
    # dev auto-reload: any .py change restarts the server automatically.
    # NOTE: the web UI's restart button (POST /api/restart) is INDEPENDENT
    # of this — it works in both modes.
    reload_mode = os.environ.get("VL_RELOAD", "").strip() in ("1", "true", "yes")
    print(f"VideoLingo server → http://{host}:{port}"
          + ("  (auto-reload ON)" if reload_mode else ""))
    uvicorn.run("server.app:app", host=host, port=port, log_level="info",
                reload=reload_mode)


if __name__ == "__main__":
    main()
