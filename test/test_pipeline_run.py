"""POST /api/pipeline/run 的单元测试：force 语义（强制重跑）。

页面上的「强制重跑」按钮就是 force=True 这条路。它有两件事必须成立：
1. 真的调用了 force_clean（否则 @check_file_exists 守卫会让步骤静默跳过，
   点了等于没点 —— 这正是历史上 "rerun 无效果" 的 bug）；
2. 清理发生在任务线程之外（print 捕获还没挂上），所以要显式推一条 log
   事件，页面日志才看得出"这一步被清空重做了"。

隔离：MANAGER / BUS / force_clean 全部换成替身，绝不起真线程、不删真文件。

Run from the repo root:  python -m pytest test/test_pipeline_run.py -q
"""

from __future__ import annotations

import os
import threading

import pytest

from conftest import CHAIN_CONFIG

MEDIA_CONFIG = CHAIN_CONFIG + """
allowed_video_formats:
- 'mp4'
allowed_audio_formats:
- 'wav'
"""


class FakeManager:
    """替身 MANAGER：只记录 start 的入参。"""

    def __init__(self, ok=True):
        self.calls: list = []
        self.ok = ok

    def start(self, step_ids, *, before_start=None):
        if not self.ok:
            return False, "busy"
        if before_start is not None:
            before_start()
        self.calls.append(list(step_ids))
        return True, "started 1 task(s)"

    def snapshot(self):
        return {"state": "idle", "queue": [], "current_step": None,
                "error": "", "log_file": None}


class FakeBus:
    """替身 LogBus：收集 publish 的事件。"""

    def __init__(self):
        self.events: list = []

    def bind_loop(self, loop):
        pass

    def publish(self, event: dict):
        self.events.append(event)


