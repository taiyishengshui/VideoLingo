"""Self-restart for the server (user-requested convenience).

Why: iterating on this project involves frequent backend .py edits; asking
the user to find the terminal and CTRL+C + rerun every time is friction.
POST /api/restart spawns a supervisor thread that (after the HTTP response
is flushed) replaces the whole process via os.execv — same interpreter,
same argv (VL_HOST/VL_PORT env preserved), fresh imports.

Safety:
- refuses while a single-task pipeline run or a batch is active
- one-shot guard: a second restart request during the grace window is 409
"""

from __future__ import annotations

import os
import sys
import threading
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["restart"])

_RESTARTING = {"flag": False, "at": 0.0}
_GRACE = 1.5  # seconds to let the HTTP response flush


def _anything_running() -> str | None:
    """A short description of what's blocking, or None if safe."""
    try:
        from server.task_manager import MANAGER
        if MANAGER.state in ("running", "paused"):
            return f"pipeline task is {MANAGER.state}"
    except Exception:
        pass
    try:
        from core.batch_engine import ENGINE
        if ENGINE.state in ("running", "stopping"):
            return f"batch is {ENGINE.state}"
    except Exception:
        pass
    return None


def _restart_process() -> None:
    time.sleep(_GRACE)
    # flush uvicorn's access/error logs before replacing the process
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    print("♻️ restarting server (self-exec) ...", flush=True)
    os.execv(sys.executable, [sys.executable] + sys.argv)


class _Empty(BaseModel):
    pass


@router.post("/restart")
def restart_server():
    if _RESTARTING["flag"]:
        raise HTTPException(409, "restart already in progress")
    busy = _anything_running()
    if busy:
        raise HTTPException(409, f"refusing to restart: {busy}")
    _RESTARTING["flag"] = True
    _RESTARTING["at"] = time.time()
    threading.Thread(target=_restart_process, daemon=True).start()
    return {"ok": True, "message": "restarting in ~1.5s; auto-refresh the page"}


@router.get("/restart-status")
def restart_status():
    return {"restarting": _RESTARTING["flag"], "at": _RESTARTING["at"] or None}


@router.post("/stop")
def stop_server():
    """Hard-stop the server from the UI. Needed because after a self-restart
    (os.execv) on Windows the new process is detached from the launching
    shell — CTRL+C in that terminal no longer reaches it."""
    if _RESTARTING["flag"]:
        raise HTTPException(409, "restart in progress")
    busy = _anything_running()
    if busy:
        raise HTTPException(409, f"refusing to stop: {busy}（先停止任务再关服务）")

    import signal

    def _stop_process() -> None:
        time.sleep(1.0)  # let the HTTP response flush
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        print("🛑 stopping server (user requested) ...", flush=True)
        try:
            # graceful on POSIX (uvicorn SIGTERM handler); on Windows this
            # maps to TerminateProcess — fine, we already checked nothing
            # is running
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(0)
        # if SIGTERM somehow didn't kill us within 3s, force it
        time.sleep(3)
        os._exit(0)

    threading.Thread(target=_stop_process, daemon=True).start()
    return {"ok": True, "message": "stopping in ~1s"}
