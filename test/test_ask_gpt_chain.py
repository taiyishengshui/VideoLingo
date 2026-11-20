"""ask_gpt 走链行为的单元测试 —— 网络层被假客户端替换，零 API 成本。

这里验证的是"这条链"在真实失败场景下的决策：
- 失败先在本 profile 内按 retries_per_model 重试，用尽才换下一个模型
- 401/402/403 属于 key 被拒：同一端点还有下一个 key 就先轮换（不换模型、
  不改风格），池子耗尽才走到链的下一个 profile
- 死 key 被永久标记（output/key_state.json）
- 缓存命中不再发请求
"""

from __future__ import annotations

import json

import pytest

from conftest import keys_used, models_used, resp_ok, status_error

# 注意：这里要拿的是 ask_gpt **模块**（要 patch 它内部的 time / 缓存函数）。
# 两种写法都会踩坑，因为 core/utils/__init__.py 里有 `from .ask_gpt import
# ask_gpt`，把包的同名属性覆盖成了函数：
#   from core.utils import ask_gpt      -> 函数
#   import core.utils.ask_gpt as ag     -> 依然是函数（getattr 先命中，
#                                          sys.modules 兜底不生效）
# 所以只能显式按模块名取。
import importlib

ag = importlib.import_module("core.utils.ask_gpt")

# 两个端点各一个 key，两个模型组成一条链 —— 便于精确编排失败脚本
TWO_KEY_CONFIG = """
llm:
  retries_per_model: 2
  endpoints:
    epa:
      base_url: 'https://api.a.com/v1'
      keys: ['sk-aaa111111111', 'sk-aaa222222222']
    epb:
      base_url: 'https://api.b.com/v1'
      key: 'sk-bbb111111111'
  models:
    mini:
      endpoint: epa
      model: 'gpt-mini'
      llm_support_json: true
    big:
      endpoint: epb
      model: 'gpt-big'
  chains:
    test:
      label: 测试
      models: [mini]
    quality-first:
      models: [big, mini]
  tasks:
    default: test
    translate_faith: test          # -> 链 test      [mini]
    translate_express: quality-first  # -> 链 quality-first [big, mini]
"""

NO_KEY_CONFIG = """
llm:
  endpoints:
    epa:
      base_url: 'https://api.a.com/v1'
  models:
    mini:
      endpoint: epa
      model: 'gpt-mini'
  chains:
    test:
      models: [mini]
  tasks:
    default: test
"""


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """瞬时失败的重试退避不要真的睡。"""
    monkeypatch.setattr(ag.time, "sleep", lambda *_a, **_kw: None)


@pytest.fixture
def two_key_cfg(cfg_path):
    return cfg_path(TWO_KEY_CONFIG)


# 调用日志的解读统一在 conftest（唯一实现），别在这里再解一次包


# ------------------------------------------------------------------ 正常路径

def test_first_profile_succeeds_without_walking(chain_cfg, tmp_cwd, fake_llm):
    calls, script = fake_llm([resp_ok("hello")])
    assert ag.ask_gpt("prompt-1", log_title="translate_faith") == "hello"
    assert len(script) == 0                      # 脚本刚好用完
    assert models_used(calls) == ["gpt-mini"]   # 链 test = [mini]


def test_responses_profile_dispatches_and_extracts_json(chain_cfg, tmp_cwd, fake_llm):
    from conftest import prompts_used

    from core.utils import config_utils
    from ruamel.yaml import YAML

    config_path = config_utils.CONFIG_PATH
    yaml = YAML()
    with open(config_path, "r", encoding="utf-8") as stream:
        config = yaml.load(stream)
    config["llm"]["models"]["mini"]["api_type"] = "responses"
    with open(config_path, "w", encoding="utf-8") as stream:
        yaml.dump(config, stream)

    response = {
        "output": [{"content": [{"type": "output_text", "text": '{"answer":"ok"}'}]}]
    }
    calls, script = fake_llm([response])
    assert ag.ask_gpt("prompt-responses", resp_type="json",
                      log_title="translate_faith") == {"answer": "ok"}
    assert models_used(calls) == ["gpt-mini"]
    request = prompts_used(calls)[0]
    assert request[0]["content"] == "prompt-responses"
    assert request[0]["role"] == "user"
    params = next(call[2] for call in calls if call[0] == "gpt-mini")
    assert params["max_output_tokens"] == 4096
    assert params["text"] == {"format": {"type": "json_object"}}
    assert "messages" not in params and "max_tokens" not in params


