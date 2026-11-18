"""Input media endpoints — the complement to `core/pipeline`.

The registry deliberately excludes the download step (see the note in
`core/pipeline.py`): it takes user input and therefore belongs to the input
bar, not the processing pipeline. These endpoints are that input bar's
HTTP surface.

Thin adapter only: URL / resolution validation + HTTP wrapping. The actual
download lives in `core._1_ytdlp.download_video_ytdlp`, input discovery in
`core.utils.media_files`.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Body, HTTPException, Query

from core.utils.media_files import (
    INPUT_MANIFEST,
    _safe_find_audio_file,
    _safe_find_video_file,
    find_media_file,
)

router = APIRouter(prefix="/api/input", tags=["input"])

_SAVE_PATH = "output"
# yt-dlp also handles Bilibili / X / Vimeo / ... so this is intentionally a
# loose sanity check, not a YouTube-only whitelist.
_URL_PREFIXES = ("http://", "https://")
_ALLOWED_RESOLUTIONS = ("360", "480", "720", "1080", "best")


@router.get("/current")
def current_input():
    """What will the pipeline treat as its input media?"""
    manifest_path = os.path.join(_SAVE_PATH, INPUT_MANIFEST)
    path = None
    media_type = None
    try:
        path, media_type = find_media_file(_SAVE_PATH)
    except Exception as e:
        # Nothing usable yet, or an unreadable/ambiguous input dir — report
        # it instead of letting UI polling blow up on a 500.
        # Two very different cases: an empty dir is normal; >1 candidate
        # media file is an error the user must resolve.
        msg = str(e) or "尚未选择输入文件"
        return {
            "path": None,
            "type": None,
            "exists": False,
            "ambiguous": "not unique" in msg,
            "manifest": os.path.exists(manifest_path),
            "video": None,
            "audio": None,
            "message": msg,
        }

    # `_safe_*` never raises on "found 0"; anything else (e.g. not-unique)
    # is reported through `ambiguous` above, so stay defensive here.
    video = audio = None
    try:
        video = _safe_find_video_file(_SAVE_PATH)
    except Exception:
        video = None
    try:
        audio = _safe_find_audio_file(_SAVE_PATH)
    except Exception:
        audio = None

    return {
        "path": path,
        "type": media_type,
        "exists": bool(path and os.path.exists(path)),
        "ambiguous": False,
        "manifest": os.path.exists(manifest_path),
        "video": video,
        "audio": audio,
        "message": None if path else "尚未选择输入文件",
    }


@router.get("/cookies")
def cookies_status():
    """Cookie 体检：下载前就能看出会不会撞上 YouTube 的 bot 挑战。"""
    from core._1_ytdlp import probe_cookies

    try:
        return probe_cookies()
    except Exception as e:                                        # noqa: BLE001
        # 诊断接口绝不能因为读取失败而让 UI 报错
        return {"verdict": "unknown", "hint": f"{type(e).__name__}: {e}"}


@router.post("/download")
def download_video(payload: dict = Body(...)):
    """Start a download task (async, logs stream over the existing WebSocket)."""
    url = str(payload.get("url") or "").strip()
    resolution = str(payload.get("resolution") or "1080").strip()

    if not url or not url.lower().startswith(_URL_PREFIXES):
        raise HTTPException(400, "请填写以 http:// 或 https:// 开头的视频链接")
    if resolution not in _ALLOWED_RESOLUTIONS:
        raise HTTPException(400, f"不支持的分辨率: {resolution}")

    # Fail fast with a clear UI message: find_video_files() requires the input
    # media to be unique, so downloading on top of an existing file would only
    # break later with an opaque error.
    try:
        existing, _ = find_media_file(_SAVE_PATH)
    except Exception:
        existing = None
    if existing:
        raise HTTPException(
            409,
            f"输入目录里已有 {os.path.basename(existing)}，请先删除旧输入再下载新视频",
        )

    from core._1_ytdlp import download_video_ytdlp
    from server.task_manager import MANAGER
    from server.subtitle_edit_lock import EDIT_ADMISSION_LOCK

    def job():
        download_video_ytdlp(url, save_path=_SAVE_PATH, resolution=resolution)

    with EDIT_ADMISSION_LOCK:
        ok, m = MANAGER.start_job("下载视频", job)
    if not ok:
        raise HTTPException(409, m)
    return {"ok": True, "message": m}


@router.delete("")
def clear_input(remove_file: bool = Query(False)):
    """Forget the current input. With remove_file=true also delete the media."""
    removed = []
    manifest_path = os.path.join(_SAVE_PATH, INPUT_MANIFEST)
    if os.path.exists(manifest_path):
        os.remove(manifest_path)
        removed.append(INPUT_MANIFEST)

    if remove_file:
        try:
            path, _ = find_media_file(_SAVE_PATH)
        except Exception:
            path = None
        if path and os.path.isabs(path) is False and path.startswith("output/"):
            if os.path.exists(path):
                os.remove(path)
                removed.append(path)

    return {"ok": True, "removed": removed}
