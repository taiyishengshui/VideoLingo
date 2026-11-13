"""步骤 1→2 的 ASR 纠错引擎单测：core/asr_correct.py（纯逻辑部分）

只测不碰网络/模型的部分：
- L1a 实体统一（_unify_entities）的保守性与三道护栏
- 词形对齐（_align_word_form）：大小写 / 标点尾巴必须保留
- L1b 热词标记（_hotword_suggestions）：只标记，绝不改写
- 句子重建（_build_sentences）：靠 whisper 标点尾巴，零成本
- L3 回写（apply_corrections）：索引锚定，行数与时间戳不变
- 断点续跑（_ckpt_*）：指纹失配必须作废，绝不拿错锚点续跑
- 统计（_sentence_stats）：零 API 调用的估价口径
"""

from __future__ import annotations

import json

import pytest

from core import asr_correct as ac


def W(*texts, **kw):
    """快捷造词：[{"text":..., "start":..., "end":...}]"""
    words = []
    for i, t in enumerate(texts):
        w = {"text": t, "start": float(i), "end": float(i) + 0.5}
        w.update(kw)
        words.append(w)
    return words


# ------------------------------------------------------------- 归一化键

class TestWordKey:
    def test_lowercase_and_strip_punctuation(self):
        assert ac._word_key("Meta-Program.") == "metaprogram"
        assert ac._word_key("NLP!") == "nlp"

    def test_keeps_apostrophes(self):
        assert ac._word_key("Don't") == "don't"

    def test_empty_for_punctuation_only(self):
        """纯标点词归一化后是空串 —— 这正是当年 ok[0][0] 崩溃的根因。"""
        assert ac._word_key(".") == ""
        assert ac._word_key("?!") == ""


# ------------------------------------------------- L1a 实体统一（自动改写）

class TestUnifyEntities:
    def test_minority_spelling_folds_into_majority(self):
        words = W(*(["metaprogram"] * 5 + ["metaprogramm"]))
        changes = ac._unify_entities(words)
        assert len(changes) == 1
        assert changes[0] == {
            "index": 5, "old": "metaprogramm", "new": "metaprogram",
            "reason": "entity unification: 'metaprogramm'x1 → 'metaprogram'x5",
            "layer": 1,
        }

    def test_no_change_without_clear_majority(self):
        """5:4 不够支配（gap<2）→ 不动，交给人/LLM 判断。"""
        words = W(*(["metaprogram"] * 5 + ["metaprogramm"] * 4))
        assert ac._unify_entities(words) == []

    def test_majority_must_be_common(self):
        """gap 放宽到 0 时，多数派出现 <3 次依然不动。"""
        words = W("metaprogramm", "metaprogram", "metaprogram")
        assert ac._unify_entities(words, min_count_gap=0) == []
        # 出现 3 次就够格了
        words = W("metaprogramm", *["metaprogram"] * 3)
        assert len(ac._unify_entities(words, min_count_gap=0)) == 1

    def test_plural_is_not_drift(self):
        """step/steps 是形态变化，不是拼写漂移（护栏 2）。"""
        words = W(*(["step"] * 5 + ["steps"]))
        assert ac._unify_entities(words) == []

    def test_case_and_punct_variants_are_not_rewritten(self):
        """句首大写 / 句尾句号必须原样保留（护栏 1，否则 3-1 断句会崩）。"""
        words = W(*(["metaprogram"] * 5 + ["Metaprogram", "metaprogram."]))
        assert ac._unify_entities(words) == []

    def test_short_words_are_ignored(self):
        words = W(*(["cat"] * 5 + ["cot"]))
        assert ac._unify_entities(words) == []

    def test_punctuation_only_words_do_not_crash(self):
        """回归：纯标点词曾经让 vocab 构造 IndexError。"""
        words = W(*(["metaprogram"] * 5 + ["metaprogramm", ".", ",", "!"]))
        changes = ac._unify_entities(words)
        assert [c["index"] for c in changes] == [5]

    def test_unrelated_words_are_left_alone(self):
        words = W("metaphor", "metaphor", "metaphor", "program", "program")
        assert ac._unify_entities(words) == []


# ------------------------------------------------------------ 词形对齐

class TestAlignWordForm:
    @pytest.mark.parametrize("original,replacement,expected", [
        ("Metaprogram", "metaprogram", "Metaprogram"),   # 句首大写保留
        ("metaprogram", "Metaprogram", "metaprogram"),   # 小写 → 小写
        ("NLP", "nlp", "NLP"),                            # 全大写 → 全大写
        ("Metaprogram.", "metaprogram", "Metaprogram."),  # 句号尾巴保留
        ("meta,", "metaprogram", "metaprogram,"),         # 逗号尾巴保留
        ("metaprogram", "metaprogram", "metaprogram"),    # 无变化
    ])
    def test_forms(self, original, replacement, expected):
        assert ac._align_word_form(original, replacement) == expected

    def test_unsafe_replacement_is_dropped(self):
        assert ac._align_word_form("abc", " def") is None

    def test_single_letter_uppercase_is_not_allcaps(self):
        """len==1 的 'I' 不走全大写分支，避免奇怪结果。"""
        assert ac._align_word_form("I", "i") == "I"


