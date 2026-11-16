"""v1.23 从历史归档导入术语（FR-13）单测。

被测对象：core/services/translate_service.py 的 history_term_sources /
history_import_preview / import_history_terms / _merge_into_base_set，以及
server/translate_api.py 的两个 POST 子接口。核心约定：

- 历史源 = history/<视频名>/log/terminology.json；客户端只传目录名，
  路径解析必须走白名单 + realpath，绝不能拼出 history 之外的文件；
- 预览/去重只能用 merge_terms（后者覆盖、保留首次拼写），前端不自己合并；
- 导入只写 terms/*.json：output/ 任务表、断点、GPT 缓存、history 原文
  全都不动，零 LLM 费用；
- 目标库已有 src 默认跳过（人工译文优先），overwrite=True 才替换；
- 服务端从磁盘重读 src/tgt/note，浏览器只传 src key。

全部零网络、零 LLM 调用；落盘一律在 tmp_path。
"""

from __future__ import annotations

import json

import pytest

from core.services import translate_service as ts

TERMS_CONFIG = """
llm:
  endpoints: {}
  models: {}
  chains: {}
  tasks: {}
terminology:
  active: []
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """独立 terms/ + history/，不碰真实工作区。"""
    terms = tmp_path / "terms"
    terms.mkdir()
    history = tmp_path / "history"
    history.mkdir()
    monkeypatch.setattr(ts, "TERMS_DIR", str(terms))
    monkeypatch.setattr(ts, "HISTORY_DIR", str(history))
    return terms, history, tmp_path


def _archive(history, name, terms, theme="主题摘要"):
    d = history / name / "log"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "terminology.json"
    p.write_text(json.dumps({"theme": theme, "terms": terms},
                            ensure_ascii=False), encoding="utf-8")
    return p


def _bytes(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


class TestHistorySources:
    def test_index_only_folders_with_a_glossary(self, env):
        _, history, _ = env
        _archive(history, "Video A", [{"src": "qi", "tgt": "气"}])
        _archive(history, "Video B", [{"src": "a", "tgt": "甲"},
                                      {"src": "b", "tgt": "乙"}])
        (history / "No Glossary").mkdir()      # 没有 terminology.json → 跳过

        out = ts.history_term_sources()
        assert [s["name"] for s in out] == ["Video A", "Video B"]
        assert [s["count"] for s in out] == [1, 2]
        assert all(s["available"] and s["mtime"] > 0 for s in out)
        assert all("terms" not in s for s in out), "索引只给元数据，不塞全量词条"
        assert out[0]["path"] == "history/Video A/log/terminology.json"
        assert out[0]["theme"] == "主题摘要"

    def test_broken_archive_is_listed_but_unavailable(self, env):
        _, history, _ = env
        p = _archive(history, "Broken", [])
        p.write_text("{not json", encoding="utf-8")
        _archive(history, "Good", [{"src": "qi", "tgt": "气"}])

        out = {s["name"]: s for s in ts.history_term_sources()}
        assert out["Broken"]["available"] is False
        assert out["Good"]["available"] is True

    def test_index_is_read_only(self, env):
        _, history, _ = env
        _archive(history, "A", [{"src": "qi", "tgt": "气"}])
        before = _bytes(history)
        ts.history_term_sources()
        assert _bytes(history) == before

    @pytest.mark.parametrize("bad", [
        "../..", "..", ".", "a/b", "a\\b", "C:\\Windows", "/etc/passwd",
        "not-in-history", "", "A/../B",
    ])
    def test_client_supplied_path_cannot_escape(self, env, bad):
        _, history, _ = env
        _archive(history, "A", [{"src": "qi", "tgt": "气"}])
        with pytest.raises(ValueError):
            ts._resolve_hist_file(bad)


class TestHistoryPreview:
    def test_dedup_later_wins_with_origins_and_conflict(self, env):
        _, history, _ = env
        _archive(history, "A", [{"src": "Qi", "tgt": "气", "note": "n1"},
                                {"src": "fresh", "tgt": "新"}], "主题 A")
        _archive(history, "B", [{"src": "qi", "tgt": "气机"},
                                {"src": "yin", "tgt": "阴"}], "主题 B")

        p = ts.history_import_preview(["A", "B"])
        rows = {r["key"]: r for r in p["rows"]}
        assert list(rows) == ["qi", "fresh", "yin"]      # 保留首次出现位置
        assert rows["qi"]["src"] == "Qi"                  # 保留首次拼写
        assert rows["qi"]["tgt"] == "气机"                 # 后者覆盖
        assert rows["qi"]["conflict"] is True
        assert rows["qi"]["duplicated"] is True
        assert [o["name"] for o in rows["qi"]["origins"]] == ["A", "B"]
        assert rows["fresh"]["conflict"] is False
        assert p["stats"] == {"files": 2, "entries": 4, "unique": 3,
                              "conflicts": 1, "duplicates": 1}
        assert [s["name"] for s in p["sources"]] == ["A", "B"]

    def test_order_follows_the_selection(self, env):
        _, history, _ = env
        _archive(history, "A", [{"src": "x", "tgt": "甲"}])
        _archive(history, "B", [{"src": "x", "tgt": "乙"}])
        assert ts.history_import_preview(["A", "B"])["rows"][0]["tgt"] == "乙"
        assert ts.history_import_preview(["B", "A"])["rows"][0]["tgt"] == "甲"

    def test_empty_selection_and_broken_file(self, env):
        _, history, _ = env
        with pytest.raises(ValueError, match="至少"):
            ts.history_import_preview([])
        p = _archive(history, "Broken", [])
        p.write_text("[]", encoding="utf-8")     # 合法 JSON 但不是 dict
        with pytest.raises(ValueError, match="读取失败"):
            ts.history_import_preview(["Broken"])

    def test_preview_touches_nothing(self, env):
        terms, history, _ = env
        _archive(history, "A", [{"src": "qi", "tgt": "气"}])
        ts.save_term_set("base", {"desc": "", "terms": [{"src": "qi", "tgt": "气"}]})
        before_h, before_t = _bytes(history), _bytes(terms)
        ts.history_import_preview(["A"])
        assert _bytes(history) == before_h
        assert _bytes(terms) == before_t


class TestHistoryImport:
    def test_creates_base_set_from_selected_archives(self, env):
        terms, history, _ = env
        _archive(history, "A", [{"src": "qi", "tgt": "气", "note": "n"},
                                {"src": "fresh", "tgt": "新"}])
        _archive(history, "B", [{"src": "yin", "tgt": "阴"}])

        r = ts.import_history_terms("ai_terms", ["A", "B"], ["qi", "yin"])
        assert r == {"target": "ai_terms", "added": 2, "updated": 0,
                     "skipped": 0, "total": 2}
        saved = ts.load_term_set("ai_terms")
        assert "历史记录导入" in saved["desc"]
        assert [(t["src"], t["tgt"]) for t in saved["terms"]] == [
            ("qi", "气"), ("yin", "阴")]

    def test_existing_target_keeps_manual_translation(self, env):
        _, history, _ = env
        _archive(history, "A", [{"src": "Qi", "tgt": "LLM 气"},
                                {"src": "fresh", "tgt": "新"}])
        ts.save_term_set("base", {"desc": "人工维护", "terms": [
            {"src": "qi", "tgt": "人工气", "note": "手改"}]})

        r = ts.import_history_terms("base", ["A"], ["qi", "fresh"])
        assert (r["added"], r["updated"], r["skipped"]) == (1, 0, 1)
        saved = ts.load_term_set("base")
        assert saved["desc"] == "人工维护"
        assert [(t["src"], t["tgt"], t["note"]) for t in saved["terms"]] == [
            ("qi", "人工气", "手改"), ("fresh", "新", "")]

    def test_overwrite_replaces_tgt_and_note(self, env):
        _, history, _ = env
        _archive(history, "A", [{"src": "qi", "tgt": "新气", "note": "n2"}])
        ts.save_term_set("base", {"desc": "", "terms": [
            {"src": "qi", "tgt": "旧气", "note": "n1"}]})

        r = ts.import_history_terms("base", ["A"], ["qi"], overwrite=True)
        assert (r["added"], r["updated"], r["skipped"]) == (0, 1, 0)
        t = ts.load_term_set("base")["terms"][0]
        assert (t["tgt"], t["note"]) == ("新气", "n2")

    def test_server_rereads_values_browser_only_sends_keys(self, env):
        """srcs 只是 key；译文取自历史文件，不能由请求体伪造。"""
        _, history, _ = env
        _archive(history, "A", [{"src": "qi", "tgt": "磁盘里的气"}])
        r = ts.import_history_terms("x", ["A"], ["QI"])   # 大小写不敏感
        assert r["added"] == 1
        assert ts.load_term_set("x")["terms"][0]["tgt"] == "磁盘里的气"

    def test_only_terms_dir_changes(self, env):
        terms, history, root = env
        _archive(history, "A", [{"src": "qi", "tgt": "气"}])
        job = root / "output" / "log" / "terminology.json"
        job.parent.mkdir(parents=True)
        job.write_text(json.dumps({"terms": [{"src": "keep", "tgt": "保留"}]},
                                  ensure_ascii=False), encoding="utf-8")
        before_h, before_job = _bytes(history), job.read_bytes()
        ts.import_history_terms("x", ["A"], ["qi"])
        assert _bytes(history) == before_h, "历史归档绝不能被改写"
        assert job.read_bytes() == before_job, "当前任务术语表不能被改写"
        assert (terms / "x.json").is_file(), "唯一落点是 terms/*.json"

    @pytest.mark.parametrize("target,names,srcs,match", [
        ("bad/name", ["A"], ["qi"], "术语库名"),
        ("x", [], ["qi"], "至少|历史记录"),
        ("x", ["A"], [], "没有勾选"),
        ("x", ["A"], ["not-there"], "不存在"),
        ("x", ["missing"], ["qi"], "历史记录不存在"),
    ])
    def test_bad_inputs_are_rejected(self, env, target, names, srcs, match):
        _, history, _ = env
        _archive(history, "A", [{"src": "qi", "tgt": "气"}])
        with pytest.raises(ValueError, match=match):
            ts.import_history_terms(target, names, srcs)

    def test_corrupt_target_is_refused(self, env):
        _, history, _ = env
        _archive(history, "A", [{"src": "qi", "tgt": "气"}])
        (env[0] / "base.json").write_text("{broken", encoding="utf-8")
        with pytest.raises(ValueError, match="损坏"):
            ts.import_history_terms("base", ["A"], ["qi"])

    def test_write_back_still_uses_the_same_merge_decision(self, env, cfg_path,
                                                          tmp_cwd):
        """抽取 _merge_into_base_set 后，原 task-glossary 回写不能回归。"""
        cfg_path(TERMS_CONFIG)
        job = tmp_cwd / "output" / "log" / "terminology.json"
        job.parent.mkdir(parents=True, exist_ok=True)
        job.write_text(json.dumps({"terms": [{"src": "qi", "tgt": "气"}]},
                                  ensure_ascii=False), encoding="utf-8")
        assert ts.write_back_terms("from_job", ["qi"])["added"] == 1
        assert ts.load_term_set("from_job")["terms"][0]["tgt"] == "气"


# ----------------------------- HTTP 契约 -----------------------------

@pytest.fixture
def client(cfg_path, tmp_cwd, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import server.app as app_mod
    from core.services import translate_service as ts

    cfg_path(TERMS_CONFIG)
    (tmp_cwd / "terms").mkdir()
    (tmp_cwd / "history").mkdir()
    monkeypatch.setattr(ts, "TERMS_DIR", str(tmp_cwd / "terms"))
    monkeypatch.setattr(ts, "HISTORY_DIR", str(tmp_cwd / "history"))
    with TestClient(app_mod.create_app()) as c:
        yield c


def test_get_terms_exposes_history_index(client, tmp_cwd):
    _archive(tmp_cwd / "history", "Video A", [{"src": "qi", "tgt": "气"}])
    d = client.get("/api/translate/terms").json()
    assert [s["name"] for s in d["history"]] == ["Video A"]
    assert d["history"][0]["count"] == 1


def test_preview_endpoint_is_post_and_read_only(client, tmp_cwd):
    """GET /terms/{name} 会吞掉 GET 子路径，所以预览必须走 POST。"""
    _archive(tmp_cwd / "history", "A", [{"src": "qi", "tgt": "气"}])
    assert client.get("/api/translate/terms/history/preview").status_code in (404, 405)
    r = client.post("/api/translate/terms/history/preview", json={"names": ["A"]})
    assert r.status_code == 200
    assert r.json()["stats"]["unique"] == 1
    assert client.post("/api/translate/terms/history/preview",
                       json={"names": []}).status_code == 400
    assert client.post("/api/translate/terms/history/preview",
                       json={"names": ["../.."]}).status_code == 400


def test_import_endpoint_writes_only_base_set(client, tmp_cwd):
    _archive(tmp_cwd / "history", "A", [{"src": "qi", "tgt": "气"}])
    r = client.post("/api/translate/terms/history/import", json={
        "target": "imported", "names": ["A"], "srcs": ["qi"]})
    assert r.status_code == 200
    assert r.json()["added"] == 1
    assert (tmp_cwd / "terms" / "imported.json").is_file()
    # 浏览器没传 tgt —— 值来自服务端重读
    assert client.post("/api/translate/terms/history/import", json={
        "target": "imported", "names": ["A"], "srcs": ["qi"]}).json()["skipped"] == 1


def test_import_endpoint_rejects_bad_archive(client):
    assert client.post("/api/translate/terms/history/import", json={
        "target": "x", "names": ["../../etc"], "srcs": ["qi"]}).status_code == 400
