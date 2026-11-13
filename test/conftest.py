"""共享夹具：把项目根加入 sys.path，提供隔离的 config / cwd / 假 LLM 客户端。

隔离原则（重要）：
- 绝不允许测试去读写真实的 config.yaml、output/、terms/。
- `core.utils.config_utils.CONFIG_PATH` 是模块级常量，用 monkeypatch 改它
  即可让 load_key() 每次调用都读到临时配置（load_key 在函数体内读全局）。
- 项目里大量路径是相对当前工作目录的（output/log/...、output/key_state.json），
  所以写文件的测试一律先 `monkeypatch.chdir(tmp_path)`。
- 绝对路径常量（translate_service.TERMS_DIR）用 monkeypatch 指到临时目录。
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

# 项目根：test/ 的上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# editable 安装时 core 已经可 import；这里兜底，保证 `pytest test/` 从任意
# 目录都能跑。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolated_log_dir(tmp_path, monkeypatch):
    """FR-31：create_app() 的 startup 会注册全局日志 sink。

    不重定向的话，测试里任何 rprint / run_logged 都会 append 到**真实的**
    `output/log/pipeline_latest.log`，把运行日志写成一堆测试输出（违反
    本文件开头「绝不允许测试读写真实 output/」的隔离原则）。
    个别落盘用例会再 patch 一次到自己的子目录，后设置的优先。
    """
    try:
        import server.logbus as logbus
    except Exception:                                              # noqa: BLE001
        return
    monkeypatch.setattr(logbus, "LOG_DIR", str(tmp_path / "log"))


# ---------------------------------------------------------------- 测试配置

# 结构与真实 config.yaml 的 llm 三表一致（当前线上：4 条链 + test 链绑定）
# 也刻意保留了一条"旧格式"的链，用于兼容路径测试。
CHAIN_CONFIG = """
llm:
  retries_per_model: 2
  endpoints:
    epa:
      base_url: 'https://api.a.com/v1'
      keys:
        - 'sk-aaa111111111'
        - 'sk-aaa222222222'
    epb:
      base_url: 'https://www.b.com/'
      key: 'sk-bbb111111111'
  models:
    mini:
      endpoint: epa
      model: 'gpt-mini'
      llm_support_json: true
      thinking_type: 'disabled'
    big:
      endpoint: epb
      model: 'gpt-big'
  chains:
    test:
      label: 测试
      models: [mini]
    quality-first:
      label: 质量优先
      models: [big, mini]
    light-verify:
      models: [mini, big]
  tasks:
    default: test
    translate_faith: test
    translate_express: quality-first
    asr_sentence_fix: light-verify
"""

MINIMAL_CONFIG = """
llm:
  endpoints:
    epa:
      base_url: 'https://api.a.com/v1'
      key: 'sk-aaa111111111'
  models:
    mini:
      endpoint: epa
      model: 'gpt-mini'
      llm_support_json: true
  chains:
    test:
      label: 测试
      models: [mini]
  tasks:
    default: test
"""

TRANSLATE_CONFIG = """
llm:
  endpoints:
    epa:
      base_url: 'https://api.a.com/v1'
      key: 'sk-aaa111111111'
  models:
    mini:
      endpoint: epa
      model: 'gpt-mini'
      llm_support_json: true
  chains:
    test:
      label: 测试
      models: [mini]
  tasks:
    default: test
translate:
  chunk_size: 100
  max_sentences: 3
terminology:
  active: [default]
"""


def write_yaml(path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.lstrip("\n"), encoding="utf-8")
    return path


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    """写入一份临时 config.yaml，并把 config_utils.CONFIG_PATH 指向它。

    返回一个工厂：cfg_path(yaml_text) -> Path（可重复调用切换配置）。"""
    from core.utils import config_utils

    def _set(text: str = CHAIN_CONFIG) -> Path:
        p = write_yaml(tmp_path / "config.yaml", text)
        monkeypatch.setattr(config_utils, "CONFIG_PATH", str(p))
        return p

    return _set


@pytest.fixture
def chain_cfg(cfg_path):
    """默认链配置（含 test / quality-first / light-verify 三条链）。"""
    return cfg_path(CHAIN_CONFIG)


@pytest.fixture
def tmp_cwd(tmp_path, monkeypatch):
    """切到临时目录：所有相对路径的写操作都落在这里。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def tmp_key_state(tmp_path, monkeypatch):
    """把死 key 标记文件 output/key_state.json 重定向到临时目录。"""
    from core.utils import llm_profiles

    target = tmp_path / "key_state.json"
    monkeypatch.setattr(llm_profiles, "KEY_STATE_FILE", str(target))
    return target


