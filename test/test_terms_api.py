"""术语库 HTTP 面（v1.16）：回写预览数据、勾选回写、新建同名确认。

只验接口契约（响应字段 / 400 / 409 / 参数转发）；真正的写入决策、
去重与原子落盘在 test_translate_service.py: TestWriteBackTerms 覆盖。
"""

from __future__ import annotations

import json

import pytest

from conftest import CHAIN_CONFIG

# config.yaml 里必须有 terminology.active；update_key 只在键已存在时写入
TERMS_CONFIG = CHAIN_CONFIG + "\nterminology:\n  active: []\n"


@pytest.fixture
def client(cfg_path, tmp_cwd, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import server.app as app_mod
    from core.services import translate_service as ts

    cfg_path(TERMS_CONFIG)
    terms = tmp_cwd / "terms"
    terms.mkdir()
    monkeypatch.setattr(ts, "TERMS_DIR", str(terms))
    with TestClient(app_mod.create_app()) as c:
        yield c


def _job(tmp_cwd, terms):
    p = tmp_cwd / "output" / "log" / "terminology.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"theme": "摘要", "terms": terms},
                            ensure_ascii=False), encoding="utf-8")
    return p


def test_get_terms_reports_missing_job(client):
    """没有 4.1 产物时 available=False，绝不把回退底板伪装成新抽术语。"""
    d = client.get("/api/translate/terms").json()
    assert d["sets"] == []
    assert isinstance(d["active"], list)
    assert d["job"] == {"available": False, "terms": [],
                        "plate_srcs": [], "sets": []}


def test_get_terms_exposes_job_terms_and_plate(client, tmp_cwd):
    client.put("/api/translate/terms", json={
        "name": "base", "desc": "基础",
        "terms": [{"src": "qi", "tgt": "气", "note": ""}], "create": True})
    assert client.put("/api/translate/terms/active",
                      json={"active": ["base"]}).status_code == 200
    _job(tmp_cwd, [{"src": "qi", "tgt": "气", "note": ""},
                   {"src": "fresh", "tgt": "新", "note": ""}])
    d = client.get("/api/translate/terms").json()
    assert d["job"]["available"] is True
    assert [t["src"] for t in d["job"]["terms"]] == ["qi", "fresh"]
    assert d["job"]["plate_srcs"] == ["qi"]
    assert d["job"]["sets"] == ["base"]


def test_get_terms_catalog_covers_all_sets_and_job(client, tmp_cwd):
    """v1.21：GET /terms 一次给出全部术语（含未激活库 + 4.1 新抽）。"""
    for name, src, tgt in (("base", "qi", "气"), ("unused", "yin", "阴")):
        assert client.put("/api/translate/terms", json={
            "name": name, "desc": "", "terms": [{"src": src, "tgt": tgt}],
            "create": True}).status_code == 200
    assert client.put("/api/translate/terms/active",
                      json={"active": ["base"]}).status_code == 200
    _job(tmp_cwd, [{"src": "qi", "tgt": "气"},
                   {"src": "fresh", "tgt": "新"}])

    d = client.get("/api/translate/terms").json()
    cat = d["catalog"]
    rows = {r["key"]: r for r in cat["rows"]}
    assert set(rows) == {"qi", "yin", "fresh"}
    assert rows["qi"]["effective"] is True
    assert rows["yin"]["effective"] is False and rows["yin"]["sets"] == ["unused"]
    assert rows["fresh"]["job_fresh"] is True and rows["fresh"]["variants"] == []
    assert cat["stats"]["total"] == 3
    assert cat["stats"]["job_available"] is True
    assert cat["stats"]["effective"] == 2
    # 老字段不受影响：回写预览仍只认严格模式的任务术语表
    assert [t["src"] for t in d["job"]["terms"]] == ["qi", "fresh"]
    assert d["job"]["plate_srcs"] == ["qi"]


