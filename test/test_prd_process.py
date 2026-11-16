"""PRD 流程守卫：强制「先需求后开发」，防止开发完才补需求。

只读校验 `PRD.md`，不写盘、不依赖任何第三方包；已并入全量 pytest
（PRD 0.4 验证清单）。守卫保证的是**文档结构可追溯**：三阶段门存在、
v1.35 起每条变更记录都有「需求/任务」与「执行/结果」两段、且最新一条
已完成阶段 C 回填。时序本身由 0.2 的流程门约束。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PRD_PATH = Path(__file__).resolve().parents[1] / "PRD.md"

# v1.35 起强制两段式变更记录；之前是历史行，不追溯改写（其中 v1.33
# 是线上返工、未按两段式记录）。
GATE_FROM_VERSION = 35

NEED = "**需求/任务**"
RESULT = "**执行/结果**"


@pytest.fixture(scope="module")
def prd_text() -> str:
    assert PRD_PATH.is_file(), f"PRD.md not found: {PRD_PATH}"
    return PRD_PATH.read_text(encoding="utf-8")


def _change_rows(text: str) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for line in text.splitlines():
        m = re.match(r"^\| v1\.(\d+) \|", line)
        if m:
            rows.append((int(m.group(1)), line))
    return rows


def test_three_phase_gate_is_documented(prd_text: str) -> None:
    """0.2 必须写明 A→B→C，且需求行落盘前禁止碰代码。"""
    assert "**A. 需求先行" in prd_text
    assert "**B. 开发" in prd_text
    assert "**C. 结果后置" in prd_text
    assert "不得跳步" in prd_text
    assert "PRD 需求行 + 变更记录行未落盘前" in prd_text
    assert "线上报错的紧急修复同样适用" in prd_text


def test_change_record_note_requires_two_phase(prd_text: str) -> None:
    """第 12 章表尾注记必须禁止「开发完才补需求行」。"""
    assert "变更记录强制格式（v1.35 起）" in prd_text
    assert "禁止开发完才补需求行" in prd_text


def test_rows_since_gate_have_both_phases(prd_text: str) -> None:
    rows = [(v, line) for v, line in _change_rows(prd_text) if v >= GATE_FROM_VERSION]
    assert rows, "v1.35 起至少要有一条变更记录"
    for version, line in rows:
        assert NEED in line, f"v1.{version} 缺「需求/任务」段"
        assert RESULT in line, f"v1.{version} 缺「执行/结果」段"


def test_latest_row_is_backfilled(prd_text: str) -> None:
    """阶段 C 未回填执行/结果就跑全量 → 直接失败，防止跳过结果后置。"""
    rows = _change_rows(prd_text)
    version, line = max(rows, key=lambda item: item[0])
    assert version >= GATE_FROM_VERSION
    # 只看「执行/结果」之后的部分：需求段落里可能引用「待填」一词描述占位
    # 约定，那不算未完成；结果文本与验证列必须已经回填。
    tail = line.rsplit(RESULT, 1)[-1]
    assert "待填（开发完成前不得预填）" not in tail, (
        f"v1.{version} 的「执行/结果」/验证列仍是占位："
        "请按 0.2 阶段 C 回填实际改动与验证结果后再跑全量"
    )
    assert len(tail.strip(" |")) > 40, f"v1.{version} 的执行/结果疑似空填"


def test_verification_list_mentions_guard(prd_text: str) -> None:
    assert "test/test_prd_process.py" in prd_text
