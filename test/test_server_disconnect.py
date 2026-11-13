"""预览 Range 请求被浏览器掐断时，Windows Proactor 的回调异常不应刷屏。

`<video>` 拖进度条 / 换裁剪源 / 关页面都会取消上一次 Range 请求，服务端在
`_call_connection_lost` 里对已关闭 socket 调 shutdown() → WinError 10054。
asyncio 把「回调里没人接的异常」打到 stderr，看着像崩溃其实请求早结束了。
这里只验证过滤器吞这一类，别的异常照常上报。
"""

from __future__ import annotations

import asyncio

from server.app import install_disconnect_noise_filter

_CB = "Exception in callback _ProactorBasePipeTransport._call_connection_lost(None)"


def _ctx(exc, message=_CB):
    return {"message": message, "exception": exc, "handle": None}


def _loop_with_recorder(monkeypatch):
    loop = asyncio.new_event_loop()
    seen = []
    monkeypatch.setattr(loop, "default_exception_handler",
                        lambda context: seen.append(context))
    return loop, seen


def test_preview_client_reset_is_swallowed(monkeypatch):
    loop, seen = _loop_with_recorder(monkeypatch)
    try:
        install_disconnect_noise_filter(loop)
        handler = loop.get_exception_handler()
        handler(loop, _ctx(ConnectionResetError(
            10054, "远程主机强迫关闭了一个现有的连接")))
        assert seen == []
    finally:
        loop.close()


def test_broken_pipe_also_swallowed(monkeypatch):
    loop, seen = _loop_with_recorder(monkeypatch)
    try:
        install_disconnect_noise_filter(loop)
        loop.get_exception_handler()(loop, _ctx(BrokenPipeError()))
        assert seen == []
    finally:
        loop.close()


def test_unrelated_exception_still_reported(monkeypatch):
    loop, seen = _loop_with_recorder(monkeypatch)
    try:
        install_disconnect_noise_filter(loop)
        ctx = _ctx(RuntimeError("real bug"))
        loop.get_exception_handler()(loop, ctx)
        assert seen == [ctx]
    finally:
        loop.close()


def test_reset_in_other_callback_still_reported(monkeypatch):
    loop, seen = _loop_with_recorder(monkeypatch)
    try:
        install_disconnect_noise_filter(loop)
        ctx = _ctx(ConnectionResetError(), "Exception in callback Task.task_wakeup")
        loop.get_exception_handler()(loop, ctx)
        assert seen == [ctx]
    finally:
        loop.close()


def test_install_is_idempotent(monkeypatch):
    loop, _ = _loop_with_recorder(monkeypatch)
    try:
        install_disconnect_noise_filter(loop)
        first = loop.get_exception_handler()
        install_disconnect_noise_filter(loop)
        assert loop.get_exception_handler() is first
    finally:
        loop.close()
