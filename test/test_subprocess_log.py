"""core/utils/subprocess_log.py — 子进程输出转发到 print。

子进程输出原本写在继承的文件描述符上，页面日志（替换 sys.stdout）抓不到。
run_logged 把它们接回 print。测试用真正的子进程（`python -c`），不走 mock。
"""

import os
import subprocess
import sys
import time

import pytest

from core.utils.subprocess_log import run_logged


def _py(*lines):
    """一条会在 stdout 打印给定行的子进程命令。"""
    return [sys.executable, "-c", "\n".join(lines)]


def test_stdout_and_stderr_are_both_forwarded(capsys):
    proc = run_logged(_py("import sys", "print('to-stdout')",
                          "print('to-stderr', file=sys.stderr)"))
    out = capsys.readouterr().out
    assert proc.returncode == 0
    assert "to-stdout" in out
    assert "to-stderr" in out          # stderr 合并进 stdout


def test_output_reaches_print_within_the_task_capture(monkeypatch, capsys):
    """页面日志靠替换 sys.stdout 捕获 —— 必须走 print，不能自己写 fd。"""
    seen = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: seen.append(" ".join(map(str, a))))
    run_logged(_py("print('hello')"))
    assert any("hello" in s for s in seen)


def test_progress_lines_are_throttled_but_last_one_survives(capsys):
    cmd = _py("import sys",
              "for i in range(40):",
              "    print(f'frame={i} size=1kB', file=sys.stderr)")
    run_logged(cmd, throttle=999)      # 节流窗口极大 → 中间帧应被压掉
    out = capsys.readouterr().out
    assert out.count("frame=") == 2    # 首帧 + 末帧
    assert "frame=0" in out
    assert "frame=39" in out           # 最后一条一定补打


def test_progress_lines_pass_through_when_throttle_is_zero(capsys):
    cmd = _py("for i in range(5):", "    print(f'progress {i}%')")
    run_logged(cmd, throttle=0)
    assert capsys.readouterr().out.count("progress") == 5


def test_progress_lines_are_exempt_from_max_lines(capsys):
    """长转码的进度不该被 max_lines 掐断 —— 否则后半段彻底失声。"""
    cmd = _py("for i in range(30):", "    print(f'frame={i}')")
    run_logged(cmd, throttle=0, max_lines=5)
    out = capsys.readouterr().out
    assert out.count("frame=") == 30
    assert "省略" not in out


def test_max_lines_caps_output_with_a_summary(capsys):
    cmd = _py("for i in range(30):", "    print(f'line {i}')")
    run_logged(cmd, max_lines=5)
    out = capsys.readouterr().out
    assert out.count("line ") == 5
    assert "省略 25 行" in out


def test_check_true_raises_on_failure():
    with pytest.raises(subprocess.CalledProcessError):
        run_logged(_py("import sys", "print('boom')", "sys.exit(3)"), check=True)


def test_returncode_is_reported_without_check():
    proc = run_logged(_py("import sys", "sys.exit(7)"))
    assert proc.returncode == 7


def test_combined_output_is_returned(capsys):
    proc = run_logged(_py("print('alpha')", "print('beta')"))
    capsys.readouterr()
    assert "alpha" in proc.stdout and "beta" in proc.stdout


def test_blank_lines_are_dropped(capsys):
    run_logged(_py("print('a')", "print()", "print('b')"))
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 2


def test_kwargs_are_passed_through(capsys, tmp_path):
    proc = run_logged(_py("import os", "print(os.getcwd())"),
                      cwd=str(tmp_path))
    out = capsys.readouterr().out
    assert proc.returncode == 0
    printed = [l.strip() for l in out.splitlines() if l.strip()][-1]
    assert os.path.realpath(printed) == os.path.realpath(str(tmp_path))


def test_throttle_does_not_delay_finished_runs():
    """节流是打印节奏，不能拖慢子进程本身。"""
    start = time.monotonic()
    run_logged(_py("print('fast')"), throttle=999)
    assert time.monotonic() - start < 10
