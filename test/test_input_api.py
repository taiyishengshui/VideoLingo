"""输入源 / 下载端点 + TaskManager 一次性任务的单元测试。

覆盖两件事：
1. `server.task_manager.TaskManager.start_job()` —— 非注册表步骤的一次性任务
   （YouTube 下载）复用同一套 pause/stop/日志捕获机制。
2. `server.input_api` 的 HTTP 端点 —— URL/分辨率校验、已有输入的 409 守卫、
   删除输入的边界。

隔离三件套（与 conftest 一致）：
- `cfg_path` 写临时 config.yaml（media_files 要读 allowed_video_formats）
- `tmp_cwd` 切临时目录（_SAVE_PATH 是相对路径 "output"）
- monkeypatch `server.task_manager.MANAGER`，绝不允许真起线程去下载

Run from the repo root:  python -m pytest test/test_input_api.py -q
"""

from __future__ import annotations

import json
import threading

import pytest

from conftest import CHAIN_CONFIG

# media_files 依赖这两个配置键才能识别输入文件
MEDIA_CONFIG = CHAIN_CONFIG + """
allowed_video_formats:
- 'mp4'
- 'mov'
- 'avi'
allowed_audio_formats:
- 'wav'
- 'mp3'
- 'flac'
"""


# ------------------------------------------------------------------ 辅助


class FakeManager:
    """替身 MANAGER：只记录 start_job 的入参，绝不真起线程。"""

    def __init__(self):
        self.jobs: list = []

    def start_job(self, title, fn):
        self.jobs.append((title, fn))
        return True, "started 1 task(s)"


@pytest.fixture
def media_cfg(cfg_path):
    return cfg_path(MEDIA_CONFIG)


@pytest.fixture
def client(media_cfg, tmp_cwd, monkeypatch):
    """只挂 input_router 的轻量 app —— 不牵扯主 app 的静态挂载等。

    真下载在 `fake_download` 里被拦截，MANAGER 也被换成 FakeManager。
    """
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import core._1_ytdlp as ytdlp
    import server.input_api as input_api
    from server import task_manager

    calls: list = []

    def _fake_download(url, save_path="output", resolution="1080"):
        calls.append((url, save_path, resolution))

    # download_video 内部是 `from core._1_ytdlp import ...`，运行时才取属性，
    # 所以替换模块属性即可拦截真下载。
    monkeypatch.setattr(ytdlp, "download_video_ytdlp", _fake_download)

    fake = FakeManager()
    monkeypatch.setattr(task_manager, "MANAGER", fake)

    app = FastAPI()
    app.include_router(input_api.router)
    with TestClient(app) as c:
        c.fake_manager = fake          # type: ignore[attr-defined]
        c.download_calls = calls       # type: ignore[attr-defined]
        yield c


def _make_input(tmp_cwd, filename="clip.mp4", media_type="video", manifest=True):
    """在临时 output/ 里造一个输入文件（可选带 manifest）。"""
    out = tmp_cwd / "output"
    out.mkdir(exist_ok=True)
    f = out / filename
    f.write_bytes(b"\x00" * 16)
    if manifest:
        (out / "input_manifest.json").write_text(
            json.dumps({"path": f"output/{filename}", "type": media_type}),
            encoding="utf-8",
        )
    return f


# ------------------------------------------------- TaskManager.start_job


def test_start_job_runs_callable_and_completes():
    from server.task_manager import TaskManager

    tm = TaskManager()
    done: list = []
    ok, msg = tm.start_job("下载视频", lambda: done.append(1))
    assert ok, msg
    tm._thread.join(timeout=5)

    assert done == [1]
    assert tm.state == "completed"
    assert tm.queue == ["下载视频"]


def test_start_job_rejects_non_callable():
    from server.task_manager import TaskManager

    ok, msg = TaskManager().start_job("坏了", "not a function")
    assert not ok
    assert "callable" in msg


def test_start_job_rejects_while_running():
    """已在跑的任务没结束前，第二个 start_job 必须被拒绝（不是排队）。"""
    from server.task_manager import TaskManager

    gate = threading.Event()
    tm = TaskManager()
    ok1, _ = tm.start_job("slow", lambda: gate.wait(5))
    assert ok1

    ok2, msg = tm.start_job("second", lambda: None)
    assert not ok2
    assert "already running" in msg

    gate.set()
    tm._thread.join(timeout=5)


def test_start_job_captures_exception_as_error_state():
    from server.task_manager import TaskManager

    def boom():
        raise RuntimeError("yt-dlp exploded")

    tm = TaskManager()
    tm.start_job("下载视频", boom)
    tm._thread.join(timeout=5)

    assert tm.state == "error"
    assert "yt-dlp exploded" in tm.error


def test_start_still_validates_registry_step_ids():
    """start() 的步骤白名单语义不能被 start_job 的重构破坏。"""
    from server.task_manager import TaskManager

    ok, msg = TaskManager().start(["no-such-step"])
    assert not ok
    assert "unknown step id" in msg


# ------------------------------------------------------- GET /api/input/current


