"""core 层全局日志出口（FR-31）—— 让「不在流水线任务线程」的日志也上页面。

背景
----
页面实时日志走 `server.logbus.BUS`，而 `TaskManager._run()` 只在**任务线程**
里把 `sys.stdout` 换成 `_StreamCapture`（→ BUS.publish + 落盘）。裁剪、识别
广告位、B站文案这类**同步 HTTP 卡片操作**跑在 uvicorn 线程池里：

* 任务线程没在跑 → `sys.stdout` 是原始的，`print` / `rprint` 只落 cmd；
* 任务线程恰好在跑 → `sys.stdout` 是进程全局的，卡片日志反而会混进任务
  日志（还带错 step 标记）—— 这就是「一会儿 cmd 一会儿页面」的成因。

这里提供一个**与 server 解耦**的注册点（G-01：core 不反向依赖 server）：
`rprint` / `subprocess_log._emit` 在 `print` 之外再调一次 `emit()`；server
启动时把 `publish_external` 注册成唯一 sink。任务线程里 `sys.stdout` 带
`_vl_capture` 标记，`emit()` 直接跳过（print 已由捕获器送进 BUS），
因此**不会重复**。CLI / 未注册 sink 时 emit 是空操作，行为不变。

任何异常都必须被吞掉 —— 日志失败绝不能拖垮裁剪、下载或流水线。
"""

from __future__ import annotations

import re
import sys
import threading

__all__ = ["register", "unregister", "emit", "plain", "rprint"]

_sinks: list = []
_lock = threading.Lock()

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# fallback 用：只删已知 rich 样式标签，别把日志里的 `[1/3]` `[LLM]` 吃掉
_RICH_TAG = re.compile(
    r"\[/?(?:bold|dim|italic|underline|blink|blink2|reverse|conceal|strike|"
    r"black|red|green|yellow|blue|magenta|cyan|white|"
    r"bright_(?:black|red|green|yellow|blue|magenta|cyan|white)|"
    r"on\s+[a-z_]+|#[0-9a-fA-F]{6}|[a-z]+\s+[a-z]+)\]", re.I)


def register(sink) -> None:
    """注册一个日志消费者；重复注册同一个对象不会生效。"""
    with _lock:
        if sink not in _sinks:
            _sinks.append(sink)


def unregister(sink) -> None:
    with _lock:
        if sink in _sinks:
            _sinks.remove(sink)


def plain(text) -> str:
    """rich markup → 纯文本（页面日志不显示 [cyan] 这类标签）。"""
    s = _ANSI.sub("", str(text))
    try:
        from rich.markup import render
        return render(s).plain
    except Exception:                                              # noqa: BLE001
        return _RICH_TAG.sub("", s)


def rprint(*args, **kwargs):
    """项目统一的 rich 打印：终端照常彩色渲染，同时补一路页面日志。

    所有 core 模块都从这里导入，别再 `from rich import print as rprint` ——
    后者在同步 HTTP 卡片操作里只会落 cmd。放在 log_hooks 而不是
    `core/utils/__init__.py`，是为了让 `__init__` 里先导入的子模块
    （ask_gpt / decorator）也能安全引用，不产生循环 import。
    """
    from rich import print as _rich_print
    _rich_print(*args, **kwargs)
    try:
        sep = str(kwargs.get("sep", " "))
        text = sep.join(plain(a) for a in args if isinstance(a, str))
        if text.strip():
            emit(text)
    except Exception:                                              # noqa: BLE001
        pass


def emit(line) -> None:
    """把一行日志送进所有 sink；任务线程里跳过（防重）。"""
    try:
        # `_StreamCapture` 置了 _vl_capture；此时 print 已经进了 BUS + 文件
        if getattr(sys.stdout, "_vl_capture", False):
            return
        text = _ANSI.sub("", str(line)).rstrip()
        if not text:
            return
        with _lock:
            sinks = list(_sinks)
        for sink in sinks:
            try:
                sink(text)
            except Exception:                                      # noqa: BLE001
                pass
    except Exception:                                              # noqa: BLE001
        pass