# ------------------------------------------------- L1b 热词（只标记不改写）

class TestHotwordSuggestions:
    def test_no_hints_no_suggestions(self):
        assert ac._hotword_suggestions(W("metaprogramm"), {}) == []
        assert ac._hotword_suggestions(W("metaprogramm"), {"hotwords": ""}) == []

    def test_exact_match_is_not_flagged(self):
        words = W("metaprogram")
        assert ac._hotword_suggestions(words, {"hotwords": "metaprogram"}) == []

    def test_near_single_word_is_flagged(self):
        sugg = ac._hotword_suggestions(W("metaprogramm"),
                                       {"hotwords": "metaprogram"})
        assert len(sugg) == 1
        s = sugg[0]
        assert s["index"] == 0 and s["span"] == 1
        assert s["matched"] == "metaprogramm" and s["hotword"] == "metaprogram"
        assert 0.75 <= s["sim"] < 1.0

    def test_short_words_are_not_flagged(self):
        """单词窗口 <4 字符不标：相似度 0.8 够高，但长度闸门拦下（噪声太大）。"""
        assert ac._hotword_suggestions(W("ab"), {"hotwords": "abc"}) == []

    def test_multiword_hotword_span(self):
        sugg = ac._hotword_suggestions(W("meta", "progrm"),
                                       {"hotwords": "meta program"})
        assert len(sugg) == 1
        assert sugg[0]["span"] == 2 and sugg[0]["index"] == 0
        assert sugg[0]["matched"] == "meta progrm"

    def test_exact_multiword_is_not_flagged(self):
        assert ac._hotword_suggestions(W("meta", "program"),
                                       {"hotwords": "meta program"}) == []

    def test_suggestions_never_mutate_words(self):
        """L1b 的核心契约：只举手，不改写。"""
        words = W("metaprogramm")
        snapshot = json.dumps(words, sort_keys=True)
        ac._hotword_suggestions(words, {"hotwords": "metaprogram"})
        assert json.dumps(words, sort_keys=True) == snapshot


# ------------------------------------------------------------- 句子重建

class TestBuildSentences:
    def test_punctuation_tails_end_sentences(self):
        words = W("Hello", "world.", "Bye", "now!")
        sents = ac._build_sentences(words)
        assert [s["indices"] for s in sents] == [[0, 1], [2, 3]]
        assert sents[0]["text"] == "Hello world."

    def test_trailing_fragment_becomes_a_sentence(self):
        sents = ac._build_sentences(W("Hello", "world.", "Bye"))
        assert [s["indices"] for s in sents] == [[0, 1], [2]]

    def test_quote_after_period_still_ends(self):
        sents = ac._build_sentences(W("He", 'said.', '"Ok."'))
        assert [s["indices"] for s in sents] == [[0, 1], [2]]

    def test_hard_cap_splits_long_run(self, monkeypatch):
        monkeypatch.setattr(ac, "_MAX_SENTENCE_WORDS", 3)
        sents = ac._build_sentences(W(*[f"w{i}" for i in range(7)]))
        assert [len(s["indices"]) for s in sents] == [3, 3, 1]

    def test_empty_text_words_are_skipped(self):
        """回写时被合并掉的空槽位不能进句子。"""
        words = [{"text": "a", "start": 0, "end": 1},
                 {"text": "   ", "start": 1, "end": 2},
                 {"text": "b.", "start": 2, "end": 3}]
        sents = ac._build_sentences(words)
        assert sents[0]["indices"] == [0, 2]


# ------------------------------------------------------------- L3 回写

class TestApplyCorrections:
    def test_single_word_fix(self):
        words = W("metaprogramm", "is", "good.")
        ac.apply_corrections(words, [
            {"index": 0, "old": "metaprogramm", "new": "metaprogram"}])
        assert words[0]["text"] == "metaprogram"

    def test_multiword_span_merges_into_first_slot(self):
        words = W("meta", "program", "is", "good.")
        ac.apply_corrections(words, [
            {"index": 0, "old": "meta program", "new": "metaprogram", "span": 2}])
        assert [w["text"] for w in words] == ["metaprogram", "", "is", "good."]

    def test_row_count_and_timestamps_unchanged(self):
        words = W("meta", "program", "is", "good.")
        before = [(w["start"], w["end"]) for w in words]
        ac.apply_corrections(words, [
            {"index": 0, "old": "meta program", "new": "metaprogram", "span": 2}])
        assert len(words) == 4
        assert [(w["start"], w["end"]) for w in words] == before

    def test_stale_anchor_is_skipped(self):
        """锚点对不上（词已被改过）→ 丢弃该条修改，绝不写错位置。"""
        words = W("metaprogram", "is", "good.")
        ac.apply_corrections(words, [
            {"index": 0, "old": "metaphor", "new": "metafore"}])
        assert words[0]["text"] == "metaprogram"

    def test_out_of_range_index_is_ignored(self):
        words = W("a", "b")
        ac.apply_corrections(words, [{"index": 9, "old": "a", "new": "z"}])
        assert [w["text"] for w in words] == ["a", "b"]

    def test_changes_are_applied_in_index_order(self):
        """先排序再回写：乱序传入也不能错位。"""
        words = W("one", "two", "three")
        ac.apply_corrections(words, [
            {"index": 2, "old": "three", "new": "THREE"},
            {"index": 0, "old": "one", "new": "ONE"}])
        assert [w["text"] for w in words] == ["ONE", "two", "THREE"]