# -------------------------------------------------------------- 瞬时失败重试

def test_transient_failure_exhausts_retries_then_walks_chain(
        two_key_cfg, tmp_cwd, fake_llm, tmp_key_state):
    """big 连挂 2 次（retries_per_model=2）→ 走到链的下一个 profile mini。"""
    calls, script = fake_llm([
        status_error(500), status_error(500),       # big：用满预算
        resp_ok("from-mini"),                       # mini：成功
    ])
    # translate_express -> 链 quality-first -> [big, mini]
    out = ag.ask_gpt("p", log_title="translate_express")
    assert out == "from-mini"

    # 换模型前 big 被尝试了 2 次，之后才是 mini
    assert models_used(calls) == ["gpt-big", "gpt-big", "gpt-mini"]
    # 500 不是"key 被拒"，不该标记死 key
    assert tmp_key_state.exists() is False


# --------------------------------------------------------------- 死 key 轮换

def test_401_rotates_to_next_key_of_same_endpoint(
        two_key_cfg, tmp_cwd, fake_llm, tmp_key_state):
    """401 = key 被拒：先轮换同一端点的下一个 key，模型不变。"""
    calls, script = fake_llm([
        status_error(401),        # key1 被拒
        resp_ok("ok-with-key2"),  # key2 成功
    ])
    # default -> test 链 -> [mini]（mini 在 epa，池里两个 key）
    assert ag.ask_gpt("p", log_title="translate_faith") == "ok-with-key2"

    assert keys_used(calls) == ["sk-aaa111111111", "sk-aaa222222222"]
    assert models_used(calls) == ["gpt-mini", "gpt-mini"]

    state = json.loads(tmp_key_state.read_text(encoding="utf-8"))
    assert state["epa"]["sk-aaa11..1111"] == "dead"
    # key2 没有被动过
    assert "sk-aaa22..2222" not in state.get("epa", {})


def test_401_with_exhausted_pool_moves_to_next_model(
        two_key_cfg, tmp_cwd, fake_llm, tmp_key_state):
    """单 key 端点 401 → 池子空了 → 直接走链的下一个 profile。"""
    calls, script = fake_llm([
        status_error(401),      # big（epb 单 key）被拒
        resp_ok("from-mini"),   # mini 接上
    ])
    # quality-first = [big(epb), mini(epa)] —— big 只有一个 key
    assert ag.ask_gpt("p", log_title="translate_express") == "from-mini"
    assert models_used(calls) == ["gpt-big", "gpt-mini"]

    state = json.loads(tmp_key_state.read_text(encoding="utf-8"))
    assert state["epb"]["sk-bbb11..1111"] == "dead"


def test_whole_chain_failing_raises_last_exception(
        two_key_cfg, tmp_cwd, fake_llm, tmp_key_state):
    """整条链都挂 → 抛出最后一个异常（不是 None，也不是"成功"）。"""
    calls, script = fake_llm([status_error(500)] * 4)   # big×2 + mini×2
    with pytest.raises(Exception) as exc:
        ag.ask_gpt("p", log_title="translate_express")
    assert "Error code: 500" in str(exc.value)
    assert len(script) == 0                             # 4 次都用掉了
    assert models_used(calls) == ["gpt-big", "gpt-big", "gpt-mini", "gpt-mini"]


# -------------------------------------------------------------------- 缓存

def test_cache_hit_skips_the_network(chain_cfg, tmp_cwd, fake_llm):
    # 预置一条缓存（key 是 prompt + resp_type + log_title）
    ag._save_cache("m", "prompt-x", "RAW", None, {"cached": True},
                   log_title="translate_faith")
    calls, script = fake_llm([])          # 空脚本：一旦真的发请求就会炸
    assert ag.ask_gpt("prompt-x", log_title="translate_faith") == {"cached": True}
    assert calls == []


def test_cache_is_keyed_by_prompt_not_model(chain_cfg, tmp_cwd, fake_llm):
    """历史行为（有意为之）：缓存按 prompt 命中，与模型无关 —— 换模型重跑
    必须先删 gpt_log/<title>.json，否则拿到的是旧答案。"""
    ag._save_cache("other-model", "prompt-x", "RAW", None, {"from": "old"},
                   log_title="translate_faith")
    calls, script = fake_llm([])
    assert ag.ask_gpt("prompt-x", log_title="translate_faith") == {"from": "old"}


