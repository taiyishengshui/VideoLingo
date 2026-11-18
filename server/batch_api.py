"""Batch REST API: scan / start / rerun / stop / status / browse.

Endpoints (all JSON):
- POST /api/batch/scan   {root}                      -> per-file rows
  `root` may be a DIRECTORY (recursive scan) or a single media FILE
  (batch of one — the workspace goes next to that file).
- POST /api/batch/browse {path}                      -> directory listing
  for the frontend file picker (dirs + media files, skips *_log).
- POST /api/batch/start  {files, force_restart?, reset_from?, reset_to?}
- POST /api/batch/stop
- GET  /api/batch/status                             -> engine snapshot
- GET  /api/batch/log?path=...                       -> tail a workspace log

Batch events ride the existing /ws LogBus (type: batch_state / job_start /
job_step / job_done / job_error / log).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.batch_engine import ENGINE
from core.utils.job_context import LOG_SUFFIX, batch_log_root
from server.logbus import BUS
from server.subtitle_edit_lock import EDIT_ADMISSION_LOCK

# Re-bind the module-level engine's publisher to the LogBus so batch events
# stream to the UI over the same /ws connection as pipeline logs.
ENGINE._publish = BUS.publish

router = APIRouter(prefix="/api/batch", tags=["batch"])


class ScanRequest(BaseModel):
    root: str


class BrowseRequest(BaseModel):
    path: str | None = None  # None -> list drives (Windows) / root


class StartRequest(BaseModel):
    files: list[str]
    force_restart: bool = False
    reset_from: str | None = None
    reset_to: str | None = None


def _media_exts() -> tuple[set, set]:
    from core.utils.config_utils import load_key
    try:
        return set(load_key("allowed_video_formats")), set(load_key("allowed_audio_formats"))
    except Exception:
        return ({"mp4", "mkv", "mov", "avi", "webm", "flv", "m4v"},
                {"mp3", "wav", "m4a", "aac", "flac", "ogg"})


@router.post("/scan")
def scan(req: ScanRequest):
    root = os.path.abspath(req.root)
    with EDIT_ADMISSION_LOCK:
        if ENGINE.state in ("running", "stopping"):
            raise HTTPException(409, "batch is busy; cannot replace the scan")
        if os.path.isfile(root):
            # single-file batch: the "root" is the file's parent dir and the
            # scan contains exactly this one file
            from core.utils.job_context import Job
            parent = os.path.dirname(root)
            job = Job(parent, root)
            with ENGINE._lock:
                ENGINE.root = parent
                ENGINE.jobs = [job]
            job.state.init_steps(ENGINE._step_ids())
            return {"root": root, "log_root": batch_log_root(parent),
                    "files": [ENGINE._file_row(job)], "state": ENGINE.state}
        if not os.path.isdir(root):
            raise HTTPException(400, f"not a directory or media file: {root}")
        try:
            rows = ENGINE.scan(root)
        except Exception as e:
            raise HTTPException(500, f"scan failed: {e}")
        return {"root": root, "log_root": batch_log_root(root), "files": rows,
                "state": ENGINE.state}


@router.post("/browse")
def browse(req: BrowseRequest):
    """Directory listing for the frontend picker: child directories and
    media files only. path=None lists available drives (Windows) or /."""
    if req.path is None:
        if os.name == "nt":
            import string
            drives = []
            for letter in string.ascii_uppercase:
                drive = f"{letter}:\\"
                if os.path.exists(drive):
                    drives.append({"name": f"{letter}:", "path": f"{letter}:\\",
                                   "type": "dir"})
            return {"path": None, "dirs": drives, "files": []}
        return {"path": "/", "dirs": [{"name": "/", "path": "/", "type": "dir"}],
                "files": []}

    p = os.path.abspath(req.path)
    if not os.path.isdir(p):
        raise HTTPException(400, f"not a directory: {p}")

    video_exts, audio_exts = _media_exts()
    dirs, files = [], []
    try:
        for name in sorted(os.listdir(p)):
            full = os.path.join(p, name)
            if os.path.isdir(full):
                is_log = name.endswith(LOG_SUFFIX)
                dirs.append({"name": name + ("/" if is_log else ""),
                             "path": full.replace("\\", "/"),
                             "type": "dir", "log": is_log})
            else:
                ext = os.path.splitext(name)[1][1:].lower()
                if ext in video_exts:
                    files.append({"name": name, "path": full.replace("\\", "/"),
                                  "type": "video"})
                elif ext in audio_exts:
                    files.append({"name": name, "path": full.replace("\\", "/"),
                                  "type": "audio"})
    except PermissionError:
        raise HTTPException(403, f"no permission to list: {p}")

    parent = os.path.dirname(p.rstrip("\\/"))
    return {"path": p.replace("\\", "/"),
            "parent": (parent.replace("\\", "/") or None) if parent != p else None,
            "dirs": dirs, "files": files}


@router.post("/start")
def start(req: StartRequest):
    if not req.files:
        raise HTTPException(400, "no files selected")
    if req.reset_from:
        from core.pipeline import get_steps
        valid = [s.id for s in get_steps()]
        for sid in (req.reset_from, req.reset_to):
            if sid is not None and sid not in valid:
                raise HTTPException(404, f"unknown step: {sid}")
    with EDIT_ADMISSION_LOCK:
        ok, msg = ENGINE.start(
            req.files, force_restart=req.force_restart,
            reset_from=req.reset_from, reset_to=req.reset_to)
    if not ok:
        raise HTTPException(409, msg)
    return {"ok": True, "message": msg}


@router.post("/stop")
def stop():
    ENGINE.stop()
    return {"ok": True, "state": ENGINE.state}


@router.get("/status")
def status():
    return ENGINE.snapshot()


@router.get("/log")
def tail_log(path: str, lines: int = 200):
    """Tail a log file inside a batch workspace (a *_log tree). Security:
    the path must resolve under the scanned batch log root."""
    if not ENGINE.root:
        raise HTTPException(409, "no batch scanned yet")
    log_root = os.path.abspath(batch_log_root(ENGINE.root))
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(log_root):
        raise HTTPException(403, "only files inside the batch log root are readable")
    if not os.path.isfile(abs_path):
        return {"path": path, "lines": []}
    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.readlines()
    return {"path": path, "lines": [l.rstrip("\n") for l in content[-lines:]]}


# ---------------- artifacts browser (project output/ + batch workspaces) ----

# server/batch_api.py lives at <root>/server/batch_api.py -> root is 2 dirs
# up (D:\code\vl). The previous triple dirname resolved to D:\code and made
# the artifacts roots point at the non-existent D:\code\output.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@router.post("/artifacts/browse")
def artifacts_browse(req: BrowseRequest):
    """Browse artifacts: path=None -> roots list (project output/, history/
    and the scanned batch log root); otherwise a directory listing."""
    if req.path is None:
        roots = [{
            "name": "output/ (单任务产物)",
            "path": os.path.join(PROJECT_ROOT, "output").replace("\\", "/"),
            "type": "dir",
        }]
        hist = os.path.join(PROJECT_ROOT, "history")
        if os.path.isdir(hist):
            roots.append({"name": "history/ (归档)", "path": hist.replace("\\", "/"),
                          "type": "dir"})
        if ENGINE.root:
            lr = batch_log_root(ENGINE.root)
            if os.path.isdir(lr):
                roots.append({"name": f"批量工作区 ({os.path.basename(lr)})",
                              "path": lr, "type": "dir"})
        return {"path": None, "dirs": roots, "files": []}

    p = os.path.abspath(req.path)
    # security: only the three roots above are browsable
    allowed = [os.path.join(PROJECT_ROOT, "output"),
               os.path.join(PROJECT_ROOT, "history")]
    if ENGINE.root:
        allowed.append(os.path.abspath(batch_log_root(ENGINE.root)))
    if not any(p.startswith(a) for a in allowed):
        raise HTTPException(403, "outside browsable roots")

    if not os.path.isdir(p):
        raise HTTPException(400, f"not a directory: {p}")

    dirs, files = [], []
    try:
        for name in sorted(os.listdir(p)):
            full = os.path.join(p, name)
            if os.path.isdir(full):
                dirs.append({"name": name, "path": full.replace("\\", "/"), "type": "dir"})
            else:
                size = os.path.getsize(full)
                files.append({"name": name, "path": full.replace("\\", "/"),
                              "type": "file", "size": size})
    except PermissionError:
        raise HTTPException(403, f"no permission to list: {p}")

    parent = os.path.dirname(p.rstrip("\\/"))
    return {"path": p.replace("\\", "/"),
            "parent": (parent.replace("\\", "/") or None) if parent != p else None,
            "dirs": dirs, "files": files}


@router.get("/artifacts/download")
def artifacts_download(path: str):
    """Stream a file from the browsable roots as an attachment."""
    from fastapi.responses import FileResponse
    abs_path = os.path.abspath(path)
    allowed = [os.path.join(PROJECT_ROOT, "output"),
               os.path.join(PROJECT_ROOT, "history")]
    if ENGINE.root:
        allowed.append(os.path.abspath(batch_log_root(ENGINE.root)))
    if not any(abs_path.startswith(a) for a in allowed):
        raise HTTPException(403, "outside browsable roots")
    if not os.path.isfile(abs_path):
        raise HTTPException(404, f"file not found: {path}")
    return FileResponse(abs_path, filename=os.path.basename(abs_path))
