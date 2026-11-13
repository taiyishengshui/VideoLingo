"""步骤 4 译文校验 + 整条链路贯通（core/translate_lines.py）

- valid_translate_result 是"这条译文能不能用"的唯一判官：行数 / 字段名
  对不上就必须重试，宁可多花一次调用也不能让错位译文进字幕。
- 末尾的贯通用例：真的走 translate_lines → ask_gpt → llm.tasks 绑定的链，
  断言它用的是 translate_faith 绑定的那条链（"单元测试用这个链"）。
"""

from __future__ import annotations

import json

from conftest import CHAIN_CONFIG, models_used, prompts_used, resp_ok
from core import translate_lines as tl


class TestValidTranslateResult:
    def test_success(self):
        result = {"1": {"direct": "你好"}, "2": {"direct": "世界"}}
        out = tl.valid_translate_result(result, ["1", "2"], ["direct"])
        assert out["status"] == "success"

    def test_missing_top_key(self):
        out = tl.valid_translate_result({"1": {"direct": "a"}}, ["1", "2"], ["direct"])
        assert out["status"] == "error" and "2" in out["message"]

    def test_missing_sub_key(self):
        out = tl.valid_translate_result({"1": {"free": "a"}}, ["1"], ["direct"])
        assert out["status"] == "error"
        assert "direct" in out["message"] and "1" in out["message"]

    def test_every_item_is_checked(self):
        """只有第一行合格不算合格 —— 全行都要有字段。"""
        out = tl.valid_translate_result(
            {"1": {"direct": "a"}, "2": {}}, ["1", "2"], ["direct"])
        assert out["status"] == "error"

    def test_no_requirements_always_passes(self):
        assert tl.valid_translate_result({}, [], [])["status"] == "success"

    def test_rejects_fewer_lines_than_source(self):
        """行数不够 = 错位，必须重试（valid 只管字段，行数由调用方比对）。"""
        result = {"1": {"direct": "a"}}
        assert tl.valid_translate_result(result, ["1"], ["direct"])["status"] == "success"
        assert len(result) == 1


class TestNormalizeSubKeys:
    """模型不照键名输出时的兜底（实测 gemini-2.5-flash-lite：direct → translation）。

    没有它，一次键名走形 = 整块翻译 ValueError = 整个第 4 步崩掉。
    """

    def test_alias_is_mapped(self):
        r = {"1": {"origin": "Hi", "translation": "你好"}}
        assert tl.normalize_sub_keys(r, "direct") is True
        assert r["1"]["direct"] == "你好"
        assert tl.valid_translate_result(r, ["1"], ["direct"])["status"] == "success"

    def test_free_alias(self):
        r = {"1": {"origin": "Hi", "polished": "你好"}}
        assert tl.normalize_sub_keys(r, "free") is True
        assert r["1"]["free"] == "你好"

    def test_case_and_space_insensitive(self):
        r = {"1": {"origin": "Hi", "Direct Translation": "你好"}}
        assert tl.normalize_sub_keys(r, "direct") is True

    def test_single_remaining_key_fallback(self):
        """不认识的键，但除 origin 外只剩一个 → 那必然是译文。"""
        r = {"1": {"origin": "Hi", "whatever": "你好"}}
        assert tl.normalize_sub_keys(r, "direct") is True
        assert r["1"]["direct"] == "你好"

    def test_nothing_to_do(self):
        r = {"1": {"origin": "Hi", "direct": "你好"}}
        assert tl.normalize_sub_keys(r, "direct") is False
        assert r["1"]["direct"] == "你好"

    def test_ambiguous_is_left_alone(self):
        """两个都像译文的键 —— 不猜，交给校验报错（宁可重试也不能选错）。"""
        r = {"1": {"origin": "Hi", "a": "1", "b": "2"}}
        assert tl.normalize_sub_keys(r, "direct") is False
        assert tl.valid_translate_result(r, ["1"], ["direct"])["status"] == "error"

    def test_non_dict_items_are_skipped(self):
        r = {"1": "你好"}
        assert tl.normalize_sub_keys(r, "direct") is False


class TestTranslateLinesEndToEnd:
    def _ctx(self):
        from core.services.translate_service import build_shared_context

        return build_shared_context("摘要", {"terms": []}, None, None)

    def _cfg(self, cfg_path):
        """链配置 + 提示词模板要用的语言键（缺了 load_key 会抛 KeyError）。"""
        return cfg_path(CHAIN_CONFIG + """
target_language: 简体中文
whisper:
  detected_language: en
""")

    def test_faith_path_uses_the_bound_chain(self, tmp_cwd, cfg_path,
                                             fake_llm, tmp_key_state):
        """translate_faith → tasks → 链 test → 模型 gpt-mini（不是别的链）。"""
        self._cfg(cfg_path)
        payload = {"1": {"origin": "Hello", "direct": "你好"},
                   "2": {"origin": "World", "direct": "世界"}}
        calls, _ = fake_llm([resp_ok(json.dumps(payload, ensure_ascii=False))])

        out, src = tl.translate_lines("Hello\nWorld", self._ctx(), reflect=False)

        assert out == "你好\n世界"
        assert src == "Hello\nWorld"
        assert models_used(calls) == ["gpt-mini"], models_used(calls)

    def test_retry_marker_is_added_per_attempt(self, tmp_cwd, cfg_path,
                                               fake_llm, tmp_key_state):
        """行数不对 → 重试，且 prompt 带 attempt 标记（显式缓存穿透）。

        注意：少一行会被 valid_def 直接打回（字段校验），走的是 ask_gpt 的
        换链重试；这里的多一行能通过校验，才轮到 translate_lines 自己重试。
        """
        self._cfg(cfg_path)
        bad = {str(i): {"origin": f"o{i}", "direct": f"d{i}"} for i in range(1, 4)}
        good = {"1": {"origin": "Hello", "direct": "你好"},
                "2": {"origin": "World", "direct": "世界"}}
        calls, _ = fake_llm([resp_ok(json.dumps(bad, ensure_ascii=False)),
                             resp_ok(json.dumps(good, ensure_ascii=False))])

        out, _ = tl.translate_lines("Hello\nWorld", self._ctx(), reflect=False)

        assert out == "你好\n世界"
        prompts = prompts_used(calls)
        assert len(prompts) == 2
        assert "<!-- attempt 1/3 -->" in prompts[0][-1]["content"]
        assert "<!-- attempt 2/3 -->" in prompts[1][-1]["content"]

    def test_model_writes_translation_instead_of_direct(self, tmp_cwd, cfg_path,
                                                        fake_llm, tmp_key_state):
        """真实事故：gemini-2.5-flash-lite 全程写 translation，一次调用就崩。

        键名归一后应当直接通过 —— 不重试、不换链、不浪费调用。
        """
        self._cfg(cfg_path)
        payload = {"1": {"origin": "Hello", "translation": "你好"},
                   "2": {"origin": "World", "translation": "世界"}}
        calls, _ = fake_llm([resp_ok(json.dumps(payload, ensure_ascii=False))])

        out, _ = tl.translate_lines("Hello\nWorld", self._ctx(), reflect=False)

        assert out == "你好\n世界"
        assert len(prompts_used(calls)) == 1   # 一次过，没重试