def test_bypass_cache_ignores_existing_cache(chain_cfg, tmp_cwd, fake_llm):
    ag._save_cache("m", "prompt-x", "RAW", None, {"cached": True},
                   log_title="translate_faith")
    calls, script = fake_llm([resp_ok("fresh")])
    assert ag.ask_gpt("prompt-x", log_title="translate_faith",
                      bypass_cache=True) == "fresh"


# ------------------------------------------------------------ 响应解析/校验

def test_json_response_is_repaired_and_unwrapped(chain_cfg, tmp_cwd, fake_llm):
    calls, script = fake_llm([resp_ok('```json\n{"a": 1,}\n```')])
    out = ag.ask_gpt("p", resp_type="json", log_title="translate_faith")
    assert out == {"a": 1}


def test_json_list_response_is_keyed_by_position(chain_cfg, tmp_cwd, fake_llm):
    calls, script = fake_llm([resp_ok('["x", "y"]')])
    out = ag.ask_gpt("p", resp_type="json", log_title="translate_faith")
    assert out == {"1": "x", "2": "y"}


def test_validation_failure_is_content_error_then_next_model(
        two_key_cfg, tmp_cwd, fake_llm, tmp_key_state):
    """valid_def 不通过 = CONTENT：便宜地再试一次，仍不合格就换模型。

    校验失败说明"这个模型干不了这活"，不值得花退避时间，所以不 sleep。
    """
    def _ok_when_good(resp):
        return ({"status": "success", "message": "ok"} if resp == "good"
                else {"status": "error", "message": "not good"})

    calls, script = fake_llm([
        resp_ok("bad"), resp_ok("bad"),     # big 两次都被校验打回
        resp_ok("good"),                    # mini 成功
    ])
    out = ag.ask_gpt("p", valid_def=_ok_when_good, log_title="translate_express")
    assert out == "good"
    assert models_used(calls) == ["gpt-big", "gpt-big", "gpt-mini"]


def test_missing_api_key_raises_early(cfg_path, tmp_cwd, fake_llm):
    cfg_path(NO_KEY_CONFIG)
    calls, script = fake_llm([])              # 不该发任何请求
    with pytest.raises(ValueError, match="API key is not set"):
        ag.ask_gpt("p", log_title="default")
    assert calls == []


# ------------------------------------------------------------- 429 限流（v1.13）

RETRY_ONCE_CONFIG = TWO_KEY_CONFIG.replace(
    "retries_per_model: 2", "retries_per_model: 1")

NEW_API_429 = ("You have reached the request limit: maximum 10 requests "
               "in 1 minutes (request id: 20261111100853...)")


def _recording_sleep(monkeypatch):
    """记录 time.sleep 的每次时长（autouse 的 _no_sleep 已把它换成 no-op，
    测试内再换成记录版即可验证限流等待）。"""
    sleeps = []

    def _sleep(secs):
        sleeps.append(secs)

    monkeypatch.setattr(ag.time, "sleep", _sleep)
    return sleeps


def test_429_wait_does_not_burn_retry_budget(
        cfg_path, tmp_cwd, fake_llm, tmp_key_state, monkeypatch):
    """retries_per_model=1 时普通失败重试一次就换模型；429 等待重试
    必须不消耗这个预算 —— 否则限流一次就把唯一尝试机会烧掉了。"""
    cfg_path(RETRY_ONCE_CONFIG)
    sleeps = _recording_sleep(monkeypatch)
    calls, script = fake_llm([
        status_error(429, message=NEW_API_429),   # 限流 → 等窗口
        resp_ok("after-wait"),                    # 同一模型重试成功
    ])
    assert ag.ask_gpt("p", log_title="translate_faith") == "after-wait"
    # 同一模型重试（没换模型），等待时长 = 1 分钟窗口 + 1s pad
    assert models_used(calls) == ["gpt-mini", "gpt-mini"]
    assert sleeps == [pytest.approx(61.0)]
    # 限流不是 key 被拒，不该标死
    assert tmp_key_state.exists() is False


