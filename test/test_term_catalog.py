"""v1.21 全部术语目录（term_catalog）单测。

被测对象：core/services/translate_service.py: term_catalog —— #/terms 页
「全部术语」总表的唯一决策点。核心约定：
- 并集 = 所有基础库（含未激活） + 当前任务术语表，按 src 大小写不敏感去重；
- "生效" 只认 4.2 真正会注入的那份术语表（有 4.1 产物就用它，否则用
  当前勾选底板），未激活基础库的词条必须显示但标为未生效；
- 显式 active: [] / 合法空任务术语表不能被静默回退；
- 纯只读：不写 terms/、config.yaml、任务术语表，不调 LLM。

全部零网络、零 LLM 调用；落盘一律在 tmp_path。
"""

from __future__ import annotations

import json

import pytest

from core.services import translate_service as ts

CATALOG_CONFIG = """
llm:
  endpoints: {}
  models: {}
  chains: {}
  tasks: {}
terminology:
  active: [a, b]
"""


@pytest.fixture
def terms_dir(tmp_path, monkeypatch):
    d = tmp_path / "terms"
    d.mkdir()
    monkeypatch.setattr(ts, "TERMS_DIR", str(d))
    return d


@pytest.fixture
def env(tmp_cwd, terms_dir, cfg_path):
    """tmp cwd + 独立 terms/ + 可切换的 config.yaml。"""
    return tmp_cwd, terms_dir, cfg_path


def _cfg(cfg_path, active: str) -> None:
    cfg_path(CATALOG_CONFIG.replace("active: [a, b]", f"active: {active}"))


def _job(tmp_cwd, terms) -> object:
    p = tmp_cwd / "output" / "log" / "terminology.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"theme": "摘要", "terms": terms},
                            ensure_ascii=False), encoding="utf-8")
    return p