# ------------------------------------------------------------ 假 LLM 客户端

class FakeCompletions:
    """按脚本逐次返回/抛出的 chat.completions。

    `script` 必须与调用方持有的是同一个 list 对象（不能 copy）：ask_gpt 每次
    请求都会重新构造一个 client，如果这里复制一份，每次请求拿到的都是"脚本
    第一条"，多步编排（先失败后成功）永远走不到第二步。
    """

    def __init__(self, script, log):
        self.script = script    # 共享的可变脚本：pop 会推进全局进度
        self.log = log          # 记录每次调用的 (key, model)

    def create(self, **params):
        self.log.append((params.get("model"), params.get("messages")))
        if not self.script:
            raise AssertionError("FakeCompletions: 脚本已用尽（调用次数超出预期）")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClient:
    """net_policy.make_openai_client 的替身。shape 与 openai SDK 一致。"""

    def __init__(self, script, log):
        self.chat = types.SimpleNamespace(completions=FakeCompletions(script, log))


@pytest.fixture
def fake_llm(monkeypatch):
    """拦截网络层：把 net_policy 的客户端工厂换成假客户端。

    用法：
        calls, script = fake_llm([resp_ok("hi")])
    `script` 是可变的，测试中途可追加；`calls` 是调用日志 list[(model, messages)]。
    """
    from core.utils import net_policy

    calls: list = []

    def _install(script):
        def _make_client(api_key, base_url, mode, timeout=300, **kw):
            # 把 key 也塞进日志，用于验证 key 轮换
            calls.append(("__key__", api_key))
            return FakeClient(script, calls)

        monkeypatch.setattr(net_policy, "make_openai_client", _make_client)
        monkeypatch.setattr(net_policy, "ordered_modes", lambda base_url: ["direct"])
        monkeypatch.setattr(net_policy, "remember_mode", lambda base_url, mode: None)
        return calls, script

    return _install


# ------------------------------------------------------------ 调用日志解读
#
# `calls` 里混着两种记录，别在各测试文件里各写一份解包：
#   ("__key__", api_key)      每次构造客户端时记一条（用于断言 key 轮换）
#   (model, messages)         每次真实请求记一条（用于断言用了哪个模型）
# 曾经有人把 (model, messages) 解成 (tag, model)，断言直接变成比对 messages。

def keys_used(calls):
    """按顺序返回每次客户端构造用的 api_key。"""
    return [c[1] for c in calls if c[0] == "__key__"]


def models_used(calls):
    """按顺序返回每次请求用的模型名。"""
    return [c[0] for c in calls if c[0] != "__key__"]


def prompts_used(calls):
    """按顺序返回每次请求的 messages（list[dict]）。"""
    return [c[1] for c in calls if c[0] != "__key__"]


def resp_ok(content: str):
    """一个合法的 chat.completions 响应（dict 形态，check_response 支持）。"""
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def status_error(status_code: int, message: str = "", headers: dict = None):
    """构造一个真实的 APIStatusError，让 classify_error 的 isinstance 生效。

    message 会拼进 str(e)（rate_limit_wait 解析限流窗口用）；
    headers 会挂到 httpx 响应上（Retry-After 头用）。
    """
    from openai import APIStatusError

    try:
        import httpx

        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        resp = httpx.Response(status_code, request=request,
                              headers=headers or {})
        text = f"Error code: {status_code}"
        if message:
            text += f" - {message}"
        return APIStatusError(text, response=resp, body=None)
    except Exception:                                    # noqa: BLE001
        # 兜底：绕过 __init__ 直接造实例，isinstance 依然成立
        err = APIStatusError.__new__(APIStatusError)
        err.status_code = status_code
        if message:
            err.args = (f"Error code: {status_code} - {message}",)
        return err