# ------------------------------------------------------------ 断点续跑

@pytest.fixture
def ckpt_path(tmp_path, monkeypatch):
    p = tmp_path / "log" / "asr_correct_ckpt.json"
    monkeypatch.setattr(ac, "_CKPT_FILE", str(p))
    return p


class TestCheckpoint:
    def test_store_fingerprint(self):
        assert ac._store_fingerprint([]) == "empty"
        assert ac._store_fingerprint(W("Hello", "world", "Hi")) == "3:Hello:Hi"

    def test_fingerprint_changes_with_store(self):
        assert ac._store_fingerprint(W("a", "b")) != ac._store_fingerprint(W("a", "c"))

    def test_cfg_fingerprint_tracks_tunables(self, monkeypatch):
        """改了影响打包的参数 → 指纹必须变，旧 ckpt 作废。"""
        base = ac._cfg_fingerprint()
        monkeypatch.setattr(ac, "_PACK_WORDS", 100)
        assert ac._cfg_fingerprint() != base

    def test_roundtrip(self, ckpt_path):
        ck = dict(ac._CKPT_EMPTY)
        ck.update(fingerprint="fp", cfg="cfg",
                  done_batches=[{"first_k": 0, "n": 5, "len": 3}])
        ac._ckpt_save(ck)
        loaded = ac._ckpt_load("fp", "cfg")
        assert loaded["done_batches"] == [{"first_k": 0, "n": 5, "len": 3}]

    def test_missing_file(self, ckpt_path):
        assert ac._ckpt_load("fp", "cfg") is None

    def test_stale_fingerprint_is_discarded(self, ckpt_path):
        ck = dict(ac._CKPT_EMPTY)
        ck.update(fingerprint="old-store", cfg="cfg")
        ac._ckpt_save(ck)
        assert ac._ckpt_load("new-store", "cfg") is None

    def test_cfg_mismatch_is_discarded(self, ckpt_path):
        ck = dict(ac._CKPT_EMPTY)
        ck.update(fingerprint="fp", cfg="old-cfg")
        ac._ckpt_save(ck)
        assert ac._ckpt_load("fp", "new-cfg") is None

    def test_corrupt_file_is_discarded(self, ckpt_path):
        ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        ckpt_path.write_text("{broken", encoding="utf-8")
        assert ac._ckpt_load("fp", "cfg") is None

    def test_done_set(self):
        ck = {"done_batches": [{"first_k": 0, "n": 5}, {"first_k": 5, "n": 2}]}
        assert ac._ckpt_done_set(ck) == {(0, 5), (5, 2)}
        assert ac._ckpt_done_set(None) == set()

    def test_delete(self, ckpt_path):
        ac._ckpt_save(dict(ac._CKPT_EMPTY))
        ac._ckpt_delete()
        assert not ckpt_path.exists()
        ac._ckpt_delete()   # 幂等：文件不存在也不能抛


# --------------------------------------------------------------- 统计

class TestSentenceStats:
    def test_counts_suspects(self):
        words = [{"text": "Hello", "start": 0, "end": 1, "score": 0.9},
                 {"text": "world.", "start": 1, "end": 2, "score": 0.3},
                 {"text": "Bye", "start": 2, "end": 3, "score": 0.95}]
        st = ac._sentence_stats(words, None)
        assert st["sentences"] == 2
        assert st["eligible_sents"] == 1
        assert st["low_only"] == 1 and st["total_suspects"] == 1
        assert st["eligible_words"] == 2
        assert st["est_calls"] == 1

    def test_no_suspects_no_cost(self):
        words = [{"text": "Hello", "start": 0, "end": 1, "score": 0.9},
                 {"text": "world.", "start": 1, "end": 2, "score": 0.8}]
        st = ac._sentence_stats(words, None)
        assert st["eligible_sents"] == 0 and st["eligible_words"] == 0
        assert st["est_calls"] == 1      # 至少报 1，不为 0（UI 语义）

    def test_hotword_flag_counts_as_suspect(self):
        words = W("metaprogramm", "is", "good.")
        st = ac._sentence_stats(words, {"hotwords": "metaprogram"})
        assert st["suggestions"] == 1
        assert st["total_suspects"] == 1 and st["eligible_sents"] == 1

    def test_short_low_score_words_are_not_suspects(self):
        """归一化后 <2 字符的低分词不计数（噪声）。"""
        words = [{"text": "a.", "start": 0, "end": 1, "score": 0.1}]
        st = ac._sentence_stats(words, None)
        assert st["total_suspects"] == 0

    def test_missing_score_field_is_not_low(self):
        words = W("hello")
        st = ac._sentence_stats(words, None)
        assert st["total_suspects"] == 0