class TestTermCatalog:
    def test_union_of_all_base_sets_and_job_only_terms(self, env):
        """未激活库也要出现；4.1 新抽（只在任务表）也要出现。"""
        tmp_cwd, _, cfg_path = env
        _cfg(cfg_path, "[a]")
        ts.save_term_set("a", {"terms": [{"src": "qi", "tgt": "气"}]})
        ts.save_term_set("b", {"terms": [{"src": "yin", "tgt": "阴"}]})
        _job(tmp_cwd, [{"src": "qi", "tgt": "气"},
                       {"src": "fresh", "tgt": "新"}])

        cat = ts.term_catalog()
        rows = {r["key"]: r for r in cat["rows"]}
        assert list(rows) == ["qi", "yin", "fresh"]
        assert rows["qi"]["effective"] is True and rows["qi"]["in_job"] is True
        assert rows["qi"]["sets"] == ["a"]
        assert rows["yin"]["effective"] is False
        assert rows["yin"]["sets"] == ["b"]
        assert rows["yin"]["tgt"] == "阴", "未生效词条也要能看到自身译文"
        assert rows["yin"]["variants"][0]["active"] is False
        assert rows["fresh"]["job_fresh"] is True
        assert rows["fresh"]["variants"] == []
        assert cat["stats"] == {
            "total": 3, "sets": 2, "active": 1, "base_entries": 2,
            "job_available": True, "job_count": 2, "effective": 2,
            "job_fresh": 1, "conflicts": 0,
        }

    def test_effective_value_is_the_frozen_job_glossary(self, env):
        """4.1 冻结后改了基础库：生效值仍是任务表，且标冲突 + 漂移。"""
        tmp_cwd, _, cfg_path = env
        _cfg(cfg_path, "[a, b]")
        ts.save_term_set("a", {"terms": [
            {"src": "Qi", "tgt": "气", "note": "n1"}]})
        ts.save_term_set("b", {"terms": [
            {"src": "qi", "tgt": "炁", "note": "n2"}]})
        _job(tmp_cwd, [{"src": "qi", "tgt": "冻结", "note": "n3"}])

        cat = ts.term_catalog()
        assert len(cat["rows"]) == 1, "大小写不同的 Qi/qi 必须是同一行"
        r = cat["rows"][0]
        assert r["src"] == "Qi", "保留首次出现的拼写"
        assert r["tgt"] == "冻结" and r["note"] == "n3"
        assert r["effective"] is True
        assert r["conflict"] is True and r["drift"] is True
        assert [(v["set"], v["tgt"], v["active"]) for v in r["variants"]] == [
            ("a", "气", True), ("b", "炁", True)]
        assert cat["stats"]["conflicts"] == 1

    def test_no_job_glossary_falls_back_to_current_plate(self, env):
        """没有 4.1 产物 → 生效值 = 当前勾选底板，未勾选库标未生效。"""
        _, _, cfg_path = env
        _cfg(cfg_path, "[a]")
        ts.save_term_set("a", {"terms": [{"src": "qi", "tgt": "气"}]})
        ts.save_term_set("b", {"terms": [{"src": "yin", "tgt": "阴"}]})

        cat = ts.term_catalog()
        rows = {r["key"]: r for r in cat["rows"]}
        assert rows["qi"]["effective"] is True
        assert rows["qi"]["in_plate"] is True
        assert rows["yin"]["effective"] is False
        assert cat["stats"]["job_available"] is False
        assert cat["stats"]["job_count"] == 0
        assert cat["stats"]["effective"] == 1

    def test_explicit_zero_active_and_valid_empty_glossary_are_respected(self, env):
        """active: [] + 合法空任务术语表 = 一条术语都不生效，绝不回退 default。"""
        tmp_cwd, _, cfg_path = env
        _cfg(cfg_path, "[]")
        ts.save_term_set("a", {"terms": [{"src": "qi", "tgt": "气"}]})
        _job(tmp_cwd, [])

        cat = ts.term_catalog()
        assert cat["stats"]["job_available"] is True
        assert cat["stats"]["job_count"] == 0
        assert cat["stats"]["effective"] == 0
        assert len(cat["rows"]) == 1
        assert cat["rows"][0]["effective"] is False
        assert cat["rows"][0]["in_plate"] is False

    def test_broken_set_skipped_and_entries_without_src_dropped(self, env):
        tmp_cwd, terms_dir, cfg_path = env
        _cfg(cfg_path, "[a]")
        (terms_dir / "broken.json").write_text("{oops", encoding="utf-8")
        ts.save_term_set("a", {"terms": [
            {"src": "Qi", "tgt": "气"}, {"tgt": "无源"}, "not a dict"]})
        _job(tmp_cwd, [{"src": "qi", "tgt": "气"}])

        cat = ts.term_catalog()
        assert [r["key"] for r in cat["rows"]] == ["qi"]
        assert cat["stats"]["base_entries"] == 1
        assert cat["stats"]["sets"] == 1
        assert [s["id"] for s in cat["sources"]] == ["base:a", "job"], \
            "损坏的库不能出现在按来源清单里"

    def test_sources_expose_every_file_verbatim(self, env):
        """并集把同 src 折成一行，sources 必须让每份文件逐条完整可见。"""
        tmp_cwd, _, cfg_path = env
        _cfg(cfg_path, "[a]")
        ts.save_term_set("a", {"terms": [
            {"src": "Qi", "tgt": "气", "note": "甲"},
            {"src": "yin", "tgt": "阴"}]})
        ts.save_term_set("b", {"terms": [
            {"src": "qi", "tgt": "炁", "note": "乙"}]})
        _job(tmp_cwd, [{"src": "qi", "tgt": "冻结"},
                       {"src": "fresh", "tgt": "新"}])

        cat = ts.term_catalog()
        srcs = {s["id"]: s for s in cat["sources"]}
        assert list(srcs) == ["base:a", "base:b", "job"]
        assert [t["src"] for t in srcs["base:a"]["terms"]] == ["Qi", "yin"]
        assert srcs["base:a"]["terms"][0] == {
            "src": "Qi", "tgt": "气", "note": "甲"}
        assert srcs["base:a"]["path"] == "terms/a.json"
        assert srcs["base:a"]["active"] is True
        assert srcs["base:b"]["active"] is False
        assert srcs["base:b"]["terms"][0]["tgt"] == "炁", \
            "另一套库的取值不能被并集折叠掉"
        assert srcs["job"]["path"] == "output/log/terminology.json"
        assert srcs["job"]["available"] is True
        assert srcs["job"]["count"] == 2
        assert [t["src"] for t in srcs["job"]["terms"]] == ["qi", "fresh"]
        assert len(cat["rows"]) == 3, "并集仍是 3 行，两种视图互补"
        assert sum(s["count"] for s in cat["sources"]) == 5

    def test_job_source_without_4_1_is_empty_not_the_plate(self, env):
        """无 4.1 产物：sources 里也要有任务表位置，但绝不能塞进回退底板。"""
        _, _, cfg_path = env
        _cfg(cfg_path, "[a]")
        ts.save_term_set("a", {"terms": [{"src": "qi", "tgt": "气"}]})

        cat = ts.term_catalog()
        job_src = [s for s in cat["sources"] if s["kind"] == "job"]
        assert len(job_src) == 1
        assert job_src[0]["available"] is False
        assert job_src[0]["count"] == 0 and job_src[0]["terms"] == []
        assert cat["stats"]["job_available"] is False
        assert cat["rows"][0]["effective"] is True, "生效判定仍走底板回退"

    def test_catalog_is_read_only(self, env):
        """看术语不能改任何盘上文件（配置 / 基础库 / 任务术语表）。"""
        tmp_cwd, terms_dir, cfg_path = env
        _cfg(cfg_path, "[a, b]")
        ts.save_term_set("a", {"terms": [{"src": "qi", "tgt": "气"}]})
        ts.save_term_set("b", {"terms": [{"src": "qi", "tgt": "炁"}]})
        job_path = _job(tmp_cwd, [{"src": "qi", "tgt": "冻结"}])
        cfg_file = tmp_cwd / "config.yaml"
        before = {
            "cfg": cfg_file.read_bytes(),
            "job": job_path.read_bytes(),
            "sets": sorted((p.name, p.read_bytes()) for p in terms_dir.iterdir()),
        }

        cat = ts.term_catalog()
        assert len(cat["sources"]) == 3, "两份基础库 + 任务表各自完整可见"

        assert cfg_file.read_bytes() == before["cfg"]
        assert job_path.read_bytes() == before["job"]
        assert sorted((p.name, p.read_bytes())
                      for p in terms_dir.iterdir()) == before["sets"]
