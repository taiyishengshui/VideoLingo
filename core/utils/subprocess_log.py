"""Run child processes with their output routed through `print`.

Why this exists
---------------
The web UI captures logs by swapping `sys.stdout` for a capture object inside
the task thread (see `server/task_manager.py`). That only intercepts Python
level `print` — a child process writes to its *inherited file descriptor*, so
anything started with a bare `subprocess.run(cmd)` shows up in the launching
cmd window and never in the page log.

`run_logged()` pipes stdout+stderr back and re-emits it with `print`, so the
output lands in the page log while the task is running.

Design notes
------------
* Progress-style lines (ffmpeg `frame= ... size= ...`, anything with a
  percentage) are throttled — they arrive dozens of times per second and would
  otherwise flood the log / WebSocket. The last one is always emitted. They are
  exempt from `max_lines`, so a long transcode keeps reporting to the end.
* A hard `max_lines` cap applies to everything else, protecting the UI from a
  pathological child process.
* `check=True` keeps `subprocess` semantics (raises CalledProcessError).
"""

import re
import subprocess
import time

__all__ = ["run_logged"]

# Lines that look like progress spam rather than information.
_PROGRESS_RE = re.compile(r"\bframe=|\bsize=|\bbitrate=|\d+(\.\d+)?%")
# Long-running tools report in `\r`-separated chunks; Python's universal
# newline mode already turns those into separate lines for us.
DEFAULT_THROTTLE = 2.0     # seconds between progress lines
DEFAULT_MAX_LINES = 200    # hard cap on forwarded lines per call


def _emit(prefix, line):
    text = f"{prefix}{line}"
    print(text, flush=True)
    # FR-31：任务线程里 print 已由 _StreamCapture 送进 BUS（emit 会跳过），
    # 同步 HTTP 卡片操作则由 emit 补上页面这一路；两者都不会重复。
    try:
        from . import log_hooks
        log_hooks.emit(text)
    except Exception:                                              # noqa: BLE001
        pass


def run_logged(cmd, *, check=False, prefix="  ", throttle=DEFAULT_THROTTLE,
               max_lines=DEFAULT_MAX_LINES, **kwargs):
    """Run `cmd`, forwarding its combined output to `print` line by line.

    Returns the `subprocess.CompletedProcess`. Unknown keyword arguments are
    passed through to `subprocess.Popen` (e.g. `cwd`, `env`, `timeout` is not
    supported via Popen — see note below).

    Note: `timeout` is intentionally not supported; callers that need it were
    already using `subprocess.run` for parsing, not logging.
    """
    kwargs.pop("stdout", None)
    kwargs.pop("stderr", None)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        **kwargs,
    )

    lines = []
    last_progress_at = 0.0
    pending = None          # last throttled line, emitted at the end
    suppressed = 0

    for raw in proc.stdout:
        line = raw.strip()
        if not line:
            continue
        lines.append(line)

        if _PROGRESS_RE.search(line):
            # 进度行不受 max_lines 限制 —— 否则长转码后半段会彻底失声。
            # 它们已被节流（默认 2s 一条），量级天然可控。
            now = time.monotonic()
            if now - last_progress_at >= throttle:
                _emit(prefix, line)
                last_progress_at = now
                pending = None
            else:
                pending = line
            continue

        if len(lines) <= max_lines:
            _emit(prefix, line)
        else:
            suppressed += 1

    proc.stdout.close()
    returncode = proc.wait()
    proc.stdout = None

    if pending:
        _emit(prefix, pending)
    if suppressed:
        _emit(prefix, f"... 省略 {suppressed} 行输出")

    if check and returncode != 0:
        raise subprocess.CalledProcessError(
            returncode, cmd, output="\n".join(lines))

    return subprocess.CompletedProcess(cmd, returncode, "\n".join(lines), None)
