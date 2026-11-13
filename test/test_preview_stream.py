"""视频预览流（server/preview_stream.py）：Range + 读句柄主动释放。

回归目标（v1.20）：旧 `FileResponse` 在浏览器取消 Range 请求时可能留下
未关闭的读句柄，Windows 上 `output_sub.mp4` 归档时删不掉，页面只能提示
「刷新页面后再保存一次」。现在句柄必须：响应结束同步关闭、后端能主动关、
关掉后文件立刻可删。
"""

from __future__ import annotations

import os

import pytest

from server import preview_stream as ps

DATA = b"0123456789abcdefghij"


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import ad_api

    (tmp_path / "output_sub.mp4").write_bytes(DATA)
    monkeypatch.setattr(ad_api, "_WORKSPACE", str(tmp_path))
    ps.close_preview_streams()
    app = FastAPI()
    app.include_router(ad_api.router)
    with TestClient(app) as c:
        c.video_path = str(tmp_path / "output_sub.mp4")   # type: ignore[attr-defined]
        yield c
    ps.close_preview_streams()


# ---------------- Range 解析 ----------------

@pytest.mark.parametrize("value,expected", [
    ("bytes=0-5", (0, 6)),
    ("bytes=6-", (6, 20)),
    ("bytes=-4", (16, 20)),
    ("bytes=0-999", (0, 20)),
    ("bytes=0-1,5-6", None),        # 多段：忽略，回全量
    ("garbage", None),
])
def test_parse_range(value, expected):
    assert ps._parse_range(value, 20) == expected


def test_parse_range_unsatisfiable():
    with pytest.raises(ps._RangeError) as e:
        ps._parse_range("bytes=30-40", 20)
    assert e.value.status == 416
    assert e.value.content_range == "bytes */20"


# ---------------- HTTP 行为 ----------------

def test_single_range_returns_206_and_closes_handle(client):
    r = client.get("/api/ad/preview?source=burned&v=1",
                   headers={"Range": "bytes=0-5"})
    assert r.status_code == 206
    assert r.content == DATA[:6]
    assert r.headers["content-range"] == "bytes 0-5/20"
    assert r.headers["accept-ranges"] == "bytes"
    assert ps.active_stream_count() == 0, "响应结束必须同步关闭读句柄"


def test_full_get_head_and_tail_range(client):
    assert client.get("/api/ad/preview?source=burned").status_code == 200
    assert client.get("/api/ad/preview?source=burned").content == DATA
    head = client.head("/api/ad/preview?source=burned")
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(DATA))
    assert head.content == b""
    tail = client.get("/api/ad/preview?source=burned",
                      headers={"Range": "bytes=-4"})
    assert tail.status_code == 206 and tail.content == DATA[-4:]
    assert ps.active_stream_count() == 0


def test_unsatisfiable_range_returns_416(client):
    r = client.get("/api/ad/preview?source=burned",
                   headers={"Range": "bytes=999-1000"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{len(DATA)}"


def test_if_range_mismatch_falls_back_to_full(client):
    r = client.get("/api/ad/preview?source=burned",
                   headers={"Range": "bytes=0-5", "If-Range": "stale-version"})
    assert r.status_code == 200 and r.content == DATA


def test_unknown_source_is_404(client):
    assert client.get("/api/ad/preview?source=missing").status_code == 404


# ---------------- 句柄释放（归档能删掉 mp4 的关键） ----------------

def test_close_preview_streams_frees_the_file(client):
    path = client.video_path
    f = ps.open_stream(path)                  # 模拟仍在被浏览器读取的流
    assert ps.active_stream_count() == 1
    assert f.read(4) == DATA[:4]
    if os.name == "nt":
        with pytest.raises(PermissionError):
            os.remove(path)                 # Windows：句柄未关 → 删不掉
    assert ps.close_preview_streams() == 1
    assert f.closed and ps.active_stream_count() == 0
    os.remove(path)
    assert not os.path.exists(path)
    assert ps.close_preview_streams() == 0  # 幂等


def test_release_endpoint_closes_handles(client):
    path = client.video_path
    f = ps.open_stream(path)
    r = client.post("/api/ad/preview/release")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "released": 1}
    assert f.closed
    os.remove(path)
    assert not os.path.exists(path)


def test_stream_opened_during_release_is_refused(tmp_path, monkeypatch):
    """open 与登记之间发生释放：句柄不能悄悄漏掉。"""
    path = tmp_path / "v.mp4"
    path.write_bytes(DATA)
    ps.close_preview_streams()
    gen = ps._generation

    def release_between_open_and_register(f, generation):
        ps.close_preview_streams()
        return False

    monkeypatch.setattr(ps, "_register", release_between_open_and_register)
    with pytest.raises(OSError, match="released"):
        ps.open_stream(str(path))
    assert ps.active_stream_count() == 0
    assert ps._generation > gen
    os.remove(path)
    assert not os.path.exists(path)
