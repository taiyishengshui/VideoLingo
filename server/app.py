"""FastAPI application: REST control plane + WebSocket log/event stream +
static hosting of the built React frontend (frontend/dist).

Run from the project root so that config.yaml and output/ resolve:
    python -m server
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.pipeline import get_steps, pipeline_status
from server.logbus import BUS, publish_external
from server.preview_stream import close_preview_streams
from server.task_manager import MANAGER
from server.batch_api import router as batch_router
from server.settings_api import router as settings_router
from server.models_api import router as models_router
from server.translate_api import router as translate_router
from server.restart_api import router as restart_router
from server.input_api import router as input_router
from server.history_api import router as history_router
from server.bili_api import router as bili_router
from server.ad_api import router as ad_router
from server.voice_api import router as voice_router
from server.subtitle_edit_api import router as subtitle_edit_router
from server.subtitle_edit_lock import EDIT_ADMISSION_LOCK
from version import __version__, build_info

# server/app.py lives at <root>/server/app.py -> project root is 2 dirs up
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIST = os.path.join(PROJECT_ROOT, "frontend", "dist")


def install_disconnect_noise_filter(loop: asyncio.AbstractEventLoop) -> None:
    """Windows Proactor 下「浏览器主动断开预览连接」的报错噪音，静音不打屏。

    `<video>` 预览走 HTTP Range：拖进度条 / 换裁剪源 / 关页面时浏览器会直接
    掐掉上一次请求，服务端在 `_ProactorBasePipeTransport._call_connection_lost`
    回调里对已关闭的 socket 调 `shutdown()` → `ConnectionResetError
    [WinError 10054] 远程主机强迫关闭了一个现有的连接`。asyncio 把「回调里
    没人接的异常」打到 stderr，看着像崩溃，其实那次请求早就结束了、不影响
    预览/下载/裁剪。只吞这一类；其他异常仍交给原 handler / 默认处理器。
    """
    prev = loop.get_exception_handler()
    if getattr(prev, "_vl_disconnect_filter", False):
        return

    def handler(loop_, context):
        exc = context.get("exception")
        blob = f"{context.get('message', '')} {context.get('handle', '')}"
        if (isinstance(exc, (ConnectionResetError, BrokenPipeError,
                             ConnectionAbortedError))
                and "_call_connection_lost" in blob):
            return
        if prev is not None:
            prev(loop_, context)
        else:
            loop_.default_exception_handler(context)

    handler._vl_disconnect_filter = True          # type: ignore[attr-defined]
    loop.set_exception_handler(handler)


def _audio_only() -> bool:
    """Best-effort: import the heavy ytdlp module lazily; default False."""
    try:
        from core.utils.media_files import is_audio_only_input
        return bool(is_audio_only_input())
    except Exception:
        return False


class RunRequest(BaseModel):
    step_id: str | None = None      # run exactly this one step
    from_step: str | None = None    # inclusive start (default: first step)
    to_step: str | None = None      # inclusive end (default: last step)
    force: bool = False             # delete stale artifacts + LLM caches first,
                                    # so guarded steps actually rerun


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup diagnostics: bind the event loop, register the unified log
    sink, and backfill historical step timings. Failures here must never
    block the app from serving (FR-31 / timing-service resilience)."""
    BUS.bind_loop(asyncio.get_running_loop())
    # FR-31：卡片类同步操作（裁剪 / 识别广告位 / B站文案）不在任务
    # 线程里，print 只会落 cmd。把统一出口注册上，它们的日志同样进
    # 页面实时日志 + pipeline_latest.log。注册是幂等的。
    from core.utils.log_hooks import register, rprint
    register(publish_external)
    install_disconnect_noise_filter(asyncio.get_running_loop())
    try:
        from core.services import timing_service
        added = timing_service.backfill()
        if added:
            rprint(f"[green]已从历史流水线日志补录 {added} 条步骤耗时[/green]")
    except Exception as exc:  # noqa: BLE001 — diagnostics must not block startup
        rprint(f"[yellow]⚠️ 历史步骤耗时回填失败：{exc}[/yellow]")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="VideoLingo", docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/version")
    def get_version():
        return {"version": __version__, "build": build_info()}

    # ---------------- pipeline control ----------------

    @app.get("/api/pipeline")
    def get_pipeline():
        from core.services.run_service import default_run_range
        from core.services import timing_service
        audio_only = _audio_only()
        steps = timing_service.attach_last_seconds(
            pipeline_status(audio_only=audio_only))
        return {**MANAGER.snapshot(),
                "steps": steps,
                # 两个步骤下拉的默认值由服务层算（结束步骤跟随烧录开关）
                "defaults": default_run_range(audio_only=audio_only),
                "version": __version__}

    @app.get("/api/pipeline/timings")
    def get_pipeline_timings():
        """Read-only step timing report (history + step-10 breakdown)."""
        from core.services import timing_service
        return {**timing_service.summary(),
                "report": timing_service.format_report(),
                "dub": timing_service.latest_breakdown()}

    @app.post("/api/pipeline/run")
    def run_pipeline(req: RunRequest):
        # thin adapter — step selection + force-clean semantics live in
        # core/services/run_service (shared with the batch engine)
        from core.services.run_service import resolve_steps, force_clean
        try:
            step_ids = resolve_steps(req.step_id, req.from_step,
                                     audio_only=_audio_only(),
                                     to_step=req.to_step)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'"))
        except ValueError as e:
            raise HTTPException(400, str(e))
        def prepare_force_run():
            from core.utils.media_files import find_media_file
            try:
                find_media_file()
            except (ValueError, OSError) as e:
                raise HTTPException(400, f"无法强制重跑，请先确认输入文件可用且唯一：{e}")
            # force 会删 output_sub.mp4 等产物：先关掉预览读句柄，否则
            # Windows 上清理静默失败、后续步骤读到旧产物还白烧 LLM 钱
            close_preview_streams()
            force_clean(step_ids, PROJECT_ROOT)
            # Publish outside the worker so cleanup remains visible in the UI.
            titles = {s.id: s.title for s in get_steps()}
            BUS.publish({"type": "log", "line": (
                "强制重跑：已清除 " +
                "、".join(titles.get(s, s) for s in step_ids) +
                " 的产物、断点与 LLM 缓存（翻译缓存也已失效，会重新翻译）"
            )})

        with EDIT_ADMISSION_LOCK:
            ok, msg = MANAGER.start(
                step_ids, before_start=prepare_force_run if req.force else None)
        if not ok:
            raise HTTPException(409, msg)
        return {"ok": True, "message": msg, "steps": step_ids}

    @app.post("/api/pipeline/pause")
    def pause():
        MANAGER.pause()
        return MANAGER.snapshot()

    @app.post("/api/pipeline/resume")
    def resume():
        MANAGER.resume()
        return MANAGER.snapshot()

    @app.post("/api/pipeline/stop")
    def stop():
        MANAGER.stop()
        return MANAGER.snapshot()

    # ---------------- batch (multi-file) control ----------------

    app.include_router(batch_router)
    app.include_router(settings_router)
    app.include_router(models_router)
    app.include_router(translate_router)
    app.include_router(restart_router)
    app.include_router(input_router)
    app.include_router(history_router)
    app.include_router(bili_router)
    app.include_router(ad_router)
    app.include_router(voice_router)
    app.include_router(subtitle_edit_router)

    # ---------------- per-step log files (whitelist: output/ only) ----------------

    @app.get("/api/logs/tail")
    def tail_log(path: str, lines: int = 200):
        abs_path = os.path.abspath(os.path.join(PROJECT_ROOT, path))
        if not abs_path.startswith(os.path.join(PROJECT_ROOT, "output")):
            raise HTTPException(403, "only files under output/ are readable")
        if not os.path.isfile(abs_path):
            return {"path": path, "lines": []}
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.readlines()
        return {"path": path, "lines": [l.rstrip("\n") for l in content[-lines:]]}

    # ---------------- websocket: log + event stream ----------------

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        queue = BUS.subscribe()
        try:
            # late joiners get the recent history first
            for event in BUS.snapshot():
                await websocket.send_text(json.dumps(event, ensure_ascii=False))
            while True:
                event = await queue.get()
                await websocket.send_text(json.dumps(event, ensure_ascii=False))
        except WebSocketDisconnect:
            pass
        finally:
            BUS.unsubscribe(queue)

    # ---------------- static frontend (production build) ----------------

    if os.path.isdir(FRONTEND_DIST):
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    else:
        @app.get("/")
        def no_frontend():
            return {
                "status": "backend up",
                "hint": "frontend/dist not found — run `npm install && npm run build` in frontend/, "
                        "or use the vite dev server on :5173",
            }

    return app


app = create_app()
