"""fetch-models 的 TLS 信任与错误提示。

回归背景：Windows 系统 CA 存储缺中间证书时，urllib 默认 context 会对
完全正常的站点报 CERTIFICATE_VERIFY_FAILED，而 UI 提示却让用户去切代理开关
（无效）。证书信任必须收敛到 net_policy.ssl_context，且不能靠关掉校验解决。
"""
from __future__ import annotations

import ssl
import sys
import types
import urllib.error
import urllib.request

import pytest

from core.utils import net_policy


# --------------------------------------------------------------------------
# 错误提示分类
# --------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
    "unable to get local issuer certificate (_ssl.c:997)",
    "SSLCertVerificationError: self signed certificate",
    "network.ca_bundle 无法加载 CA 证书 D:/bad.pem",
])
def test_ssl_errors_do_not_suggest_toggling_proxy(msg):
    hint = net_policy.network_error_hint(msg)
    assert "证书" in hint
    assert "ca_bundle" in hint
    assert "切换代理开关无效" in hint


@pytest.mark.parametrize("msg,expect", [
    ("HTTP Error 401: Unauthorized", "Key 无效"),
    ("HTTP Error 403: Forbidden", "权限"),
    ("HTTP Error 404: Not Found", "手填模型名"),
    ("HTTP Error 405: Method Not Allowed", "手填模型名"),
    ("HTTP Error 429: Too Many Requests", "限流"),
    ("HTTP Error 502: Bad Gateway", "服务端错误"),
])
def test_http_status_has_specific_hint(msg, expect):
    hint = net_policy.network_error_hint(msg)
    assert expect in hint
    assert "代理开关" not in hint


@pytest.mark.parametrize("msg", [
    "urlopen error timed out",
    "<urlopen error [WinError 10061] 由于目标计算机积极拒绝>",
])
def test_network_errors_keep_proxy_hint(msg):
    assert "代理开关" in net_policy.network_error_hint(msg)


# --------------------------------------------------------------------------
# CA 合并
# --------------------------------------------------------------------------

class _FakeCtx:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def load_verify_locations(self, **kw):
        self.calls.append(kw)
        if self.fail_on and kw.get("cafile") == self.fail_on:
            raise OSError("no such file")


def _fake_ssl(monkeypatch, ctx):
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: ctx)
    fake_certifi = types.ModuleType("certifi")
    fake_certifi.where = lambda: "/fake/certifi/cacert.pem"
    monkeypatch.setitem(sys.modules, "certifi", fake_certifi)
    monkeypatch.setattr(net_policy, "_cfg", lambda key, default: default)


def test_ssl_context_appends_certifi_to_system_store(monkeypatch):
    ctx = _FakeCtx()
    _fake_ssl(monkeypatch, ctx)
    assert net_policy.ssl_context() is ctx
    assert ctx.calls == [{"cafile": "/fake/certifi/cacert.pem"}]


def test_missing_certifi_degrades_to_system_store(monkeypatch):
    ctx = _FakeCtx()
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: ctx)
    monkeypatch.setitem(sys.modules, "certifi", None)   # ImportError
    monkeypatch.setattr(net_policy, "_cfg", lambda key, default: default)
    assert net_policy.ssl_context() is ctx
    assert ctx.calls == []


def test_ca_bundle_is_appended_when_configured(monkeypatch):
    ctx = _FakeCtx()
    _fake_ssl(monkeypatch, ctx)
    monkeypatch.setattr(net_policy, "_cfg",
                        lambda key, default: "D:/proxy-root.pem"
                        if key == "ca_bundle" else default)
    net_policy.ssl_context()
    assert ctx.calls == [{"cafile": "/fake/certifi/cacert.pem"},
                         {"cafile": "D:/proxy-root.pem"}]


def test_bad_ca_bundle_fails_loudly(monkeypatch):
    ctx = _FakeCtx(fail_on="D:/missing.pem")
    _fake_ssl(monkeypatch, ctx)
    monkeypatch.setattr(net_policy, "_cfg",
                        lambda key, default: "D:/missing.pem"
                        if key == "ca_bundle" else default)
    with pytest.raises(ValueError, match="ca_bundle"):
        net_policy.ssl_context()


@pytest.fixture
def fake_openai(monkeypatch):
    import openai
    seen = {}

    class FakeHttpx:
        def __init__(self, **kw):
            seen["http"] = kw

    class FakeOpenAI:
        def __init__(self, **kw):
            seen["sdk"] = kw

    monkeypatch.setattr(openai, "DefaultHttpxClient", FakeHttpx)
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return seen


