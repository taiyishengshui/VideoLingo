"""链（llm.chains / llm.tasks）解析的单元测试 —— 本轮改动的核心机制。

覆盖 core/utils/llm_profiles.py：
- 命名链的三种格式（v2 dict / v1 bare-list / v0 任务内联列表）
- 任务 → 链 ID 的绑定解析，以及 default 回退
- 校验（引用不存在的模型 / 链 / 空链 / 非法 id）必须报错而不是静默
- get_profile_chain 真正"用这条链"解析出 profile 序列
- key 池解析与死 key 标记
"""

from __future__ import annotations

import pytest

from core.utils import llm_profiles as lp


# --------------------------------------------------------------- 链 ID 格式

@pytest.mark.parametrize("cid", [
    "a", "test", "fast-cheap", "quality-first", "a1", "x-9", "a" * 32,
    "abc-",   # 尾连字符合法（和 DNS 名一样），但开头必须是字母数字
])
def test_chain_id_ok_accepts(cid):
    assert lp.chain_id_ok(cid) is True


@pytest.mark.parametrize("cid", [
    "", "-abc", "Abc", "ABC", "a b", "a_b", "a.b", "质量优先",
    "a" * 33, "中文-id",
])
def test_chain_id_ok_rejects(cid):
    # id 会被任务绑定引用，必须是程序可安全解析的英文小写串
    assert lp.chain_id_ok(cid) is False


# ------------------------------------------------------ 链与任务的解析（纯函数）

MODELS = {
    "mini": {"name": "mini", "endpoint": "epa", "model": "m-mini",
             "llm_support_json": True, "thinking_type": None},
    "big": {"name": "big", "endpoint": "epb", "model": "m-big",
            "llm_support_json": False, "thinking_type": "disabled"},
}


def test_v2_named_chain_keeps_label_and_models():
    llm = {"chains": {"test": {"label": "测试", "models": ["mini", "big"]}},
           "tasks": {"default": "test"}}
    chains, tasks = lp._resolve_chains_and_tasks(llm, MODELS)
    assert chains["test"]["label"] == "测试"
    assert chains["test"]["models"] == ["mini", "big"]
    assert tasks["default"] == ["mini", "big"]


def test_label_falls_back_to_chain_id():
    llm = {"chains": {"test": {"models": ["mini"]}}, "tasks": {"default": "test"}}
    chains, _ = lp._resolve_chains_and_tasks(llm, MODELS)
    assert chains["test"]["label"] == "test"          # 没有 label 就用 id


def test_v1_bare_list_chain_still_works():
    """旧格式：链名 -> [模型]，label 用链名（可以是中文）。"""
    llm = {"chains": {"省钱快跑": ["mini"]}, "tasks": {"default": "省钱快跑"}}
    chains, tasks = lp._resolve_chains_and_tasks(llm, MODELS)
    assert chains["省钱快跑"]["label"] == "省钱快跑"
    assert tasks["default"] == ["mini"]


def test_string_models_value_is_wrapped():
    llm = {"chains": {"test": {"models": "mini"}}, "tasks": {"default": "test"}}
    chains, tasks = lp._resolve_chains_and_tasks(llm, MODELS)
    assert chains["test"]["models"] == ["mini"]
    assert tasks["default"] == ["mini"]


def test_task_binding_expands_to_chain_sequence_in_order():
    llm = {
        "chains": {
            "test": {"label": "测试", "models": ["mini"]},
            "quality-first": {"label": "质量优先", "models": ["big", "mini"]},
        },
        "tasks": {"default": "test", "translate_express": "quality-first"},
    }
    _, tasks = lp._resolve_chains_and_tasks(llm, MODELS)
    assert tasks["translate_express"] == ["big", "mini"]      # 顺序即回退顺序
    assert tasks["default"] == ["mini"]


def test_v0_inline_task_list_still_resolves():
    """最老的格式：任务直接内联模型列表（没有 chains 表）。"""
    llm = {"tasks": {"default": ["mini", "big"]}}
    chains, tasks = lp._resolve_chains_and_tasks(llm, MODELS)
    assert chains == {}
    assert tasks["default"] == ["mini", "big"]


def test_default_is_auto_added_when_missing():
    llm = {"chains": {"test": {"models": ["big"]}}, "tasks": {}}
    _, tasks = lp._resolve_chains_and_tasks(llm, MODELS)
    assert tasks["default"] == ["mini"]      # 取 models 表的第一个


