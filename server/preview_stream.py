"""视频预览的 HTTP Range 响应 —— 读句柄必须由后端主动可控地关闭。

为什么不能直接用 Starlette 的 `FileResponse`（v1.14~v1.19 的残留根因）：

* `<video>` 拖进度条 / 卸载时浏览器直接掐断 Range 请求，uvicorn 发
  `http.disconnect`，Starlette 会 cancel 掉流式任务；
* `FileResponse._handle_single_range` 用 `async with anyio.open_file(...)`，
  退出时的 `aclose()` 本身是 `await`，在**已被取消的 scope 里会被立刻取消**，
  于是 Windows 上留一个没有 `FILE_SHARE_DELETE` 的读句柄；
* Windows 不允许删除/改名仍被打开的文件 → 归档 `shutil.move` 报
  `PermissionError`，`output_sub.mp4` 永远清不出 output/。

这里的三条不变式：

1. 文件句柄在 `finally` 里 **同步** `f.close()`（不 await，不受取消影响）；
2. 所有活跃句柄登记到模块级注册表，`close_preview_streams()` 可从归档 /
   force 重跑 / 显式 release 的线程里主动关掉它们（应对 `await send` 阻塞、
   `finally` 还没跑到的情况）；
3. 被主动关掉后正在读的流安静结束（浏览器本来就在断开），不抛 500。

只支持单段 Range（浏览器预览只用单段）；多段/畸形 Range 退化为全量或 416。
"""

from __future__ import annotations

import os
import threading
from email.utils import formatdate
from typing import BinaryIO

import anyio
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse, Response

_CHUNK_SIZE = 64 * 1024

_registry_lock = threading.Lock()
_open_files: set[BinaryIO] = set()
_generation = 0      # 每次释放递增：拦住「open 完还没登记就被释放」的竞态


class _RangeError(Exception):
    def __init__(self, status: int, content_range: str | None = None):
        super().__init__(f"unusable range: {status}")
        self.status = status
        self.content_range = content_range


def _parse_range(value: str, size: int) -> tuple[int, int] | None:
    """`bytes=start-end` → [start, end)（end 独占）；None = 忽略 Range 回全量。

    `bytes=-N` 取尾部 N 字节；越界起点/空段 → 416。浏览器不会发多段 Range，
    多段直接忽略而不是 500。
    """
    if not value.lower().startswith("bytes="):
        return None
    spec = value[6:].strip()
    if "," in spec:                       # multipart ranges：回全量，够用且简单
        return None
    if "-" not in spec:
        raise _RangeError(416, f"bytes */{size}")
    first, last = spec.split("-", 1)
    if first:
        try:
            start = int(first)
            end = min(int(last) + 1, size) if last else size
        except ValueError:
            return None
    else:
        try:
            suffix = int(last)
        except ValueError:
            return None
        if suffix <= 0:
            raise _RangeError(416, f"bytes */{size}")
        start, end = max(0, size - suffix), size
    if start < 0 or start >= size or end <= start:
        raise _RangeError(416, f"bytes */{size}")
    return start, end


def _register(f: BinaryIO, generation: int) -> bool:
    """登记句柄；若 open 与登记之间发生了一次释放，返回 False 让调用方关掉。"""
    with _registry_lock:
        if _generation != generation:
            return False
        _open_files.add(f)
        return True


def _unregister(f: BinaryIO) -> None:
    with _registry_lock:
        _open_files.discard(f)


def open_stream(path: str) -> BinaryIO:
    """打开预览文件并登记句柄。同步 open：归档前可随时被 close 掉。"""
    with _registry_lock:
        generation = _generation
    f: BinaryIO = open(path, "rb")
    if not _register(f, generation):
        f.close()
        raise OSError("preview stream released before it started")
    return f


def active_stream_count() -> int:
    """当前登记的活跃句柄数（诊断 / 测试用）。"""
    with _registry_lock:
        return len(_open_files)


def close_preview_streams() -> int:
    """主动关闭所有活跃的预览读句柄，返回关闭数量。幂等、线程安全。

    调用点：`POST /api/ad/preview/release`（前端归档/重跑前）、
    `POST /api/history/save` 的 job、force 重跑清理前、归档删源每次重试。
    """
    global _generation
    with _registry_lock:
        _generation += 1
        files = list(_open_files)
        _open_files.clear()
    closed = 0
    for f in files:
        try:
            if not f.closed:
                f.close()
                closed += 1
        except OSError:
            pass                     # 已被响应自己关掉了
    return closed


class PreviewFileResponse(Response):
    """带 Range 的本地文件响应，句柄生命周期完全由本进程掌握。"""

    def __init__(self, path: str, media_type: str = "video/mp4",
                 chunk_size: int = _CHUNK_SIZE):
        super().__init__(content=b"", status_code=200, media_type=media_type,
                         headers={"accept-ranges": "bytes"})
        self.path = path
        self.chunk_size = chunk_size

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            raise RuntimeError("PreviewFileResponse only supports HTTP")
        try:
            st = os.stat(self.path)
        except OSError:
            await PlainTextResponse("preview file not found", status_code=404)(
                scope, receive, send)
            return

        size = st.st_size
        req = Headers(scope=scope)
        method = scope["method"].upper()
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        last_modified = formatdate(st.st_mtime, usegmt=True)

        start, end, status = 0, size, 200
        range_value = req.get("range") if method in ("GET", "HEAD") else None
        if_range = req.get("if-range")
        range_ok = not if_range or if_range.strip('"') in (
            etag.strip('"'), last_modified)
        if range_value and range_ok:
            try:
                parsed = _parse_range(range_value, size)
            except _RangeError as e:
                hdrs = MutableHeaders(raw=list(self.raw_headers))
                hdrs["content-length"] = "0"
                if e.content_range:
                    hdrs["content-range"] = e.content_range
                await send({"type": "http.response.start", "status": e.status,
                            "headers": hdrs.raw})
                await send({"type": "http.response.body", "body": b"",
                            "more_body": False})
                return
            if parsed is not None:
                start, end = parsed
                status = 206

        headers = MutableHeaders(raw=list(self.raw_headers))
        headers["content-length"] = str(end - start)
        headers["last-modified"] = last_modified
        headers["etag"] = etag
        if status == 206:
            headers["content-range"] = f"bytes {start}-{end - 1}/{size}"
        await send({"type": "http.response.start", "status": status,
                    "headers": headers.raw})
        if method == "HEAD" or start >= end:
            await send({"type": "http.response.body", "body": b"",
                        "more_body": False})
            return

        try:
            f = open_stream(self.path)
        except OSError:
            await send({"type": "http.response.body", "body": b"",
                        "more_body": False})
            return

        try:
            await anyio.to_thread.run_sync(f.seek, start)
            remaining = end - start
            while remaining > 0:
                chunk = await anyio.to_thread.run_sync(
                    f.read, min(self.chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                await send({"type": "http.response.body", "body": chunk,
                            "more_body": remaining > 0})
            if remaining > 0:
                await send({"type": "http.response.body", "body": b"",
                            "more_body": False})
        except (OSError, ValueError):
            # 浏览器取消请求 / 归档主动 close 了句柄：不是错误，安静收尾
            pass
        finally:
            _unregister(f)
            try:
                f.close()            # 同步关闭：取消作用域里也一定执行
            except OSError:
                pass
