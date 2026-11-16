"""Global pipeline task manager for the web server.

Runs registry steps sequentially on a daemon thread, captures everything the
pipeline prints (rich/tqdm/plain print) and republishes it to the LogBus for
WebSocket streaming.

Pause/stop cooperates with core code through the SAME mechanism the old
Streamlit runner used: ``core.utils.check_cancel()`` consults
``TaskRunner._current`` — this manager installs itself there while running
(exposing the attribute names check_cancel expects), so long core loops
freeze on pause and raise StopTask on stop, unchanged.
"""

from __future__ import annotations

import glob
import io
import os
import re
import sys
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime

from core.pipeline import get_step, get_steps, STEP_NO
from core.services import timing_service
from core.st_utils.task_runner import TaskRunner, StopTask
from server.logbus import BUS, LOG_FILE_LOCK, append_log_file
from server import logbus as _logbus   # 动态取 LOG_DIR，测试只需 patch 一处

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# tqdm-style carriage-return progress: keep only the latest rewrite
_CR = "\r"

# 每次运行的完整日志都落盘：页面日志（WebSocket）断开/刷新就没了，
# 排查问题时用户还得从 cmd 窗口手抄。文件在 output/log/pipeline_latest.log，
# 上一次的会被归档成带时间戳的副本，只保留最近 _MAX_LOG_FILES 份。
# FR-31：文件不再由本模块长期以 "w" 持有 —— 任务线程与卡片类同步操作
# 都走 logbus.append_log_file（共享锁 + append），避免互相截断。
_MAX_LOG_FILES = 10


def open_run_log():
    """归档上次日志并清空当前日志（不再持有句柄）；失败静默跳过。"""
    try:
        log_dir = _logbus.LOG_DIR
        os.makedirs(log_dir, exist_ok=True)
        latest = os.path.join(log_dir, _logbus.LATEST_LOG)
        with LOG_FILE_LOCK:
            if os.path.exists(latest):     # 归档上一次运行
                stamp = datetime.fromtimestamp(
                    os.path.getmtime(latest)).strftime("%Y%m%d_%H%M%S")
                try:
                    os.replace(latest, os.path.join(log_dir, f"pipeline_{stamp}.log"))
                except OSError:
                    os.remove(latest)
            with open(latest, "w", encoding="utf-8", errors="replace"):
                pass
        _rotate_logs()
    except Exception:
        pass
    return None


def _rotate_logs():
    """只留最近 _MAX_LOG_FILES 份归档，避免 output/log 无限增长。"""
    try:
        files = sorted(glob.glob(os.path.join(_logbus.LOG_DIR, "pipeline_*.log")),
                       key=os.path.getmtime)
        for f in files[:-_MAX_LOG_FILES]:
            try:
                os.remove(f)
            except OSError:
                pass
    except Exception:
        pass


def latest_log_relpath() -> str:
    """相对项目根的日志路径（给前端展示 / /api/logs/tail 用）。"""
    return f"output/log/{_logbus.LATEST_LOG}"


class _StreamCapture(io.TextIOBase):
    """File-like object: buffers writes into lines and publishes them.

    `_vl_capture` 是给 `core.utils.log_hooks.emit` 的防重标记：本捕获器
    已经把 print 送进 BUS + 落盘，emit 见到它就跳过（FR-31）。
    """

    _vl_capture = True

    def __init__(self, publish):
        super().__init__()
        self._publish = publish
        self._buf = ""

    def write(self, s):
        if not s:
            return 0
        self._buf += s.replace(_CR, "\n")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = _ANSI.sub("", line).rstrip()
            if line:
                self._publish(line)
        return len(s)

    def flush(self):
        tail = _ANSI.sub("", self._buf).strip()
        if tail:
            self._publish(tail)
        self._buf = ""

    def isatty(self):
        return False  # makes rich disable styling at the source when re-created