def test_chain_referencing_unknown_model_raises():
    llm = {"chains": {"test": {"models": ["ghost"]}}, "tasks": {"default": "test"}}
    with pytest.raises(ValueError, match="unknown model 'ghost'"):
        lp._resolve_chains_and_tasks(llm, MODELS)


def test_task_referencing_unknown_chain_raises():
    llm = {"chains": {"test": {"models": ["mini"]}},
           "tasks": {"default": "no-such-chain"}}
    with pytest.raises(ValueError, match="unknown chain 'no-such-chain'"):
        lp._resolve_chains_and_tasks(llm, MODELS)


def test_empty_chain_models_raises():
    llm = {"chains": {"test": {"models": []}}, "tasks": {"default": "test"}}
    with pytest.raises(ValueError, match="non-empty models list"):
        lp._resolve_chains_and_tasks(llm, MODELS)


def test_invalid_task_value_raises():
    llm = {"chains": {"test": {"models": ["mini"]}}, "tasks": {"default": 123}}
    with pytest.raises(ValueError, match="must be a chain id or a non-empty list"):
        lp._resolve_chains_and_tasks(llm, MODELS)


def test_empty_task_list_raises():
    llm = {"chains": {"test": {"models": ["mini"]}}, "tasks": {"default": []}}
    with pytest.raises(ValueError):
        lp._resolve_chains_and_tasks(llm, MODELS)


# ------------------------------------------------------------- 全量配置解析

def test_load_llm_config_resolves_three_tables(chain_cfg):
    cfg = lp.load_llm_config()
    assert set(cfg["chains"]) == {"test", "quality-first", "light-verify"}
    assert cfg["chains"]["test"]["label"] == "测试"
    assert cfg["chains"]["quality-first"]["models"] == ["big", "mini"]
    # light-verify 没写 label -> 用 id
    assert cfg["chains"]["light-verify"]["label"] == "light-verify"
    assert cfg["tasks"]["default"] == ["mini"]
    assert cfg["retries_per_model"] == 2


def test_profile_shape_carries_endpoint_fields(chain_cfg):
    cfg = lp.load_llm_config()
    mini = cfg["profiles"]["mini"]
    assert mini["name"] == "mini"
    assert mini["api_type"] == "chat_completions"
    assert mini["endpoint"] == "epa"
    assert mini["model"] == "gpt-mini"
    assert mini["base_url"] == "https://api.a.com/v1"
    # key 取池中第一个；keys 保留整池供轮换
    assert mini["key"] == "sk-aaa111111111"
    assert mini["keys"] == ["sk-aaa111111111", "sk-aaa222222222"]
    assert mini["llm_support_json"] is True
    assert mini["thinking_type"] == "disabled"


def test_model_profile_accepts_responses_and_rejects_unknown_api_type(cfg_path):
    cfg_path("""
llm:
  endpoints:
    ep:
      base_url: 'https://api.example.com/v1'
      key: 'sk-test'
  models:
    m:
      endpoint: ep
      model: m1
      api_type: responses
  tasks:
    default: [m]
""")
    assert lp.load_llm_config()["profiles"]["m"]["api_type"] == "responses"

    cfg_path("""
llm:
  endpoints:
    ep:
      base_url: 'https://api.example.com/v1'
      key: 'sk-test'
  models:
    m:
      endpoint: ep
      model: m1
      api_type: invalid
  tasks:
    default: [m]
""")
    with pytest.raises(ValueError, match="api_type"):
        lp.load_llm_config()


def test_model_with_dangling_endpoint_is_dropped(cfg_path):
    cfg_path("""
llm:
  endpoints:
    epa:
      base_url: 'https://api.a.com/v1'
      key: 'sk-a'
  models:
    ghost:
      endpoint: 'nope'      # 引用了不存在的端点
      model: 'x'
    mini:
      endpoint: epa
      model: 'gpt-mini'
  chains:
    test:
      models: [mini]
  tasks:
    default: test
""")
    cfg = lp.load_llm_config()
    assert "ghost" not in cfg["models"]        # 悬挂引用被丢弃
    assert "mini" in cfg["models"]


def test_missing_llm_section_raises(cfg_path):
    cfg_path("translate:\n  chunk_size: 100\n")
    with pytest.raises(ValueError, match="no llm section"):
        lp.load_llm_config()


