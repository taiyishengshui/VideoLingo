"""任务链 HTTP 面（v1.19）：删除链 + 任务改绑的引用完整性。

前端删除被任务绑定的链时会自动改绑；这里验证服务端 PUT /chains
（一次写全量 chains + tasks）能接受删除后的配置，且仍拒绝空链、
悬空绑定、零条链、缺 default —— 避免删完又冒出新的 400。
"""

from __future__ import annotations

import pytest

from conftest import CHAIN_CONFIG

CHAINS = {
    "test": {"label": "测试", "models": ["mini"]},
    "quality-first": {"label": "质量优先", "models": ["big", "mini"]},
    "light-verify": {"label": "轻量校验", "models": ["mini", "big"]},
}
TASKS = {
    "default": "test",
    "translate_faith": "test",
    "translate_express": "quality-first",
    "asr_sentence_fix": "light-verify",
}


@pytest.fixture
def client(cfg_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import server.app as app_mod
    import server.models_api as api

    p = cfg_path(CHAIN_CONFIG)
    # models_api 在导入时复制了 CONFIG_PATH，必须单独指向同一份临时配置
    monkeypatch.setattr(api, "CONFIG_PATH", str(p))
    with TestClient(app_mod.create_app()) as c:
        yield c


def _put(client, chains, tasks):
    return client.put("/api/models/chains", json={
        "chains": chains, "tasks": tasks, "retries_per_model": 2})


def _without(chains, cid):
    return {k: v for k, v in chains.items() if k != cid}


def _rebind(tasks, old, new):
    return {t: (new if c == old else c) for t, c in tasks.items()}


def test_delete_bound_chain_with_rebind_is_saved(client):
    """用户场景：test 链被多个任务绑定且模型被清空 → 删掉它、任务全部改绑。"""
    chains = _without(CHAINS, "test")
    tasks = _rebind(TASKS, "test", "quality-first")
    r = _put(client, chains, tasks)
    assert r.status_code == 200, r.text
    d = client.get("/api/models").json()
    assert "test" not in d["chains"]
    assert d["tasks"]["default"] == "quality-first"
    assert d["tasks"]["translate_faith"] == "quality-first"
    assert d["tasks"]["asr_sentence_fix"] == "light-verify"   # 未受影响的绑定保留


def test_delete_unbound_chain_keeps_bindings(client):
    r = _put(client, _without(CHAINS, "light-verify"),
             _rebind(TASKS, "light-verify", "test"))
    assert r.status_code == 200, r.text
    d = client.get("/api/models").json()
    assert "light-verify" not in d["chains"]
    assert d["tasks"]["translate_express"] == "quality-first"


def test_empty_chain_400_has_actionable_hint(client):
    chains = dict(CHAINS)
    chains["test"] = {"label": "测试", "models": []}
    r = _put(client, chains, TASKS)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "链 test 不能为空" in detail
    assert "添加模型" in detail and "删除" in detail


def test_delete_chain_without_rebind_is_rejected(client):
    """删链但任务仍指向它 → 400，不能让悬空绑定落盘。"""
    r = _put(client, _without(CHAINS, "test"), TASKS)
    assert r.status_code == 400
    assert "绑定的链" in r.json()["detail"]


def test_cannot_save_zero_chains(client):
    r = _put(client, {}, {"default": "test"})
    assert r.status_code == 400
    assert "至少需要一条链" in r.json()["detail"]


def test_default_binding_is_still_required(client):
    r = _put(client, CHAINS, {"translate_faith": "test"})
    assert r.status_code == 400
    assert "default" in r.json()["detail"]


def test_chain_referencing_unknown_model_is_rejected(client):
    chains = dict(CHAINS)
    chains["test"] = {"label": "测试", "models": ["ghost"]}
    r = _put(client, chains, TASKS)
    assert r.status_code == 400
    assert "ghost" in r.json()["detail"]


def test_model_api_type_is_saved_and_returned(client):
    r = client.put("/api/models/model", json={
        "name": "mini", "endpoint": "epa", "model": "gpt-mini-responses",
        "llm_support_json": True, "api_type": "responses",
    })
    assert r.status_code == 200, r.text
    assert client.get("/api/models").json()["models"]["mini"]["api_type"] == "responses"


def test_model_api_rejects_unknown_api_type(client):
    r = client.put("/api/models/model", json={
        "name": "mini", "endpoint": "epa", "model": "gpt-mini",
        "api_type": "chat_completions_v2",
    })
    assert r.status_code == 400
    assert "api_type" in r.json()["detail"]