def test_openai_client_gets_merged_ca_when_bundle_configured(monkeypatch, fake_openai):
    ctx = ssl.create_default_context()
    monkeypatch.setattr(net_policy, "ssl_context", lambda: ctx)
    monkeypatch.setattr(net_policy, "_cfg",
                        lambda key, default: "D:/proxy-root.pem"
                        if key == "ca_bundle" else default)
    monkeypatch.setattr(net_policy, "get_policy", lambda: ("direct", "", ""))
    net_policy.make_openai_client("sk-test", "https://x/v1", "direct", timeout=7)
    assert fake_openai["http"]["verify"] is ctx
    assert fake_openai["http"]["trust_env"] is False
    assert fake_openai["http"]["timeout"] == 7


def test_openai_client_keeps_sdk_default_without_bundle(monkeypatch, fake_openai):
    monkeypatch.setattr(net_policy, "_cfg", lambda key, default: default)
    monkeypatch.setattr(net_policy, "get_policy", lambda: ("direct", "", ""))
    net_policy.make_openai_client("sk-test", "https://x/v1", "direct", timeout=7)
    assert "verify" not in fake_openai["http"]


# --------------------------------------------------------------------------
# 各 opener 都必须带上合并后的 context，且 auto 仍是「先直连」
# --------------------------------------------------------------------------

@pytest.fixture
def capture_opener(monkeypatch):
    seen = {}
    real_build = urllib.request.build_opener
    ctx = ssl.create_default_context()
    monkeypatch.setattr(net_policy, "ssl_context", lambda: ctx)

    def fake_build(*handlers):
        seen["handlers"] = handlers
        return real_build(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", fake_build)
    seen["ctx"] = ctx
    return seen


def _https_ctx(seen):
    for h in seen["handlers"]:
        if isinstance(h, urllib.request.HTTPSHandler):
            return getattr(h, "_context", None)
    return None


def _proxy_handler(seen):
    for h in seen["handlers"]:
        if isinstance(h, urllib.request.ProxyHandler):
            return h
    return None


@pytest.mark.parametrize("mode,use_proxy", [
    ("direct", None), ("auto", None), ("proxy", None),
    ("auto", True), ("auto", False),
])
def test_every_opener_uses_merged_ca(monkeypatch, capture_opener, mode, use_proxy):
    monkeypatch.setattr(net_policy, "get_policy",
                        lambda: (mode, "http://127.0.0.1:7890", "localhost"))
    net_policy.urllib_opener(use_proxy)
    assert _https_ctx(capture_opener) is capture_opener["ctx"]


def test_auto_still_bypasses_env_proxy_on_first_leg(monkeypatch, capture_opener):
    monkeypatch.setattr(net_policy, "get_policy",
                        lambda: ("auto", "http://127.0.0.1:7890", "localhost"))
    net_policy.urllib_opener(None)
    assert _proxy_handler(capture_opener) is not None
    assert _proxy_handler(capture_opener).proxies == {}


def test_fallback_uses_env_proxy_and_merged_ca(monkeypatch, capture_opener):
    net_policy.urllib_fallback_opener()
    assert _proxy_handler(capture_opener) is None       # 让 urllib 读 env
    assert _https_ctx(capture_opener) is capture_opener["ctx"]


def test_proxy_without_url_trusts_env(monkeypatch, capture_opener):
    monkeypatch.setattr(net_policy, "get_policy", lambda: ("proxy", "", ""))
    net_policy.urllib_opener(True)
    assert _proxy_handler(capture_opener) is None


# --------------------------------------------------------------------------
# 接口层：错误带出可执行的 hint
# --------------------------------------------------------------------------

@pytest.fixture
def models_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import models_api
    app = FastAPI()
    app.include_router(models_api.router)
    return TestClient(app)


@pytest.mark.parametrize("reason,expect", [
    ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
     "unable to get local issuer certificate", "证书"),
    ("timed out", "代理开关"),
])
def test_fetch_models_returns_actionable_hint(models_client, monkeypatch,
                                              reason, expect):
    def boom(*a, **k):
        def _open(request, timeout=None):
            raise urllib.error.URLError(reason)
        return _open

    monkeypatch.setattr(net_policy, "urllib_opener", boom)
    monkeypatch.setattr(net_policy, "get_policy", lambda: ("direct", "", ""))
    r = models_client.post("/api/models/fetch-models",
                           json={"base_url": "https://zero.cat/v1",
                                 "key": "sk-test"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["models"] == []
    assert expect in body["hint"]
