# use try-except to avoid error when installing
# rprint 统一走 log_hooks（FR-31）：core 里任何模块的打印都会同时进 cmd、
# 页面实时日志与 pipeline_latest.log，不管它跑在任务线程还是 HTTP 线程。
try:
    from .log_hooks import rprint, register, unregister, emit, plain
except ImportError:
    pass

try:
    from .ask_gpt import ask_gpt
    from .decorator import except_handler, check_file_exists
    from .config_utils import load_key, update_key, get_joiner
except ImportError:
    pass


def check_cancel():
    """Cooperative cancellation hook for long-running core loops.

    Imports lazily to avoid coupling core scripts to the Streamlit-side
    TaskRunner. Becomes a no-op when no runner is active (CLI usage).
    """
    try:
        from core.st_utils.task_runner import TaskRunner
    except Exception:
        return
    TaskRunner.check_cancel()


__all__ = [
    "ask_gpt",
    "except_handler",
    "check_file_exists",
    "load_key",
    "update_key",
    "rprint",
    "get_joiner",
    "check_cancel",
    "register",
    "unregister",
    "emit",
    "plain",
]