@pytest.fixture
def run_client(cfg_path, tmp_cwd, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import core.services.run_service as run_service
    import server.app as app_mod

    cfg_path(MEDIA_CONFIG)
    out = tmp_cwd / "output"
    out.mkdir(exist_ok=True)
    (out / "clip.mp4").write_bytes(b"input")
    monkeypatch.setattr(app_mod, "PROJECT_ROOT", str(tmp_cwd))

    mgr = FakeManager()
    bus = FakeBus()
    cleaned: list = []

    def _fake_clean(step_ids, workspace):
        cleaned.append((list(step_ids), workspace))
        return list(step_ids)

    # app.py 在请求处理时才 `from ... import`，运行时取模块属性 → 可拦截
    monkeypatch.setattr(run_service, "force_clean", _fake_clean)
    monkeypatch.setattr(app_mod, "MANAGER", mgr)
    monkeypatch.setattr(app_mod, "BUS", bus)

    app = app_mod.create_app()
    with TestClient(app) as c:
        c.manager = mgr        # type: ignore[attr-defined]
        c.bus = bus            # type: ignore[attr-defined]
        c.cleaned = cleaned    # type: ignore[attr-defined]
        yield c


def _step_ids():
    from core.pipeline import get_steps
    return [s.id for s in get_steps()]


# ------------------------------------------------------------ 非 force


def test_run_without_force_does_not_clean(run_client):
    """「从这一步跑到完」是续跑语义 —— 绝不能顺手删掉已有产物。"""
    ids = _step_ids()
    r = run_client.post("/api/pipeline/run", json={"from_step": ids[0]})
    assert r.status_code == 200
    assert run_client.cleaned == []
    assert run_client.manager.calls[0] == ids


def test_run_single_step_resolves_to_one_id(run_client):
    ids = _step_ids()
    r = run_client.post("/api/pipeline/run", json={"step_id": ids[1]})
    assert r.status_code == 200
    assert r.json()["steps"] == [ids[1]]


# --------------------------------------------------------------- force


def test_force_cleans_before_starting(run_client):
    """强制重跑的核心：先清产物/断点/LLM 缓存，再启动。"""
    ids = _step_ids()
    r = run_client.post("/api/pipeline/run",
                        json={"step_id": ids[1], "force": True})
    assert r.status_code == 200
    assert run_client.cleaned == [([ids[1]], run_client.cleaned[0][1])]
    assert run_client.manager.calls[0] == [ids[1]]


def test_force_from_step_cleans_every_step_after_it(run_client):
    """顶部的「强制重跑」：这一步及之后所有步骤都要清，否则下游会跳过。"""
    ids = _step_ids()
    r = run_client.post("/api/pipeline/run",
                        json={"from_step": ids[1], "force": True})
    assert r.status_code == 200
    assert run_client.cleaned[0][0] == ids[1:]
    assert run_client.manager.calls[0] == ids[1:]


def test_force_publishes_a_visible_log_line(run_client):
    """清理不在任务线程里（print 捕获未挂上）→ 必须显式推 log 事件。"""
    ids = _step_ids()
    run_client.post("/api/pipeline/run",
                    json={"step_id": ids[1], "force": True})
    logs = [e for e in run_client.bus.events if e.get("type") == "log"]
    assert len(logs) == 1
    assert "强制重跑" in logs[0]["line"]


def test_no_log_line_without_force(run_client):
    ids = _step_ids()
    run_client.post("/api/pipeline/run", json={"step_id": ids[1]})
    assert [e for e in run_client.bus.events if e.get("type") == "log"] == []


def test_unknown_step_is_404(run_client):
    r = run_client.post("/api/pipeline/run", json={"step_id": "nope"})
    assert r.status_code == 404


# ------------------------------------------- 断点文件不是完成判据


def test_asr_correct_is_done_without_ckpt(tmp_cwd):
    """asr_correct 成功收尾会自己删掉 ckpt —— 它不能是完成判据。

    把它列进 markers 的后果：页面上「幻觉词修正」跑完了也永远显示
    「未运行」（marker_mode=all 缺一即 False），用户以为没跑，反复重跑。
    """
    from core.pipeline import get_step
    step = get_step("asr_correct")
    assert "output/log/asr_correct_ckpt.json" not in step.markers
    assert "output/log/asr_correct_ckpt.json" in step.clean_extra

    os.makedirs("output/log", exist_ok=True)
    with open(step.markers[0], "w", encoding="utf-8") as f:
        f.write("{}")
    assert step.is_done() is True   # 只有 report，没有 ckpt


def test_force_clean_wipes_step_checkpoints(tmp_cwd):
    """断点/缓存不参与完成判定，但强制重跑时必须清掉。

    - asr_correct_ckpt.json：留着会让重跑从旧断点恢复，等于没重跑
    - output/log/asr_checkpoint：留着会让第 2 步直接复用旧 seg_*.json
    """
    from core.pipeline import get_step
    from core.utils.job_context import delete_step_artifacts

    os.makedirs("output/log/asr_checkpoint", exist_ok=True)
    with open("output/log/asr_checkpoint/seg_0000.json", "w",
              encoding="utf-8") as f:
        f.write("{}")
    with open("output/log/asr_correct_ckpt.json", "w", encoding="utf-8") as f:
        f.write("{}")

    delete_step_artifacts("transcribe", os.getcwd())
    assert not os.path.exists("output/log/asr_checkpoint")

    delete_step_artifacts("asr_correct", os.getcwd())
    assert not os.path.exists("output/log/asr_correct_ckpt.json")
    # report 是 marker，一并被清掉了 → 这一步回到「未运行」，符合强制重跑语义
    assert not os.path.exists(get_step("asr_correct").markers[0])


# ------------------------------------------------------- 日志落盘


def test_run_log_lands_on_disk(monkeypatch, tmp_path):
    """页面日志刷新就没了 —— 每次运行都必须有一份可查的文件。

    FR-31：不再长期持有 'w' 句柄，任务线程与外部卡片操作统一走
    logbus.append_log_file（共享锁 + append），路径常量只 patch 一处。
    """
    import server.task_manager as tm
    import server.logbus as lb
    monkeypatch.setattr(lb, "LOG_DIR", str(tmp_path / "log"))
    assert tm.open_run_log() is None       # 只归档 + 清空，不返回句柄
    lb.append_log_file("line one")
    latest = tmp_path / "log" / "pipeline_latest.log"
    assert latest.exists()
    assert "line one" in latest.read_text(encoding="utf-8")


def test_run_log_archives_the_previous_run(monkeypatch, tmp_path):
    """上一次的 latest 要留成带时间戳的副本，而不是被直接覆盖。"""
    import server.task_manager as tm
    import server.logbus as lb
    log_dir = tmp_path / "log"
    monkeypatch.setattr(lb, "LOG_DIR", str(log_dir))
    tm.open_run_log()
    lb.append_log_file("previous run")
    tm.open_run_log()
    # glob("pipeline_*.log") 也会匹配 pipeline_latest.log，要排除掉
    archived = [p for p in log_dir.glob("pipeline_*.log")
                if p.name != "pipeline_latest.log"]
    assert len(archived) == 1                      # 上次内容被留成归档
    assert "previous run" in archived[0].read_text(encoding="utf-8")
    assert "previous run" not in (log_dir / "pipeline_latest.log").read_text(encoding="utf-8")


def test_run_log_rotation_keeps_only_recent_files(monkeypatch, tmp_path):
    import server.task_manager as tm
    import server.logbus as lb
    log_dir = tmp_path / "log"
    monkeypatch.setattr(lb, "LOG_DIR", str(log_dir))
    for _ in range(14):
        tm.open_run_log()
    n = len(list(log_dir.glob("pipeline_*.log")))
    assert n <= tm._MAX_LOG_FILES


def test_run_log_captures_stdout_of_the_job(monkeypatch, tmp_path):
    """任务里 print 的内容要同时进文件（这才是"完整日志"）。"""
    import server.task_manager as tm
    import server.logbus as lb
    monkeypatch.setattr(lb, "LOG_DIR", str(tmp_path / "log"))
    mgr = tm.TaskManager()
    ok, _ = mgr.start_job("say", lambda: print("hello-from-job"))
    assert ok
    mgr._thread.join(timeout=10)
    text = (tmp_path / "log" / "pipeline_latest.log").read_text(encoding="utf-8")
    assert "hello-from-job" in text
    assert "步骤开始" in text          # 步骤边界也要记下来


def test_busy_manager_is_409(run_client, monkeypatch):
    import server.app as app_mod
    monkeypatch.setattr(app_mod, "MANAGER", FakeManager(ok=False))
    ids = _step_ids()
    r = run_client.post("/api/pipeline/run", json={"step_id": ids[0]})
    assert r.status_code == 409


@pytest.mark.parametrize("state", ["running", "paused"])
def test_busy_force_request_does_not_prepare_or_clean(run_client, monkeypatch, state):
    from server.task_manager import TaskManager
    import server.app as app_mod

    manager = TaskManager()
    manager.state = state
    monkeypatch.setattr(app_mod, "MANAGER", manager)
    response = run_client.post("/api/pipeline/run", json={
        "from_step": "transcribe", "to_step": "merge_sub", "force": True,
    })
    assert response.status_code == 409
    assert run_client.cleaned == []
    assert run_client.bus.events == []
    assert manager._thread is None


@pytest.mark.parametrize("ambiguous", [False, True])
def test_invalid_input_rejects_force_without_cleaning(run_client, tmp_cwd, ambiguous):
    out = tmp_cwd / "output"
    if ambiguous:
        (out / "second.mp4").write_bytes(b"input")
    else:
        (out / "clip.mp4").unlink()
    response = run_client.post("/api/pipeline/run", json={
        "from_step": "transcribe", "to_step": "align", "force": True,
    })
    assert response.status_code == 400
    assert "输入文件" in response.json()["detail"]
    assert run_client.cleaned == []
    assert run_client.manager.calls == []
    assert run_client.bus.events == []


def test_concurrent_start_waits_for_force_preparation(monkeypatch):
    from server.task_manager import TaskManager
    import server.task_manager as task_manager

    preparing = threading.Event()
    release = threading.Event()
    competing = threading.Event()
    actions = []
    results = []

    class IdleWorker:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            actions.append("worker")

    # Exercise admission with real caller threads, without running pipeline code.
    caller_thread = threading.Thread
    monkeypatch.setattr(task_manager.threading, "Thread", IdleWorker)
    manager = TaskManager()

    def prepare():
        actions.append("prepare")
        preparing.set()
        assert release.wait(5)
        actions.append("cleaned")

    def start_first():
        results.append(manager.start(["transcribe"], before_start=prepare))

    def start_second():
        competing.set()
        results.append(manager.start(
            ["transcribe"], before_start=lambda: actions.append("unexpected-clean")))

    first = caller_thread(target=start_first)
    second = caller_thread(target=start_second)
    first.start()
    try:
        assert preparing.wait(5)
        second.start()
        assert competing.wait(5)
    finally:
        release.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert actions == ["prepare", "cleaned", "worker"]
    assert sum(ok for ok, _ in results) == 1
    assert any(not ok and "already running" in msg for ok, msg in results)


def test_failed_preparation_does_not_start_a_worker():
    from server.task_manager import TaskManager

    manager = TaskManager()

    def prepare():
        raise ValueError("input unavailable")

    with pytest.raises(ValueError, match="input unavailable"):
        manager.start(["transcribe"], before_start=prepare)
    assert manager.state == "idle"
    assert manager._thread is None
    assert manager.queue == []


# --------------------------------------------- 开始 / 结束步骤范围


def test_run_with_to_step_runs_an_inclusive_range(run_client):
    """页面两个下拉：开始和结束都含在队列里。"""
    ids = _step_ids()
    r = run_client.post("/api/pipeline/run",
                        json={"from_step": ids[1], "to_step": ids[3]})
    assert r.status_code == 200
    assert r.json()["steps"] == ids[1:4]
    assert run_client.manager.calls[0] == ids[1:4]


def test_to_step_without_from_starts_at_the_first_step(run_client):
    ids = _step_ids()
    r = run_client.post("/api/pipeline/run", json={"to_step": ids[2]})
    assert r.status_code == 200
    assert run_client.manager.calls[0] == ids[:3]


def test_force_only_cleans_the_selected_range(run_client):
    """强制重跑不能清到结束步骤之后 —— 下游产物要留着。"""
    ids = _step_ids()
    r = run_client.post("/api/pipeline/run",
                        json={"from_step": ids[1], "to_step": ids[3], "force": True})
    assert r.status_code == 200
    assert run_client.cleaned[0][0] == ids[1:4]


@pytest.mark.parametrize("burn,audio_only,end", [
    (True, False, "merge_sub"),
    (False, False, "align"),
    (True, True, "align"),
])
def test_stage_one_force_follows_default_range_and_preserves_input(
        run_client, cfg_path, tmp_cwd, burn, audio_only, end):
    cfg_path(MEDIA_CONFIG + f"\nburn_subtitles: {str(burn).lower()}\n")
    source = tmp_cwd / "output" / "clip.mp4"
    if audio_only:
        source.rename(source.with_suffix(".wav"))
        source = source.with_suffix(".wav")
    selected = run_client.get("/api/pipeline").json()["defaults"]
    response = run_client.post("/api/pipeline/run", json={**selected, "force": True})
    assert response.status_code == 200
    expected = ["transcribe", "asr_correct", "split", "translate", "align"]
    if end == "merge_sub":
        expected.append("merge_sub")
    assert response.json()["steps"] == expected
    assert run_client.cleaned[0][0] == expected
    assert run_client.manager.calls == [expected]
    assert source.read_bytes() == b"input"


def test_unknown_to_step_is_404(run_client):
    r = run_client.post("/api/pipeline/run", json={"to_step": "nope"})
    assert r.status_code == 404


def test_reversed_range_is_400(run_client):
    ids = _step_ids()
    r = run_client.post("/api/pipeline/run",
                        json={"from_step": ids[4], "to_step": ids[1]})
    assert r.status_code == 400


def test_pipeline_status_exposes_default_range(run_client, cfg_path):
    """结束步骤默认值跟随设置里的烧录开关；开始固定转录。"""
    cfg_path(MEDIA_CONFIG + "\nburn_subtitles: false\n")
    assert run_client.get("/api/pipeline").json()["defaults"] == {
        "from_step": "transcribe", "to_step": "align"}

    cfg_path(MEDIA_CONFIG + "\nburn_subtitles: true\n")
    assert run_client.get("/api/pipeline").json()["defaults"] == {
        "from_step": "transcribe", "to_step": "merge_sub"}