def test_current_reports_empty_when_no_input(client):
    r = client.get("/api/input/current")
    assert r.status_code == 200
    data = r.json()
    assert data["path"] is None
    assert data["exists"] is False
    assert data["ambiguous"] is False


def test_current_reports_manifest_input(client, tmp_cwd):
    _make_input(tmp_cwd, "clip.mp4")
    data = client.get("/api/input/current").json()
    assert data["exists"] is True
    assert data["type"] == "video"
    assert data["path"].endswith("clip.mp4")
    assert data["manifest"] is True


def test_current_flags_ambiguous_input(client, tmp_cwd):
    """output/ 里有多个视频 → 不唯一，必须报出来而不是 500。"""
    _make_input(tmp_cwd, "a.mp4", manifest=False)
    _make_input(tmp_cwd, "b.mp4", manifest=False)
    data = client.get("/api/input/current").json()
    assert data["ambiguous"] is True
    assert "not unique" in (data["message"] or "")


# ----------------------------------------------------- POST /api/input/download


def test_download_rejects_non_url(client):
    r = client.post("/api/input/download", json={"url": "你好", "resolution": "1080"})
    assert r.status_code == 400


def test_download_rejects_empty_url(client):
    r = client.post("/api/input/download", json={"url": "   ", "resolution": "1080"})
    assert r.status_code == 400


def test_download_rejects_bad_resolution(client):
    r = client.post(
        "/api/input/download",
        json={"url": "https://youtu.be/abc", "resolution": "4k"},
    )
    assert r.status_code == 400


def test_download_rejects_when_input_already_present(client, tmp_cwd):
    """已有输入就拒绝下载：否则下完会撞 find_video_files 的 not-unique 报错。"""
    _make_input(tmp_cwd, "old.mp4")
    r = client.post(
        "/api/input/download",
        json={"url": "https://youtu.be/abc", "resolution": "720"},
    )
    assert r.status_code == 409
    assert "old.mp4" in r.json()["detail"]
    assert client.fake_manager.jobs == []     # 不能起任务


def test_download_starts_job_with_correct_args(client):
    r = client.post(
        "/api/input/download",
        json={"url": "https://youtu.be/abc", "resolution": "720"},
    )
    assert r.status_code == 200, r.text

    jobs = client.fake_manager.jobs
    assert len(jobs) == 1
    title, fn = jobs[0]
    assert title == "下载视频"
    assert callable(fn)

    # 执行这个 job：真下载已被替换成记录器，断言参数透传正确
    fn()
    assert client.download_calls == [("https://youtu.be/abc", "output", "720")]


def test_download_accepts_all_whitelisted_resolutions(client):
    for res in ("360", "480", "720", "1080", "best"):
        client.fake_manager.jobs.clear()
        r = client.post(
            "/api/input/download",
            json={"url": "https://youtu.be/x", "resolution": res},
        )
        assert r.status_code == 200, f"{res} 被拒绝"
        assert client.fake_manager.jobs, f"{res} 没有起任务"


# --------------------------------------------------------- DELETE /api/input


def test_clear_input_removes_manifest_only_by_default(client, tmp_cwd):
    f = _make_input(tmp_cwd, "clip.mp4")
    r = client.delete("/api/input")
    assert r.status_code == 200
    assert "input_manifest.json" in r.json()["removed"]
    assert f.exists()                      # 文件默认保留


def test_clear_input_can_remove_file(client, tmp_cwd):
    f = _make_input(tmp_cwd, "clip.mp4")
    r = client.delete("/api/input?remove_file=true")
    assert r.status_code == 200
    assert not f.exists()
    assert not (tmp_cwd / "output" / "input_manifest.json").exists()


def test_clear_input_is_safe_when_nothing_present(client):
    r = client.delete("/api/input?remove_file=true")
    assert r.status_code == 200
    assert r.json()["removed"] == []


def test_clear_input_refuses_to_delete_paths_outside_output(client, tmp_cwd):
    """批量模式的 manifest 可能指向工作区外的绝对路径 —— 绝不能删。"""
    outside = tmp_cwd / "outside.mp4"
    outside.write_bytes(b"\x00" * 8)
    out = tmp_cwd / "output"
    out.mkdir(exist_ok=True)
    (out / "input_manifest.json").write_text(
        json.dumps({"path": str(outside), "type": "video"}), encoding="utf-8"
    )

    client.delete("/api/input?remove_file=true")
    assert outside.exists()


# ------------------------------------------------- GET /api/input/cookies


def test_cookies_status_reports_verdict(client):
    r = client.get("/api/input/cookies")
    assert r.status_code == 200
    assert "verdict" in r.json()      # 无 youtube 配置时也应给出结论，不 500


def test_cookies_status_survives_probe_failure(client, monkeypatch):
    """体检只是辅助信息，读 cookie 炸了也不能让 UI 报错。"""
    import core._1_ytdlp as ytdlp

    monkeypatch.setattr(
        ytdlp, "probe_cookies", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    r = client.get("/api/input/cookies")
    assert r.status_code == 200
    assert r.json()["verdict"] == "unknown"
    assert "boom" in r.json()["hint"]
