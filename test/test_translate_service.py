"""步骤 4（translate）核心机制的单测：分块 / 断点续跑 / 术语库 / 共享上下文。

被测对象：core/services/translate_service.py
- split_chunks 是"分块规则"的唯一来源（字符数 AND 句数双重上限），
  3-2/4-2/playground 都调它 — 这里锁死它的边界行为。
- TranslationCkpt 是断点续跑的唯一实现（原子写 + 索引锚点）。
- 术语库 I/O 与 load_active_terms 的合并优先级（后者覆盖前者）。
- build_shared_context 只产出"裸内容"，表头归模板（防止双表头回归）。

全部零网络、零 LLM 调用；落盘一律在 tmp_path。
"""

from __future__ import annotations

import json

import pytest

from core.services import translate_service as ts
from conftest import TRANSLATE_CONFIG


# ------------------------------------------------------------------ 分块

def _sents(n: int, width: int = 4):
    """n 句，每句 width 个字符（内容不同，便于断言归属）。

    只用 ASCII：chr(ord('a')+i) 超过 26 会撞上 \x85 / \xa0 这类被
    strip() 当成空白的字符，句子会被静默丢掉。
    """
    return [f"s{i:0{width - 1}d}" for i in range(n)]


class TestSplitChunks:
    def test_char_limit_breaks_chunk(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        # chunk_size=10：前两句共 4+1+4=9 放得下，第三句会超 → 断开
        assert ts.split_chunks(["aaaa", "bbbb", "cccc"], 10, 10) == [
            "aaaa\nbbbb", "cccc"]

    def test_sentence_limit_breaks_chunk(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        # 字符上限给足，只让句数上限生效
        assert ts.split_chunks(["aaaa", "bbbb", "cccc"], 1000, 2) == [
            "aaaa\nbbbb", "cccc"]

    def test_config_driven_defaults(self, cfg_path):
        """不传参数时走配置：translate.chunk_size / max_sentences。"""
        cfg_path(TRANSLATE_CONFIG)          # chunk_size=100, max_sentences=3
        chunks = ts.split_chunks(["x" * 40 for _ in range(7)])
        assert [c.count("\n") + 1 for c in chunks] == [2, 2, 2, 1]

    def test_overlong_sentence_is_never_split_or_merged(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        chunks = ts.split_chunks(["aaaa", "b" * 40, "cc"], 5, 10)
        # 单句超长：自成一块，不截断、不与邻居合并
        assert chunks == ["aaaa", "b" * 40, "cc"]

    def test_raw_text_input_normalizes_crlf_and_blanks(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        assert ts.split_chunks("a\r\nb\n\nc\r\n", 100, 10) == ["a\nb\nc"]

    def test_single_blob_element_is_split(self, cfg_path):
        """playground 传的是 ['多行文本']：只有 \n 才算 blob。"""
        cfg_path(TRANSLATE_CONFIG)
        assert ts.split_chunks(["a\nb\nc"], 100, 10) == ["a\nb\nc"]

    def test_trailing_cr_is_not_a_blob(self, cfg_path):
        """单行末尾带 \r 是 CRLF 残留，不能当成多行 blob 切碎。"""
        cfg_path(TRANSLATE_CONFIG)
        assert ts.split_chunks(["hello\r"], 100, 10) == ["hello"]

    def test_empty_inputs(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        assert ts.split_chunks([], 100, 10) == []
        assert ts.split_chunks("", 100, 10) == []
        assert ts.split_chunks(["", "  ", "\n"], 100, 10) == []

    def test_blank_lines_are_dropped(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        assert ts.split_chunks(["a", "", "b"], 100, 10) == ["a\nb"]

    def test_all_sentences_preserved_in_order(self, cfg_path):
        """分块只切不丢：拼接回来必须等于原文顺序。"""
        cfg_path(TRANSLATE_CONFIG)
        sents = _sents(50)
        joined = "\n".join(ts.split_chunks(sents, 30, 4))
        assert joined == "\n".join(sents)


class TestChunkStats:
    def test_stats(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        st = ts.chunk_stats(["ab", "cdef"])
        assert st["count"] == 2
        assert st["chars"] == {"total": 6, "min": 2, "max": 4, "avg": 3}
        assert st["sentences"]["total"] == 2

    def test_stats_empty(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        st = ts.chunk_stats([])
        assert st["count"] == 0 and st["chars"]["total"] == 0


# ------------------------------------------------------------- 断点续跑

class TestTranslationCkpt:
    def test_put_get_missing(self, tmp_path, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        ck = ts.TranslationCkpt(str(tmp_path / "log" / "ckpt.json"))
        assert ck.get(0) is None
        ck.put(0, {"lines": 2, "translation": "你好"})
        ck.put(2, {"lines": 1, "translation": "世界"})
        assert ck.get(0)["translation"] == "你好"
        assert ck.missing(4) == [1, 3]

    def test_persists_across_instances(self, tmp_path, cfg_path):
        """重开一个实例（模拟重跑）必须能续上。"""
        cfg_path(TRANSLATE_CONFIG)
        p = str(tmp_path / "ckpt.json")
        ts.TranslationCkpt(p).put(1, {"lines": 1, "translation": "x"})
        again = ts.TranslationCkpt(p)
        assert again.get(1) == {"lines": 1, "translation": "x"}
        assert again.missing(3) == [0, 2]

    def test_atomic_write_leaves_no_tmp(self, tmp_path, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        p = tmp_path / "ckpt.json"
        ts.TranslationCkpt(str(p)).put(0, {"lines": 1, "translation": "x"})
        assert not (tmp_path / "ckpt.json.tmp").exists()

    def test_index_keys_are_strings_on_disk(self, tmp_path, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        p = tmp_path / "ckpt.json"
        ts.TranslationCkpt(str(p)).put(0, {"lines": 1, "translation": "x"})
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert list(raw["chunks"]) == ["0"]

    def test_corrupt_file_falls_back_to_empty(self, tmp_path, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        p = tmp_path / "ckpt.json"
        p.write_text("{not json", encoding="utf-8")
        ck = ts.TranslationCkpt(str(p))
        assert ck.missing(2) == [0, 1]

    def test_clear(self, tmp_path, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        p = str(tmp_path / "ckpt.json")
        ck = ts.TranslationCkpt(p)
        ck.put(0, {"lines": 1, "translation": "x"})
        ck.clear()
        assert ck.get(0) is None
        assert ts.TranslationCkpt(p).missing(1) == [0]


class TestCkptFingerprint:
    """改了分块参数/重跑切句后，旧块索引指向的是别的内容——必须作废。"""

    def test_same_input_same_fingerprint(self):
        sents = ["a", "b", "c"]
        assert ts.chunks_fingerprint(sents, 2) == ts.chunks_fingerprint(sents, 2)

    @pytest.mark.parametrize("other_sents,other_n", [
        (["a", "b", "x"], 2),      # 源文本变了
        (["a", "b", "c"], 3),      # 分块参数变了 → 块数变了
    ])
    def test_changed_input_changes_fingerprint(self, other_sents, other_n):
        assert ts.chunks_fingerprint(["a", "b", "c"], 2) != \
            ts.chunks_fingerprint(other_sents, other_n)

    def test_matching_fingerprint_keeps_chunks(self, tmp_path, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        p = str(tmp_path / "ckpt.json")
        ck = ts.TranslationCkpt(p)
        ck.ensure_fingerprint("fp-1")
        ck.put(0, {"lines": 1, "translation": "x"})
        # 重跑：同一份输入 → 断点保留
        again = ts.TranslationCkpt(p)
        assert again.ensure_fingerprint("fp-1") is True
        assert again.get(0)["translation"] == "x"

    def test_stale_fingerprint_drops_chunks(self, tmp_path, cfg_path):
        """分块变了还复用 = 译文错位到别的句子上，比从头跑更糟。"""
        cfg_path(TRANSLATE_CONFIG)
        p = str(tmp_path / "ckpt.json")
        ck = ts.TranslationCkpt(p)
        ck.ensure_fingerprint("fp-1")
        ck.put(0, {"lines": 1, "translation": "x"})
        again = ts.TranslationCkpt(p)
        assert again.ensure_fingerprint("fp-2") is False
        assert again.get(0) is None
        assert again.missing(1) == [0]

    def test_clear_keeps_the_fingerprint(self, tmp_path, cfg_path):
        """产物落盘后 clear：指纹留下，下次同输入不会误判成"输入变了"。"""
        cfg_path(TRANSLATE_CONFIG)
        p = str(tmp_path / "ckpt.json")
        ck = ts.TranslationCkpt(p)
        ck.ensure_fingerprint("fp-1")
        ck.put(0, {"lines": 1, "translation": "x"})
        ck.clear()
        again = ts.TranslationCkpt(p)
        assert again.ensure_fingerprint("fp-1") is True
        assert again.get(0) is None


# ---------------------------------------------------------------- 术语库

@pytest.fixture
def terms_dir(tmp_path, monkeypatch):
    d = tmp_path / "terms"
    d.mkdir()
    monkeypatch.setattr(ts, "TERMS_DIR", str(d))
    return d


class TestTermSets:
    def test_save_list_load(self, terms_dir, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        ts.save_term_set("medical", {"desc": "医学", "terms": [
            {"src": "qi", "tgt": "气"}, {"src": "yin", "tgt": "阴"}]})
        assert ts.list_term_sets() == [
            {"name": "medical", "desc": "医学", "count": 2}]
        assert ts.load_term_set("medical")["terms"][0]["tgt"] == "气"

    def test_list_sorted_and_ignores_non_json(self, terms_dir, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        ts.save_term_set("b", {"terms": []})
        ts.save_term_set("a", {"terms": [{"src": "x", "tgt": "y"}]})
        (terms_dir / "notes.txt").write_text("ignore me", encoding="utf-8")
        assert [x["name"] for x in ts.list_term_sets()] == ["a", "b"]

    def test_broken_json_is_skipped(self, terms_dir, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        (terms_dir / "bad.json").write_text("{oops", encoding="utf-8")
        assert ts.list_term_sets() == []

    def test_load_active_terms_merges_in_order(self, terms_dir, cfg_path):
        cfg_path("""
llm:
  endpoints: {}
  models: {}
  chains: {}
  tasks: {}
terminology:
  active: [a, b]
""")
        ts.save_term_set("a", {"terms": [{"src": "qi", "tgt": "气"}]})
        ts.save_term_set("b", {"terms": [{"src": "yin", "tgt": "阴"}]})
        merged = ts.load_active_terms()["terms"]
        assert [(t["src"], t["tgt"]) for t in merged] == [("qi", "气"), ("yin", "阴")]

    def test_later_set_overrides_same_src(self, terms_dir, cfg_path):
        """同名术语 src 大小写不敏感，后激活的库胜出（有告警）。"""
        cfg_path("""
llm:
  endpoints: {}
  models: {}
  chains: {}
  tasks: {}
terminology:
  active: [a, b]
""")
        ts.save_term_set("a", {"terms": [{"src": "Qi", "tgt": "气"}]})
        ts.save_term_set("b", {"terms": [{"src": "qi", "tgt": "炁", "note": "n"}]})
        merged = ts.load_active_terms()["terms"]
        assert len(merged) == 1
        assert merged[0]["tgt"] == "炁" and merged[0]["note"] == "n"

    def test_entries_without_src_are_dropped(self, terms_dir, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        ts.save_term_set("default", {"terms": [
            {"src": "qi", "tgt": "气"}, {"tgt": "无源"}, "not a dict"]})
        assert [t["src"] for t in ts.load_active_terms()["terms"]] == ["qi"]

    def test_missing_set_is_skipped(self, terms_dir, cfg_path):
        cfg_path("""
llm:
  endpoints: {}
  models: {}
  chains: {}
  tasks: {}
terminology:
  active: [nope, default]
""")
        ts.save_term_set("default", {"terms": [{"src": "qi", "tgt": "气"}]})
        assert len(ts.load_active_terms()["terms"]) == 1

    def test_active_names_default(self, cfg_path):
        """terminology.active 缺失 → 回落 ['default']。"""
        cfg_path("llm:\n  endpoints: {}\n  models: {}\n  chains: {}\n  tasks: {}\n")
        assert ts.active_term_names() == ["default"]


# ------------------------------------------------------------ 共享上下文

class TestSharedContext:
    def test_builds_raw_pieces_only(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        parts = ts.build_shared_context(
            "这是摘要",
            {"terms": [{"src": "qi", "tgt": "气"},
                       {"src": "yin", "tgt": "阴", "note": "名词"}]},
            ["前一句", "前二句"],
            ["后一句"],
        )
        assert parts["summary"] == "这是摘要"
        assert parts["terms"] == "- qi => 气\n- yin => 阴  (名词)"
        assert parts["previous_content"] == "前一句\n前二句"
        assert parts["subsequent_content"] == "后一句"
        # 回归：这里绝不能出现表头（表头只属于模板），否则 UI 会出现双表头
        assert "###" not in parts["terms"]
        assert "Summary" not in parts["terms"]

    def test_empty_inputs(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        parts = ts.build_shared_context(None, {}, None, None)
        assert parts == {"summary": "", "terms": "",
                         "previous_content": "", "subsequent_content": ""}

    def test_terms_none_or_missing_key(self, cfg_path):
        cfg_path(TRANSLATE_CONFIG)
        assert ts.build_shared_context("s", {"terms": None}, None, None)["terms"] == ""
        assert ts.build_shared_context("s", {}, None, None)["terms"] == ""

    def test_render_goes_through_shared_template(self, cfg_path, monkeypatch):
        """render_shared_context 必须走 core.prompts 的共享模板（唯一出口）。"""
        cfg_path(TRANSLATE_CONFIG)
        seen = {}

        def _fake(prev, after, summary, terms):
            seen.update(prev=prev, after=after, summary=summary, terms=terms)
            return f"<{summary}|{terms}|{prev}|{after}>"

        import core.prompts as prompts
        monkeypatch.setattr(prompts, "generate_shared_prompt", _fake)
        out = ts.render_shared_context({"summary": "S", "terms": "T",
                                        "previous_content": "P",
                                        "subsequent_content": "A"})
        assert out == "<S|T|P|A>"
        assert seen == {"prev": "P", "after": "A", "summary": "S", "terms": "T"}


# ------------------------------------------- 任务术语表（v1.15 用户模型）

TERMS_CFG_ZERO = """
llm:
  endpoints: {}
  models: {}
  chains: {}
  tasks: {}
terminology:
  active: []
"""

TERMS_CFG_A = """
llm:
  endpoints: {}
  models: {}
  chains: {}
  tasks: {}
terminology:
  active: [a]
"""


def _write_job_glossary(cwd, terms, **extra):
    """模拟 4.1 写出的任务术语表（相对 cwd，每个任务一份）。"""
    p = cwd / "output" / "log" / "terminology.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"terms": terms}
    data.update(extra)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


class TestJobTerms:
    def test_unchecking_all_means_zero_base_sets(self, cfg_path, terms_dir):
        """用户取消全部勾选 = 0 套基础库；不能偷偷回退到 default。"""
        cfg_path(TERMS_CFG_ZERO)
        ts.save_term_set("default", {"terms": [{"src": "qi", "tgt": "气"}]})
        assert ts.active_term_names() == []
        assert ts.load_active_terms() == {"terms": []}

    def test_missing_config_key_still_defaults(self, cfg_path, terms_dir):
        """兼容旧配置：键缺失才回退 default，显式 [] 不回退。"""
        cfg_path("llm:\n  endpoints: {}\n  models: {}\n  chains: {}\n  tasks: {}\n")
        assert ts.active_term_names() == ["default"]

    def test_job_glossary_is_base_plus_new_and_not_re_merged(
            self, cfg_path, terms_dir, tmp_cwd):
        """4.2 只读 4.1 产物：新抽的术语在、底板不重复叠加。

        当前勾选已改成 0 套，但任务术语表是 4.1 时冻结的快照，仍生效。
        """
        cfg_path(TERMS_CFG_ZERO)
        ts.save_term_set("default", {"terms": [
            {"src": "should-not-appear", "tgt": "不该出现"}]})
        _write_job_glossary(tmp_cwd, [
            {"src": "fresh", "tgt": "新抽"},
            {"src": "base", "tgt": "底板"}], theme="摘要")
        assert [(t["src"], t["tgt"]) for t in ts.load_job_terms()["terms"]] \
            == [("fresh", "新抽"), ("base", "底板")]

    def test_missing_glossary_falls_back_to_bottom_plate(
            self, cfg_path, terms_dir, tmp_cwd):
        """只跑 4.2 / 新工作区：没有 4.1 产物时用勾选底板。"""
        cfg_path(TERMS_CFG_A)
        ts.save_term_set("a", {"terms": [{"src": "qi", "tgt": "气"}]})
        assert [t["src"] for t in ts.load_job_terms()["terms"]] == ["qi"]

    def test_broken_glossary_falls_back(self, cfg_path, terms_dir, tmp_cwd):
        cfg_path(TERMS_CFG_A)
        ts.save_term_set("a", {"terms": [{"src": "qi", "tgt": "气"}]})
        p = tmp_cwd / "output" / "log" / "terminology.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{oops", encoding="utf-8")
        assert [t["src"] for t in ts.load_job_terms()["terms"]] == ["qi"]

    def test_valid_empty_glossary_is_not_overridden(
            self, cfg_path, terms_dir, tmp_cwd):
        """合法的空任务术语表 = 有意不启用术语，不静默重新注入底板。"""
        cfg_path(TERMS_CFG_A)
        ts.save_term_set("a", {"terms": [{"src": "qi", "tgt": "气"}]})
        _write_job_glossary(tmp_cwd, [])
        assert ts.load_job_terms() == {"terms": []}

    def test_merge_terms_dedups_case_insensitively(self):
        """同 src 后者（底板）覆盖前者（LLM 猜测），位置保持在首次出现处。"""
        merged = ts.merge_terms(
            [{"src": "Qi", "tgt": "LLM 猜的", "note": ""}],
            [{"src": "qi", "tgt": "人工底板", "note": "以基础库为准"}],
            [{"tgt": "无源"}, "not a dict"],
        )
        assert len(merged) == 1
        assert merged[0]["tgt"] == "人工底板"
        assert merged[0]["note"] == "以基础库为准"

    def test_terms_signature_invalidates_stale_chunks(self):
        """术语变了 → 断点指纹变；术语没变 → 指纹稳定。"""
        sents = ["a", "b"]
        sig1 = ts.terms_signature({"terms": [{"src": "qi", "tgt": "气"}]})
        sig2 = ts.terms_signature({"terms": [{"src": "qi", "tgt": "炁"}]})
        assert sig1 != sig2
        f1 = ts.chunks_fingerprint(sents, 2, sig1)
        assert f1 == ts.chunks_fingerprint(sents, 2, sig1)
        assert f1 != ts.chunks_fingerprint(sents, 2, sig2)
        assert f1 != ts.chunks_fingerprint(sents, 2)

    def test_summary_prompt_omits_empty_existing_terms_section(self, monkeypatch):
        """0 勾选时不给 LLM 注入一个空的 Existing Terms 节。"""
        import core.prompts as prompts
        seen = {}

        def _fake_render(name, kw, template=None):
            seen.update(kw)
            return "rendered"

        monkeypatch.setattr(prompts, "render_prompt", _fake_render)
        prompts.get_summary_prompt("text", {"terms": []},
                                   src_lang="en", tgt_lang="zh")
        assert seen["terms_note"] == ""
        prompts.get_summary_prompt(
            "text", {"terms": [{"src": "qi", "tgt": "气", "note": ""}]},
            src_lang="en", tgt_lang="zh")
        assert "Existing Terms" in seen["terms_note"]


class TestStep41Glossary:
    def test_writes_base_plus_new_deduped(self, cfg_path, terms_dir,
                                          tmp_cwd, monkeypatch):
        """4.1 写盘 = LLM 新抽 + 勾选底板，去重且底板优先，theme 保留。"""
        cfg_path(TERMS_CFG_A)
        ts.save_term_set("a", {"terms": [
            {"src": "qi", "tgt": "气", "note": ""}]})
        from core import _4_1_summarize as s
        monkeypatch.setattr(s, "migrate_custom_terms_xlsx", lambda: 0)
        monkeypatch.setattr(s, "combine_chunks", lambda: "full transcript")
        monkeypatch.setattr(s, "get_summary_prompt", lambda src, terms: "prompt")
        monkeypatch.setattr(s, "ask_gpt", lambda *a, **kw: {
            "theme": "摘要",
            "terms": [{"src": "fresh", "tgt": "新抽", "note": ""},
                      {"src": "Qi", "tgt": "LLM 猜的", "note": ""}],
        })
        s.get_summary()
        p = tmp_cwd / "output" / "log" / "terminology.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["theme"] == "摘要"
        # 大小写不敏感去重：底板覆盖译文，但保留首次出现的 src 拼写
        # （与旧 load_active_terms 行为一致）
        assert [(t["src"], t["tgt"]) for t in data["terms"]] \
            == [("fresh", "新抽"), ("Qi", "气")]
        assert (tmp_cwd / "output" / "log" / "summary.txt").read_text(
            encoding="utf-8") == "摘要"


class TestWriteBackTerms:
    """勾选回写（v1.16）：唯一写入决策、同名默认跳过、任务术语表只读。"""

    def _job(self, tmp_cwd, terms):
        p = tmp_cwd / "output" / "log" / "terminology.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"theme": "摘要", "terms": terms},
                                ensure_ascii=False), encoding="utf-8")
        return p

    def test_creates_new_set_from_job_values(self, cfg_path, terms_dir, tmp_cwd):
        cfg_path(TRANSLATE_CONFIG)
        self._job(tmp_cwd, [
            {"src": "self-worth", "tgt": "自我价值感", "note": "n1"},
            {"src": "qi", "tgt": "气", "note": "n2"},
        ])
        out = ts.write_back_terms("fresh_set", ["self-worth"])
        assert out == {"target": "fresh_set", "added": 1, "updated": 0,
                       "skipped": 0, "total": 1}
        data = ts.load_term_set("fresh_set")
        assert data["terms"] == [{"src": "self-worth", "tgt": "自我价值感",
                                  "note": "n1"}]
        assert "回写" in data["desc"]

    def test_existing_src_is_skipped_and_manual_translation_kept(
            self, cfg_path, terms_dir, tmp_cwd):
        cfg_path(TRANSLATE_CONFIG)
        ts.save_term_set("manual", {"desc": "人工维护", "terms": [
            {"src": "Qi", "tgt": "炁", "note": "人工"}]})
        self._job(tmp_cwd, [{"src": "qi", "tgt": "LLM 气", "note": "LLM"}])
        out = ts.write_back_terms("manual", ["qi"])
        assert (out["added"], out["updated"], out["skipped"]) == (0, 0, 1)
        data = ts.load_term_set("manual")
        assert data["desc"] == "人工维护"
        assert data["terms"] == [{"src": "Qi", "tgt": "炁", "note": "人工"}]

    def test_overwrite_updates_tgt_and_note(self, cfg_path, terms_dir, tmp_cwd):
        cfg_path(TRANSLATE_CONFIG)
        ts.save_term_set("manual", {"desc": "d", "terms": [
            {"src": "qi", "tgt": "旧", "note": "旧注"}]})
        self._job(tmp_cwd, [{"src": "QI", "tgt": "新", "note": "新注"}])
        out = ts.write_back_terms("manual", ["QI"], overwrite=True)
        assert (out["added"], out["updated"], out["skipped"]) == (0, 1, 0)
        assert ts.load_term_set("manual")["terms"][0] == {
            "src": "qi", "tgt": "新", "note": "新注"}

    def test_no_job_glossary_never_writes_back_the_fallback_plate(
            self, cfg_path, terms_dir, tmp_cwd):
        cfg_path(TRANSLATE_CONFIG)
        ts.save_term_set("default", {"terms": [{"src": "qi", "tgt": "气"}]})
        assert ts.load_job_terms(allow_fallback=False) is None
        assert ts.load_job_terms()["terms"]        # 默认仍回退底板给 4.2
        with pytest.raises(ValueError, match="没有任务术语表"):
            ts.write_back_terms("default", ["qi"])
        assert ts.load_term_set("default")["terms"] == [{"src": "qi", "tgt": "气"}]

    def test_corrupt_target_is_refused_not_rebuilt(
            self, cfg_path, terms_dir, tmp_cwd):
        cfg_path(TRANSLATE_CONFIG)
        self._job(tmp_cwd, [{"src": "fresh", "tgt": "新"}])
        bad = terms_dir / "broken.json"
        bad.write_text("{oops", encoding="utf-8")
        with pytest.raises(ValueError, match="损坏"):
            ts.write_back_terms("broken", ["fresh"])
        assert bad.read_text(encoding="utf-8") == "{oops"

    def test_invalid_inputs_never_write(self, cfg_path, terms_dir, tmp_cwd):
        cfg_path(TRANSLATE_CONFIG)
        self._job(tmp_cwd, [{"src": "qi", "tgt": "气"}])
        for target, srcs in [("../evil", ["qi"]), ("", ["qi"]),
                             ("ok", []), ("ok", ["not-in-job"])]:
            with pytest.raises(ValueError):
                ts.write_back_terms(target, srcs)
        assert ts.list_term_sets() == []

    def test_job_glossary_is_never_modified(self, cfg_path, terms_dir, tmp_cwd):
        cfg_path(TRANSLATE_CONFIG)
        p = self._job(tmp_cwd, [{"src": "qi", "tgt": "气"}])
        before = p.read_text(encoding="utf-8")
        ts.write_back_terms("base", ["qi"])
        assert p.read_text(encoding="utf-8") == before

    def test_shared_name_rule_and_create_confirmation(
            self, cfg_path, terms_dir, tmp_cwd):
        cfg_path(TRANSLATE_CONFIG)
        with pytest.raises(ValueError):
            ts.create_or_replace_term_set("../evil", {"terms": []}, create=True)
        ts.save_term_set("a", {"desc": "保留", "terms": [
            {"src": "x", "tgt": "y"}]})
        with pytest.raises(FileExistsError):
            ts.create_or_replace_term_set("a", {"terms": []}, create=True)
        assert ts.load_term_set("a")["terms"] == [{"src": "x", "tgt": "y"}]
        out = ts.create_or_replace_term_set(
            "a", {"terms": []}, create=True, overwrite=True)
        assert out == {"name": "a", "count": 0, "created": False}
        assert ts.load_term_set("a")["desc"] == "保留"
        # merge_terms 清洗：丢空 src、同 src 后者覆盖（保留首次出现的拼写）
        created = ts.create_or_replace_term_set("new", {"terms": [
            {"src": "", "tgt": "丢"}, {"src": "a", "tgt": "旧"},
            {"src": "A", "tgt": "新"}]}, create=True)
        assert created == {"name": "new", "count": 1, "created": True}
        assert ts.load_term_set("new")["terms"][0]["tgt"] == "新"
