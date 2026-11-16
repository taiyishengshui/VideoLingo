"""Isolated tests for historical pipeline timing backfill + start/end times."""

from __future__ import annotations

import json
from datetime import datetime

from core.services import timing_service


def test_backfill_imports_history_once(tmp_path, monkeypatch):
    logs = tmp_path / "history"
    logs.mkdir()
    # 文件名带 YYYY-MM-DD，用于验证 from 文件名补全绝对日期
    (logs / "pipeline_2026-11-13_120000.log").write_text(
        "12:00:00 --- 步骤开始：生成配音 (gen_audio)\n"
        "12:03:12 --- 步骤结束：生成配音 (gen_audio) 耗时 192.4s 状态=ok\n"
        "12:03:12 === 任务结束 状态=completed 2026-11-13 12:03:12 ===\n",
        encoding="utf-8",
    )
    store = tmp_path / "store" / "step_timings.json"
    monkeypatch.setattr(timing_service, "LOG_DIR", str(store.parent))
    monkeypatch.setattr(timing_service, "TIMING_FILE", str(store))

    assert timing_service.backfill(str(logs)) == 1
    assert timing_service.backfill(str(logs)) == 0  # 幂等

    data = json.loads(store.read_text(encoding="utf-8"))
    assert len(data["records"]) == 1
    rec = data["records"][0]
    # 历史日志：文件名日期 + 开始 ts + 耗时 → 补全 started/ended
    assert rec["started_at"] == "2026-11-13 12:00:00"
    assert rec["ended_at"] == "2026-11-13 12:03:12"
    assert rec["seconds"] == 192.4
    assert rec["exact"] is True
    assert rec["id"] == "gen_audio"


def test_record_writes_started_and_ended(tmp_path, monkeypatch):
    store = tmp_path / "store" / "step_timings.json"
    monkeypatch.setattr(timing_service, "LOG_DIR", str(store.parent))
    monkeypatch.setattr(timing_service, "TIMING_FILE", str(store))

    timing_service.record("gen_audio", "生成配音", 192.4, "ok", 10)
    data = json.loads(store.read_text(encoding="utf-8"))
    rec = data["records"][-1]
    assert rec["id"] == "gen_audio"
    assert rec["started_at"] and rec["ended_at"]
    fmt = "%Y-%m-%d %H:%M:%S"
    delta = (datetime.strptime(rec["ended_at"], fmt)
             - datetime.strptime(rec["started_at"], fmt)).total_seconds()
    # 结束 - 开始 应≈ 耗时（墙上时钟四舍五入到秒，容差 2s）
    assert abs(delta - 192.4) < 2


def test_attach_fills_started_ended(tmp_path, monkeypatch):
    store = tmp_path / "store" / "step_timings.json"
    monkeypatch.setattr(timing_service, "LOG_DIR", str(store.parent))
    monkeypatch.setattr(timing_service, "TIMING_FILE", str(store))

    timing_service.record("gen_audio", "生成配音", 192.4, "ok", 10)
    steps = [{"id": "gen_audio", "no": 10, "title": "生成配音", "done": True}]
    out = timing_service.attach_last_seconds(steps)
    assert out[0]["last_seconds"] == 192.4
    assert out[0]["last_started_at"] == "2026-11-13 12:00:00" or out[0]["last_started_at"]
    assert out[0]["last_started_at"]
    assert out[0]["last_ended_at"]
