"""FR-31 全局日志出口：卡片类同步操作的日志也必须进页面 + 落盘。

回归目标：用户 2026-11-10 发现「按此裁剪」的 ffmpeg 输出只在 cmd 窗口，
页面实时日志一片空白。根因是 TaskManager 只在任务线程替换 sys.stdout。
这里锁死「任意线程 → BUS + 文件」以及「任务线程不重复」两条不变式。
"""

import io
import sys

import pytest

from core.utils import log_hooks


@pytest.fixture(autouse=True)
def clean_sinks():
    """sink 是进程级全局；每个测试都从干净状态开始、结束也不残留。"""
    log_hooks._sinks.clear()
    yield
    log_hooks._sinks.clear()


def test_plain_strips_rich_markup_but_keeps_bracketed_text():
    assert log_hooks.plain("[cyan]✂️ 裁剪[/cyan]") == "✂️ 裁剪"
    assert "[1/3]" in log_hooks.plain("[bold green]OK[/bold green] [1/3]")
    assert "[LLM]" in log_hooks.plain("普通 [LLM] 文本")
    assert "\x1b[" not in log_hooks.plain("\x1b[31mred\x1b[0m")


def test_emit_fans_out_to_every_sink_and_registration_is_idempotent():
    a, b = [], []
    log_hooks.register(a.append)
    log_hooks.register(a.append)
    log_hooks.register(b.append)
    log_hooks.emit("  hello  ")
    # 只去尾随空白：前导缩进是 run_logged 的输出标记，要保留
    assert a == ["  hello"] and b == ["  hello"]


def test_broken_sink_does_not_break_others_or_raise():
    def boom(_line):
        raise RuntimeError("sink down")

    ok = []
    log_hooks.register(boom)
    log_hooks.register(ok.append)
    log_hooks.emit("still works")
    log_hooks.unregister(boom)
    assert ok == ["still works"]


def test_emit_skips_when_stdout_is_task_capture(monkeypatch):
    class Capture(io.TextIOBase):
        _vl_capture = True

    got = []
    log_hooks.register(got.append)
    monkeypatch.setattr(sys, "stdout", Capture())
    log_hooks.emit("should not be emitted")
    assert got == []


def test_rprint_reaches_both_cmd_and_sink(capsys):
    from core.utils.log_hooks import rprint

    got = []
    log_hooks.register(got.append)
    rprint("[cyan]✂️ 裁剪：测试[/cyan]")
    assert "✂️ 裁剪：测试" in capsys.readouterr().out
    assert got == ["✂️ 裁剪：测试"]


def test_run_logged_child_output_reaches_sink(capsys):
    from core.utils.subprocess_log import run_logged

    got = []
    log_hooks.register(got.append)
    run_logged([sys.executable, "-c", "print('child-line')"])
    capsys.readouterr()
    assert any("child-line" in x for x in got)


def test_append_log_file_appends_instead_of_truncating(monkeypatch, tmp_path):
    import server.logbus as lb

    monkeypatch.setattr(lb, "LOG_DIR", str(tmp_path))
    lb.append_log_file("one")
    lb.append_log_file("two")
    lines = (tmp_path / lb.LATEST_LOG).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "one" in lines[0] and "two" in lines[1]


def test_publish_external_hits_bus_and_disk(monkeypatch, tmp_path):
    import server.logbus as lb

    monkeypatch.setattr(lb, "LOG_DIR", str(tmp_path))
    n0 = len(lb.BUS.snapshot())
    lb.publish_external("external-line")
    evs = [e["line"] for e in lb.BUS.snapshot()[n0:]
           if e.get("type") == "log"]
    assert "external-line" in evs
    assert "external-line" in (tmp_path / lb.LATEST_LOG).read_text(encoding="utf-8")


def test_startup_registers_sink_so_http_thread_log_reaches_bus(monkeypatch, tmp_path):
    """只剩 cmd 的老 bug：startup 没注册时这里会拿不到任何 log 事件。"""
    from fastapi.testclient import TestClient
    from server.app import create_app
    from server.logbus import BUS
    import server.logbus as lb
    from core.utils import rprint

    monkeypatch.setattr(lb, "LOG_DIR", str(tmp_path / "log"))
    with TestClient(create_app()) as client:
        n0 = len(BUS.snapshot())
        rprint("[cyan]卡片操作日志[/cyan]")
        evs = [e["line"] for e in BUS.snapshot()[n0:]
               if e.get("type") == "log"]
    assert "卡片操作日志" in evs
    assert "卡片操作日志" in (tmp_path / "log" / lb.LATEST_LOG).read_text(
        encoding="utf-8")


def test_task_thread_capture_publishes_exactly_once(monkeypatch, tmp_path):
    """任务线程：print 走 _StreamCapture，emit 必须跳过，否则页面日志翻倍。"""
    from server.logbus import BUS, publish_external
    from server.task_manager import _StreamCapture
    import server.logbus as lb
    from core.utils import rprint

    monkeypatch.setattr(lb, "LOG_DIR", str(tmp_path / "log"))
    n0 = len(BUS.snapshot())
    cap = _StreamCapture(publish_external)
    monkeypatch.setattr(sys, "stdout", cap)
    rprint("[green]task-line[/green]")
    cap.flush()
    monkeypatch.undo()                       # 先把 stdout 还回去再断言
    evs = [e for e in BUS.snapshot()[n0:]
           if e.get("type") == "log" and e.get("line") == "task-line"]
    assert len(evs) == 1


def test_cut_endpoint_log_reaches_page(monkeypatch, tmp_path):
    """端到端回归：同步 HTTP 裁剪 → run_logged 回灌 → BUS。

    不跑真 ffmpeg：把 build_command 换成一个会打印一行的 Python 子进程，
    完整保留 HTTP handler → cut_video → run_logged → emit 这条链路。
    """
    from fastapi.testclient import TestClient
    from server import ad_api
    from server.app import create_app
    from server.logbus import BUS
    import server.logbus as lb
    from core.services import ad_cut_service as cut

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "output_sub.mp4").write_bytes(b"x" * 100)
    monkeypatch.setattr(ad_api, "_WORKSPACE", str(ws))
    monkeypatch.setattr(lb, "LOG_DIR", str(tmp_path / "log"))
    monkeypatch.setattr(cut, "probe", lambda _p: (100.0, True))
    monkeypatch.setattr(cut, "build_command",
                        lambda *a, **k: [sys.executable, "-c",
                                         "print('ffmpeg-fake-line')"])
    seg = {"id": 0, "brand": "手动", "note": "测试", "confidence": "manual",
           "start_basis": "manual", "start": 10.0, "end": 20.0,
           "enabled": True, "transcript_start": 10.0, "scene_cuts": []}
    with TestClient(create_app()) as client:
        n0 = len(BUS.snapshot())
        r = client.post("/api/ad/cut",
                        json={"source": "burned", "segments": [seg]})
        evs = [e["line"] for e in BUS.snapshot()[n0:]
               if e.get("type") == "log"]
    assert r.status_code == 200, r.text
    assert any("ffmpeg-fake-line" in x for x in evs)
    assert any("✂️ 裁剪" in x for x in evs)
    assert any("编码参数" in x for x in evs)
