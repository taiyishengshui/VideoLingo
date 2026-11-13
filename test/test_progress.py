"""ChunkProgress — 终端下画条，stdout 被捕获时逐块打印计数行。

UI 日志走 server/task_manager.py 的 _StreamCapture（非终端），rich 不重绘
进度条，只能靠这些纯文本行看"总多少块、还剩多少块"。
"""

import io

from rich.console import Console

from core.utils.progress import ChunkProgress


def _cap():
    """非终端 console：模拟 server 捕获 stdout 的场景。"""
    return Console(file=io.StringIO(), width=200)


def test_one_plain_line_per_unit_when_captured():
    con = _cap()
    with ChunkProgress("Translating chunks", 3, console=con) as p:
        p.advance()
        p.advance()
        p.advance()
    out = con.file.getvalue()
    assert "1/3 完成，剩 2 块" in out
    assert "2/3 完成，剩 1 块" in out
    assert "3/3 完成，剩 0 块" in out


def test_unit_is_configurable():
    """不同步骤的单位不同：块 / 条 / 段。"""
    con = _cap()
    with ChunkProgress("Generating TTS audio", 2, unit="条",
                       console=con) as p:
        p.advance()
    assert "剩 1 条" in con.file.getvalue()


def test_done_counts_batch_advance():
    con = _cap()
    with ChunkProgress("x", 5, console=con) as p:
        p.advance()
        p.advance(2)
        assert p.done == 3


def test_no_plain_lines_in_a_real_terminal(monkeypatch):
    """真终端下进度条自己会动，不再额外刷文本行（避免双份噪音）。"""
    monkeypatch.setattr(Console, "is_terminal", True, raising=True)
    con = _cap()
    with ChunkProgress("x", 3, console=con) as p:
        p.advance()
    assert "完成" not in con.file.getvalue()


def test_left_never_goes_negative():
    """advance 超过 total 时不报负数（跳过/异常路径可能多调一次）。"""
    con = _cap()
    with ChunkProgress("x", 2, console=con) as p:
        p.advance()
        p.advance()
        p.advance()
    assert "剩 0 块" in con.file.getvalue()
    assert "剩 -1" not in con.file.getvalue()
