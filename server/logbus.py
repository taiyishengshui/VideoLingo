"""Thread-safe event bus: core/task threads publish, WebSocket clients subscribe.

Keeps a ring buffer of recent events so a client that connects (or
reconnects) mid-run immediately gets the current picture.
"""

from __future__ import annotations

import asyncio
import collections
import os
import threading
import time


class LogBus:
    def __init__(self, buffer_size: int = 2000):
        self._buffer: collections.deque = collections.deque(maxlen=buffer_size)
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        """Called once at app startup so worker threads can reach the loop."""
        self._loop = loop

    def publish(self, event: dict):
        """Thread-safe: callable from any thread (task worker, API handler)."""
        with self._lock:
            self._buffer.append(event)
            subscribers = list(self._subscribers)
        loop = self._loop
        if loop is None:
            return
        for q in subscribers:
            # put_nowait via call_soon_threadsafe; drop on full queue instead
            # of blocking a worker thread (a slow client must not stall core)
            loop.call_soon_threadsafe(self._offer, q, event)

    @staticmethod
    def _offer(q: asyncio.Queue, event: dict):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def subscribe(self, maxsize: int = 5000) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._lock:
            self._subscribers.discard(q)

    def snapshot(self) -> list:
        with self._lock:
            return list(self._buffer)


BUS = LogBus()

# ---------------------------------------------------------------------------
# FR-31 统一落盘：任务线程与「卡片类同步 HTTP 操作」共用同一把锁、同一个
# 文件、同一种 append 语义。以前任务线程用 open(..., "w") 长期持有句柄，
# 外部操作若同时写会互相截断；现在随写随关，谁都能安全追加。
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(_PROJECT_ROOT, "output", "log")
LATEST_LOG = "pipeline_latest.log"
LOG_FILE_LOCK = threading.Lock()


def append_log_file(line: str) -> None:
    """带时间前缀追加一行到 output/log/pipeline_latest.log；失败静默跳过。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with LOG_FILE_LOCK:
            with open(os.path.join(LOG_DIR, LATEST_LOG), "a",
                      encoding="utf-8", errors="replace") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except Exception:                                              # noqa: BLE001
        pass


def publish_external(line: str) -> None:
    """注册给 core.utils.log_hooks 的唯一 sink。

    供不在任务线程的日志使用（裁剪 / 识别广告位 / B站文案等）。任务线程
    的 print 走 _StreamCapture，不会经过这里，因此不会重复。
    """
    BUS.publish({"type": "log", "line": line, "step": None})
    append_log_file(line)