def test_get_terms_catalog_sources_list_each_file_fully(client, tmp_cwd):
    """v1.22：每套基础库 + 任务术语表各自的完整词条都在 catalog.sources。

    并集总表按 src 折叠，光看它读不全某一套库；按来源清单必须逐份给全。
    """
    for name, src, tgt in (("base", "qi", "气"), ("unused", "yin", "阴")):
        assert client.put("/api/translate/terms", json={
            "name": name, "desc": "", "terms": [{"src": src, "tgt": tgt}],
            "create": True}).status_code == 200
    assert client.put("/api/translate/terms/active",
                      json={"active": ["base"]}).status_code == 200
    _job(tmp_cwd, [{"src": "qi", "tgt": "气"},
                   {"src": "fresh", "tgt": "新"}])

    cat = client.get("/api/translate/terms").json()["catalog"]
    srcs = {s["id"]: s for s in cat["sources"]}
    assert list(srcs) == ["base:base", "base:unused", "job"]
    assert [s["path"] for s in cat["sources"]] == [
        "terms/base.json", "terms/unused.json",
        "output/log/terminology.json"]
    assert [t["src"] for t in srcs["base:base"]["terms"]] == ["qi"]
    assert [t["src"] for t in srcs["base:unused"]["terms"]] == ["yin"]
    assert srcs["base:base"]["active"] is True
    assert srcs["base:unused"]["active"] is False
    assert [t["src"] for t in srcs["job"]["terms"]] == ["qi", "fresh"]
    assert srcs["job"]["available"] is True


def test_get_terms_catalog_without_job_marks_unchecked_sets_inactive(client):
    """无 4.1 产物且 active: [] → 基础库词条全部显示但都不生效。"""
    assert client.put("/api/translate/terms", json={
        "name": "base", "terms": [{"src": "qi", "tgt": "气"}],
        "create": True}).status_code == 200
    d = client.get("/api/translate/terms").json()
    cat = d["catalog"]
    assert cat["stats"]["job_available"] is False
    assert cat["stats"]["job_count"] == 0
    assert cat["stats"]["effective"] == 0
    assert len(cat["rows"]) == 1
    assert cat["rows"][0]["src"] == "qi"
    assert cat["rows"][0]["effective"] is False
    assert cat["rows"][0]["sets"] == ["base"]


def test_writeback_takes_values_from_the_job_glossary(client, tmp_cwd):
    """请求只带 src，译文由服务端重读，前端改过的脏值写不进基础库。"""
    from core.services import translate_service as ts
    _job(tmp_cwd, [{"src": "fresh", "tgt": "服务端译文", "note": "n"}])
    r = client.post("/api/translate/terms/writeback",
                    json={"target": "newset", "srcs": ["fresh"]})
    assert r.status_code == 200
    assert r.json()["added"] == 1
    assert ts.load_term_set("newset")["terms"] == [
        {"src": "fresh", "tgt": "服务端译文", "note": "n"}]


def test_writeback_without_job_is_400(client):
    r = client.post("/api/translate/terms/writeback",
                    json={"target": "x", "srcs": ["a"]})
    assert r.status_code == 400
    assert "4.1" in r.json()["detail"]


def test_writeback_invalid_name_is_400_and_writes_nothing(client, tmp_cwd):
    _job(tmp_cwd, [{"src": "a", "tgt": "b"}])
    r = client.post("/api/translate/terms/writeback",
                    json={"target": "../evil", "srcs": ["a"]})
    assert r.status_code == 400
    assert client.get("/api/translate/terms").json()["sets"] == []


def test_create_existing_is_409_until_confirmed(client):
    r = client.put("/api/translate/terms", json={
        "name": "a", "desc": "d", "terms": [{"src": "x", "tgt": "y"}]})
    assert r.status_code == 200 and r.json()["created"] is True
    r2 = client.put("/api/translate/terms", json={
        "name": "a", "terms": [], "create": True})
    assert r2.status_code == 409
    r3 = client.put("/api/translate/terms", json={
        "name": "a", "terms": [], "create": True, "overwrite": True})
    assert r3.status_code == 200 and r3.json()["count"] == 0
    assert client.get("/api/translate/terms/a").json()["desc"] == "d"


def test_empty_and_illegal_name_is_400(client):
    for name in ("", "bad/name", "a" * 49):
        r = client.put("/api/translate/terms", json={
            "name": name, "terms": [], "create": True})
        assert r.status_code == 400


def test_active_selection_never_reports_false_success(client, monkeypatch):
    """update_key 在 config 缺键时返回 False —— 勾选没落盘就不能回 200。"""
    import core.utils.config_utils as cu
    monkeypatch.setattr(cu, "update_key", lambda *a, **kw: False)
    r = client.put("/api/translate/terms/active", json={"active": []})
    assert r.status_code == 500
    assert "勾选未保存" in r.json()["detail"]