@pytest.mark.parametrize("bad", ["0", "-1", "'two'"])
def test_invalid_retries_per_model_raises(cfg_path, bad):
    cfg_path(f"""
llm:
  retries_per_model: {bad}
  endpoints:
    epa:
      base_url: 'https://api.a.com/v1'
      key: 'sk-a'
  models:
    mini:
      endpoint: epa
      model: 'gpt-mini'
  chains:
    test:
      models: [mini]
  tasks:
    default: test
""")
    with pytest.raises(ValueError, match="retries_per_model"):
        lp.load_llm_config()


# --------------------------------------------------------- get_profile_chain

def test_get_profile_chain_uses_the_bound_chain(chain_cfg):
    chain = lp.get_profile_chain("translate_express")     # -> quality-first
    assert [p["name"] for p in chain] == ["big", "mini"]
    assert chain[0]["base_url"] == "https://www.b.com/"   # 未归一化，由调用方处理


def test_get_profile_chain_default_chain_is_test(chain_cfg):
    """当前线上绑定：default -> test 链 -> [mini]。"""
    chain = lp.get_profile_chain("default")
    assert [p["name"] for p in chain] == ["mini"]


def test_get_profile_chain_falls_back_to_default(chain_cfg):
    """没绑定的任务（如 summary）走 default 链，而不是报错。"""
    chain = lp.get_profile_chain("summary")
    assert [p["name"] for p in chain] == ["mini"]


def test_get_profile_chain_none_is_default(chain_cfg):
    assert [p["name"] for p in lp.get_profile_chain()] == ["mini"]


def test_retries_and_default_prefix(chain_cfg):
    assert lp.retries_per_model() == 2
    assert lp.default_profile_config_prefix() == "llm.models.mini"


# ------------------------------------------------------------------ key 池

def test_parse_key_file_handles_all_line_shapes(tmp_path):
    f = tmp_path / "keys.txt"
    f.write_text(
        "# a comment\n"
        "\n"
        "label-a----sk-1111\n"
        "sk-2222\n"                    # 裸 key（没有分隔符）
        "   ----sk-3333\n"             # 有分隔符，key 在前
        "----\n"                       # 分隔符后为空 -> 丢弃
    , encoding="utf-8")
    assert lp.parse_key_file(str(f)) == ["sk-1111", "sk-2222", "sk-3333"]


def test_key_id_shortens_long_keys():
    assert lp._key_id("sk-aaa111111111") == "sk-aaa11..1111"
    assert lp._key_id("short") == "short"


def test_dead_key_is_marked_and_filtered(tmp_key_state):
    ep = {"_name": "epa", "keys": ["sk-aaa111111111", "sk-aaa222222222"]}
    assert lp.endpoint_keys(ep) == ["sk-aaa111111111", "sk-aaa222222222"]

    lp.mark_key_dead("epa", "sk-aaa111111111")

    state = lp.load_key_state()
    assert state["epa"]["sk-aaa11..1111"] == "dead"
    # 死 key 被过滤掉，剩下的继续可用
    assert lp.endpoint_keys(ep) == ["sk-aaa222222222"]


def test_dead_mark_is_permanent_and_resettable(tmp_key_state):
    lp.mark_key_dead("epa", "sk-aaa111111111")
    lp.mark_key_dead("epb", "sk-bbb111111111")
    assert lp._is_key_dead("epa", "sk-aaa111111111") is True
    assert lp._is_key_dead("epb", "sk-bbb111111111") is True

    assert lp.reset_endpoint_keys("epa") == 1
    assert lp._is_key_dead("epa", "sk-aaa111111111") is False
    assert lp._is_key_dead("epb", "sk-bbb111111111") is True     # 只清一个端点

    assert lp.reset_endpoint_keys(None) == 1                      # 全清
    assert lp.load_key_state() == {}


def test_single_key_endpoint_falls_back_to_key_field():
    assert lp.endpoint_keys({"_name": "e", "key": "sk-only"}) == ["sk-only"]


# --------------------------------------------------------------- base_url

@pytest.mark.parametrize("raw,expected", [
    ("https://www.yhshu.ai/", "https://www.yhshu.ai/v1"),
    ("https://api.a.com/v1", "https://api.a.com/v1"),
    ("https://ark.cn-beijing.volces.com/api/v3",
     "https://ark.cn-beijing.volces.com/api/v3"),
    ("https://my-ark-gateway.com", "https://ark.cn-beijing.volces.com/api/v3"),
])
def test_normalize_base_url(raw, expected):
    assert lp.normalize_base_url(raw) == expected