class TaskManager:
    def __init__(self):
        self.state = "idle"  # idle | running | paused | stopped | completed | error
        self.queue: list[str] = []
        self.current_step: str | None = None
        self.error: str = ""
        # 终态定位：崩溃/中止时记录具体是哪一步，前端据此显示「失败/已停止」，
        # 否则 failed_step 被清空后前端无从区分「未运行」与「真的失败了」（v1.32）
        self.failed_step: str | None = None
        self.stopped_step: str | None = None
        self._lock = threading.RLock()   # reentrant: start() validates then delegates to _start()
        # attribute names MUST match what TaskRunner.check_cancel() reads
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # work items: ("step", step_id, None) | ("job", title, callable)
        self._jobs: list = []

    # ---------- control ----------

    def start(self, step_ids: list[str], *,
              before_start: Callable[[], None] | None = None) -> tuple[bool, str]:
        """Validate, prepare and start a run under the same admission lock."""
        with self._lock:
            if self.state in ("running", "paused"):
                return False, "task already running"
            valid = [s.id for s in get_steps()]
            unknown = [i for i in step_ids if i not in valid]
            if unknown:
                return False, f"unknown step id(s): {', '.join(unknown)}"
            ordered = [i for i in valid if i in step_ids]
            if not ordered:
                return False, "empty step list"
            if before_start is not None:
                before_start()
            return self._start([("step", sid, None) for sid in ordered])

    def start_job(self, title: str, fn) -> tuple[bool, str]:
        """Run a one-off callable under the same pause/stop/log machinery.

        Used for work that is deliberately NOT a registry step (e.g. the
        YouTube download, which takes user input and therefore lives outside
        `core.pipeline.STEPS`).
        """
        if not callable(fn):
            return False, "job is not callable"
        return self._start([("job", title, fn)])

    def _start(self, jobs: list) -> tuple[bool, str]:
        with self._lock:
            if self.state in ("running", "paused"):
                return False, "task already running"
            self._jobs = jobs
            self.queue = [ident for _, ident, _ in jobs]
            self.current_step = None
            self.error = ""
            self.failed_step = None
            self.stopped_step = None
            self.state = "running"
            self._pause_event.set()
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            return True, f"started {len(jobs)} task(s)"

    def pause(self):
        if self.state == "running":
            self._pause_event.clear()
            self.state = "paused"
            self._publish_state()

    def resume(self):
        if self.state == "paused":
            self._pause_event.set()
            self.state = "running"
            self._publish_state()

    def stop(self):
        if self.state in ("running", "paused"):
            self._stop_event.set()
            self._pause_event.set()  # unblock a paused worker so it can exit
            # state flips to "stopped" inside the worker when it notices

    # ---------- introspection ----------

    def snapshot(self) -> dict:
        return {
            "state": self.state,
            "queue": list(self.queue),
            "current_step": self.current_step,
            "error": self.error,
            "failed_step": self.failed_step,
            "stopped_step": self.stopped_step,
            "log_file": latest_log_relpath(),   # 让前端能直接找到本次日志
        }

    # ---------- worker ----------

    def _publish_log(self, line: str):
        BUS.publish({"type": "log", "line": line, "step": self.current_step})
        self._write_log(line)

    def _write_log(self, text: str):
        """落盘（带时间前缀）；与外部卡片操作共用同一把锁、同一个文件。"""
        append_log_file(text)

    def _publish_state(self):
        BUS.publish({"type": "state", **self.snapshot()})

    def _run(self):
        TaskRunner._current = self  # enables core.utils.check_cancel()
        old_out, old_err = sys.stdout, sys.stderr
        open_run_log()
        self._write_log(f"=== 任务开始 {datetime.now():%Y-%m-%d %H:%M:%S} | "
                        f"{len(self._jobs)} 个任务 ===")
        capture = _StreamCapture(self._publish_log)
        sys.stdout, sys.stderr = capture, capture
        try:
            self._publish_state()
            for kind, ident, fn in self._jobs:
                if self._stop_event.is_set():
                    self.state = "stopped"
                    break
                self._pause_event.wait()
                if self._stop_event.is_set():
                    self.state = "stopped"
                    break

                if kind == "step":
                    step = get_step(ident)
                    title, runner = step.title, step.run
                    step_no = STEP_NO[ident]
                else:  # "job" — one-off work outside the step registry
                    title, runner = str(ident), fn
                    step_no = 0
                self.current_step = ident
                t0 = time.perf_counter()
                self._write_log(f"--- 步骤开始：{title} ({ident})")
                BUS.publish({"type": "step_start", "id": ident, "title": title})
                step_status = "ok"
                try:
                    runner()
                except StopTask:
                    step_status = "stopped"
                    self.stopped_step = ident
                    self.state = "stopped"
                    break
                except BaseException:
                    step_status = "error"
                    self.failed_step = ident
                    raise
                finally:
                    # 结构化耗时：日志给人和 backfill 读，json 给页面读。
                    # 计时本身绝不能影响流水线成败。
                    elapsed = time.perf_counter() - t0
                    try:
                        self._write_log(
                            f"--- 步骤结束：{title} ({ident}) "
                            f"耗时 {elapsed:.1f}s 状态={step_status}")
                        timing_service.record(ident, title, elapsed,
                                              step_status, step_no)
                    except Exception:  # noqa: BLE001
                        pass
                if self._stop_event.is_set():
                    self.state = "stopped"
                    break
                BUS.publish({"type": "step_done", "id": ident, "title": title})
            else:
                self.state = "completed"
        except Exception as e:
            self.error = str(e)
            self.state = "error"
            traceback.print_exc()
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            self._write_log(f"=== 任务结束 状态={self.state} "
                            f"{datetime.now():%Y-%m-%d %H:%M:%S} ===")
            if TaskRunner._current is self:
                TaskRunner._current = None
            self.current_step = None
            self._publish_state()


MANAGER = TaskManager()
