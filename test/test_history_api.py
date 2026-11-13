"""POST /api/history/save —— 「保存历史」按钮的 HTTP 面。

只验接口契约（参数转发 / 忙时 409 / 空闲时启动任务），真正的搬文件逻辑在
test_history_archive.py 里覆盖。归档要搬几百 MB 的成品，绝不能同步跑在请求里。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import CHAIN_CONFIG

MEDIA_CONFIG = CHAIN_CONFIG + """
allowed_video_formats:
- 'mp4'
allowed_audio_formats:
- 'wav'
"""


class FakeManager:
    def __init__(self, ok=True):
        self.calls: list = []
        self.ok = ok

    def start_job(self, title, fn):
        self.calls.append((title, fn))
        return self.ok, "started" if self.ok else "task already running"


@pytest.fixture
def client(cfg_path, tmp_cwd, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import server.app as app_mod
    import server.history_api as hist

    cfg_path(MEDIA_CONFIG)
    mgr = FakeManager()
    monkeypatch.setattr(app_mod, "MANAGER", mgr)
    monkeypatch.setattr(hist, "MANAGER", mgr)

    app = app_mod.create_app()
    with TestClient(app) as c:
        c.manager = mgr        # type: ignore[attr-defined]
        yield c


def test_save_starts_a_background_job(client):
    r = client.post("/api/history/save", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    title, fn = client.manager.calls[0]
    assert title == "保存历史记录"
    assert callable(fn)


def test_explicit_name_is_forwarded(client, monkeypatch):
    seen = {}
    import server.history_api as hist
    # 模块级 from ... import 绑定，要 patch 使用方而不是定义方
    monkeypatch.setattr(hist, "archive_current",
                        lambda **kw: seen.update(kw) or {"video": "x", "dest": "y", "moved": 0})

    r = client.post("/api/history/save", json={"name": "自定义名"})
    assert r.status_code == 200
    client.manager.calls[0][1]()          # 跑一次 job
    assert seen["name"] == "自定义名"


def test_save_job_releases_preview_handles_before_archiving(client, monkeypatch):
    """归档 job 先主动关预览句柄，并把同一个钩子交给删源重试。"""
    import server.history_api as hist

    seen = {"order": [], "kw": {}}

    def fake_close():
        seen["order"].append("close")
        return 1

    def fake_archive(**kw):
        seen["order"].append("archive")
        seen["kw"] = kw
        return {"video": "x", "dest": "y", "moved": 1}

    monkeypatch.setattr(hist, "close_preview_streams", fake_close)
    monkeypatch.setattr(hist, "archive_current", fake_archive)

    r = client.post("/api/history/save", json={})
    assert r.status_code == 200
    assert seen["order"] == [], "job 是后台跑的，请求里不能同步搬文件"
    client.manager.calls[0][1]()

    assert seen["order"] == ["close", "archive"]
    assert seen["kw"]["on_retry"] is fake_close
    assert seen["kw"]["workspace"] == "output"


def test_locked_archive_starts_background_cleanup(client, monkeypatch):
    """归档后仍有残留 → 后台继续清出，用户不用刷新或再点一次保存。"""
    import server.history_api as hist

    started = {}

    class FakeThread:
        def __init__(self, target, args, daemon, name):
            started.update(target=target, args=args, daemon=daemon, name=name)

        def start(self):
            started["started"] = True

    monkeypatch.setattr(hist, "threading", SimpleNamespace(Thread=FakeThread))
    monkeypatch.setattr(hist, "archive_current", lambda **kw: {
        "video": "x", "dest": "y", "moved": 45, "locked": ["output_sub.mp4"]})

    assert client.post("/api/history/save", json={}).status_code == 200
    client.manager.calls[0][1]()

    assert started["started"] is True and started["daemon"] is True
    assert started["args"] == (["output_sub.mp4"],)
    assert started["target"] is hist._cleanup_locked


def test_cleanup_locked_publishes_result(monkeypatch):
    import server.history_api as hist

    events = []
    monkeypatch.setattr(hist, "retry_locked", lambda *a, **kw: [])
    monkeypatch.setattr(hist.BUS, "publish", events.append)

    hist._cleanup_locked(["output_sub.mp4"])

    assert events[0] == {"type": "history_cleaned", "locked": 0}
    assert "已自动清出" in events[1]["line"]


def test_busy_returns_409(client):
    client.manager.ok = False
    r = client.post("/api/history/save", json={})
    assert r.status_code == 409
    assert "运行" in r.json()["detail"]
