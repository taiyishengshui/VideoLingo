"""Controlled subtitle editing and original-media preview endpoints."""

from __future__ import annotations

import mimetypes
import os
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.services import subtitle_edit_service as service
from core.utils.job_context import Job
from core.utils.media_files import find_media_file
from core.utils.media_probe import probe_media
from server.preview_stream import PreviewFileResponse
from server.subtitle_edit_lock import EDIT_ADMISSION_LOCK

router = APIRouter(prefix="/api/subtitles", tags=["subtitles"])


class SaveRequest(BaseModel):
    revision: str
    rows: list[dict]


def _ensure_idle() -> None:
    from server.task_manager import MANAGER
    from core.batch_engine import ENGINE
    if MANAGER.state in ("running", "paused") or ENGINE.state in ("running", "stopping"):
        raise HTTPException(409, "pipeline is busy; subtitle editing is unavailable")


def _same_path(left: str, right: str) -> bool:
    try:
        return (os.path.normcase(os.path.realpath(left))
                == os.path.normcase(os.path.realpath(right)))
    except OSError:
        return False


def _resolve_context(scope: str, video: str | None) -> tuple[str, str]:
    from core.batch_engine import ENGINE
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if scope == "single":
        if video is not None:
            raise HTTPException(400, "single workspace does not accept a video path")
        workspace = project_root
        try:
            source, media_type = find_media_file(os.path.join(workspace, "output"))
        except (ValueError, OSError) as exc:
            raise HTTPException(404, f"single-task input unavailable: {exc}") from exc
        if media_type != "video":
            raise HTTPException(400, "subtitle video preview requires a video input")
        return workspace, os.path.abspath(source)
    if scope != "batch" or not video:
        raise HTTPException(400, "scope must be single or batch; batch requires a scanned video id")
    with ENGINE._lock:
        job = next((item for item in ENGINE.jobs
                    if _same_path(item.video_path, video)), None)
        if job is None:
            raise HTTPException(404, "video is not part of the current batch scan")
        workspace, source = job.workspace, job.video_path
    if not os.path.isfile(source):
        raise HTTPException(404, "source video is unavailable")
    return workspace, source


def _media_info(path: str) -> tuple[float, float, str]:
    info = probe_media(path)
    duration = float(info.get("duration") or 0)
    fps = float(info.get("fps") or 0)
    if fps > 0:
        return (duration if duration > 0 else 0.0), fps, "auto"
    return (duration if duration > 0 else 0.0), 30.0, "fallback"


def _with_context(scope: str, video: str | None):
    _ensure_idle()
    workspace, source = _resolve_context(scope, video)
    if not os.path.isfile(source):
        raise HTTPException(404, "source video is unavailable")
    duration, fps, fps_source = _media_info(source)
    if duration <= 0:
        raise HTTPException(422, "could not determine source video duration")
    return workspace, source, duration, fps, fps_source


@router.get("/context")
def get_context(scope: str = "single", video: str | None = None):
    with EDIT_ADMISSION_LOCK:
        workspace, source, duration, fps, fps_source = _with_context(scope, video)
        tracks = {}
        for name in ("src.srt", "trans.srt"):
            try:
                tracks[name] = service.read_track(workspace, name, duration)
            except FileNotFoundError:
                tracks[name] = None
            except ValueError as exc:
                raise HTTPException(422, f"{name}: {exc}") from exc
        params = {"scope": scope}
        if video:
            params["video"] = video
        return {"scope": scope, "video": video, "duration": duration,
                "fps": fps, "fps_source": fps_source,
                "preview_url": f"/api/subtitles/preview?{urlencode(params)}",
                "tracks": tracks}


@router.put("/{name}")
def put_track(name: str, req: SaveRequest, scope: str = "single",
              video: str | None = None):
    with EDIT_ADMISSION_LOCK:
        workspace, source, duration, _, _ = _with_context(scope, video)
        try:
            return service.save_track(workspace, name, req.revision, req.rows, duration)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"atomic subtitle save failed: {exc}") from exc


@router.api_route("/preview", methods=["GET", "HEAD"])
def preview(scope: str = "single", video: str | None = None):
    with EDIT_ADMISSION_LOCK:
        _ensure_idle()
        _, source, _, _, _ = _with_context(scope, video)
    media_type = mimetypes.guess_type(source)[0] or "application/octet-stream"
    return PreviewFileResponse(source, media_type=media_type)