def test_429_waits_bounded_then_moves_to_next_profile(
        cfg_path, tmp_cwd, fake_llm, tmp_key_state, monkeypatch):
    """每模型最多 3 次限流等待：第 4 次 429 直接切链上下一模型。"""
    cfg_path(TWO_KEY_CONFIG)   # quality-first = [big, mini]，retries=2
    sleeps = _recording_sleep(monkeypatch)
    calls, script = fake_llm([
        status_error(429, message=NEW_API_429),   # big：等 1
        status_error(429, message=NEW_API_429),   # big：等 2
        status_error(429, message=NEW_API_429),   # big：等 3
        status_error(429, message=NEW_API_429),   # big：等待预算用尽 → 切 mini
        resp_ok("from-mini"),                     # mini 成功
    ])
    out = ag.ask_gpt("p", log_title="translate_express")
    assert out == "from-mini"
    assert models_used(calls) == ["gpt-big"] * 4 + ["gpt-mini"]
    assert sleeps == [pytest.approx(61.0)] * 3    # 第 4 次不再等


def test_429_without_parseable_info_uses_default_wait(
        cfg_path, tmp_cwd, fake_llm, tmp_key_state, monkeypatch):
    """裸 429（无窗口信息、无 Retry-After）→ 兜底 30s。"""
    cfg_path(RETRY_ONCE_CONFIG)
    sleeps = _recording_sleep(monkeypatch)
    calls, script = fake_llm([
        status_error(429),                         # 无 message、无头
        resp_ok("ok"),
    ])
    assert ag.ask_gpt("p", log_title="translate_faith") == "ok"
    assert sleeps == [pytest.approx(30.0)]


# ------------------------------------------- rate_limit_wait 解析（纯函数）

def test_rate_limit_wait_parsing():
    from core.services.llm_service import rate_limit_wait
    from core.services import llm_service as ls

    # new_api / one-api 风格：N 请求 / M 窗口 → 等整个窗口 + 1s
    assert rate_limit_wait(status_error(429, message=NEW_API_429)) == \
        pytest.approx(61.0)
    assert rate_limit_wait(status_error(
        429, message="maximum 20 requests in 30 seconds")) == \
        pytest.approx(31.0)
    # 多分钟窗口触顶 120s
    assert rate_limit_wait(status_error(
        429, message="maximum 100 requests in 10 minutes")) == \
        pytest.approx(120.0)
    # "try again in Xs"
    assert rate_limit_wait(status_error(
        429, message="Rate limit exceeded, try again in 12.5 seconds")) == \
        pytest.approx(13.0)
    # Retry-After 头（秒）
    assert rate_limit_wait(status_error(
        429, headers={"retry-after": "45"})) == pytest.approx(45.5)
    # 裸 429 → 兜底
    assert rate_limit_wait(status_error(429)) == \
        pytest.approx(ls.DEFAULT_RATE_LIMIT_WAIT)


def test_classify_error_distinguishes_429():
    from core.services.llm_service import classify_error, RATE_LIMIT, TRANSIENT
    assert classify_error(status_error(429)) == RATE_LIMIT
    assert classify_error(status_error(500)) == TRANSIENT


def test_response_validation_handles_empty_and_error_shapes():
    from core.services.llm_service import check_response

    assert check_response(None, api_type="responses")[0] is None
    content, error = check_response(
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
        api_type="responses")
    assert content is None
    assert "incomplete" in error or "max_output_tokens" in error

    content, error = check_response({"error": {"message": "bad request"}},
                                    api_type="responses")
    assert content is None
    assert "bad request" in error

    assert check_response(None)[0] is None
    content, error = check_response({"error": {"message": "bad request"}})
    assert content is None
    assert "bad request" in error


def test_responses_probe_dispatches_and_checks_output(monkeypatch):
    from core.services import llm_service
    from conftest import FakeClient
    from core.utils import net_policy

    calls = []
    client = FakeClient([{"output_text": " ok "}], calls)
    monkeypatch.setattr(net_policy, "get_policy",
                        lambda: ("direct", None, None))
    monkeypatch.setattr(net_policy, "make_openai_client",
                        lambda *args, **kwargs: client)

    ok, result, status = llm_service.probe({
        "base_url": "https://api.example.com/v1",
        "model": "gpt-responses",
        "api_type": "responses",
        "thinking_type": "enabled",
    }, "test-key")

    assert ok is True
    assert result["reply"] == "ok"
    assert status is None
    params = next(call[2] for call in calls if call[0] == "gpt-responses")
    assert params["input"] == [{"role": "user", "content": "Reply with the single word: ok"}]
    assert params["max_output_tokens"] == 32
    assert params["reasoning"] == {"effort": "none"}
    assert "messages" not in params and "max_tokens" not in params
