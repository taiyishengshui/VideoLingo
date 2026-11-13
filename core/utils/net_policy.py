"""Unified network proxy policy for all outbound LLM/API calls.

Single source of truth for "how do we connect to this base_url":
  - settings_api (UI test button)      -> direct/proxy clients for probing
  - models_api   (fetch-models)        -> urllib opener honoring the policy
  - core/utils/ask_gpt (pipeline hot path) -> mode selection + per-host memory

Config (config.yaml):
  network:
    proxy_mode: auto | direct | proxy
    proxy_url:  e.g. http://127.0.0.1:7890 or socks5://127.0.0.1:10808
    no_proxy:   comma-separated hosts that bypass the proxy

Modes:
  auto   try direct first; on connection failure retry via proxy; remember
         the winning mode per base_url (thread-safe). The remembered mode is
         tried first next time but still falls back if it breaks later.
  direct never use any proxy (DefaultHttpxClient(trust_env=False) / empty
         ProxyHandler) — system/env SOCKS proxies are bypassed entirely.
  proxy  always route through network.proxy_url (env proxies are ignored;
         a missing proxy_url in this mode is a config error -> fallback auto).
"""

import os
import threading

from core.utils.config_utils import load_key

_lock = threading.Lock()
_mode_memory = {}  # base_url -> "direct" | "proxy"


def _cfg(key, default):
    try:
        val = load_key(f"network.{key}")
        return val if val not in (None, "") else default
    except KeyError:
        return default


def get_policy():
    """(mode, proxy_url, no_proxy) from config, with sane defaults."""
    mode = str(_cfg("proxy_mode", "auto")).strip().lower()
    if mode not in ("auto", "direct", "proxy"):
        mode = "auto"
    proxy_url = str(_cfg("proxy_url", "")).strip()
    no_proxy = str(_cfg("no_proxy", "localhost,127.0.0.1")).strip()
    if mode == "proxy" and not proxy_url:
        # forced proxy but no URL configured — degrade to auto, never crash
        mode = "auto"
    return mode, proxy_url, no_proxy


def remember_mode(base_url, mode):
    with _lock:
        _mode_memory[base_url] = mode


def get_remembered_mode(base_url):
    with _lock:
        return _mode_memory.get(base_url)


def ordered_modes(base_url):
    """Attempt order for this base_url under the current policy.

    direct policy -> ["direct"]; proxy policy -> ["proxy"];
    auto -> remembered mode first, the other one as fallback.
    """
    mode, _, _ = get_policy()
    if mode == "direct":
        return ["direct"]
    if mode == "proxy":
        return ["proxy"]
    remembered = get_remembered_mode(base_url)
    if remembered == "proxy":
        return ["proxy", "direct"]
    # default & remembered=="direct": direct first, proxy as fallback
    return ["direct", "proxy"]


# ---------------------------------------------------------------------------
# OpenAI SDK clients
# ---------------------------------------------------------------------------

def make_openai_clients(api_key, base_url, timeout=300):
    """(client, mode) factory for OpenAI SDK honoring the policy.

    Under auto this returns a DIRECT client (first mode); the caller is
    expected to walk `ordered_modes(base_url)` on APIConnectionError and
    call `make_openai_client(api_key, base_url, mode, timeout)` for each.
    For single-shot convenience use `openai_client_for(base_url, ...)`.
    """
    mode, _, _ = get_policy()
    client = make_openai_client(api_key, base_url, mode, timeout)
    return client, mode


def _httpx_kwargs(timeout):
    """Shared kwargs for the OpenAI SDK clients.

    Only when `network.ca_bundle` is configured do we override httpx's own
    certifi default with the merged context (G-17) — otherwise leave the SDK
    untouched so existing connections keep their stricter behaviour.
    """
    kw = {"timeout": timeout}
    if str(_cfg("ca_bundle", "")).strip():
        kw["verify"] = ssl_context()
    return kw


def make_openai_client(api_key, base_url, mode, timeout=300):
    from openai import OpenAI, DefaultHttpxClient
    kw = _httpx_kwargs(timeout)
    if mode == "direct":
        return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                      http_client=DefaultHttpxClient(trust_env=False, **kw))
    # proxy mode: route through network.proxy_url explicitly (ignore env)
    _, proxy_url, _ = get_policy()
    if proxy_url:
        import httpx
        mounts = {"all://": httpx.Proxy(proxy_url)}
        http_client = DefaultHttpxClient(mounts=mounts, **kw)
    else:
        # no explicit URL -> trust env proxies (the historical default)
        http_client = DefaultHttpxClient(**kw)
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout,
                  http_client=http_client)


def openai_client_for(api_key, base_url, timeout=300):
    """Convenience: client for the first mode under the current policy.
    Use when the caller does not implement fallback itself."""
    client, mode = make_openai_clients(api_key, base_url, timeout)
    return client


# ---------------------------------------------------------------------------
# urllib openers (fetch-models)
# ---------------------------------------------------------------------------

def ssl_context():
    """HTTPS context for urllib openers — single source of CA trust.

    Windows' system store frequently lacks intermediate CAs, so a plain
    `create_default_context()` fails with CERTIFICATE_VERIFY_FAILED on sites
    that browsers accept.  Append certifi's Mozilla bundle (still keeping the
    system store, so locally installed corporate/proxy roots keep working).

    `network.ca_bundle` is an optional extra PEM for MITM proxies that replace
    certificates; a bad path fails loudly instead of silently trusting nobody.
    Certificate verification is NEVER disabled — /v1/models carries the API
    key in its Authorization header.
    """
    import ssl
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError, ssl.SSLError):
        pass  # certifi missing/broken -> system store still usable
    bundle = str(_cfg("ca_bundle", "")).strip()
    if bundle:
        try:
            ctx.load_verify_locations(cafile=bundle)
        except (OSError, ssl.SSLError, ValueError) as e:
            raise ValueError(f"network.ca_bundle 无法加载 CA 证书 {bundle}: {e}") from e
    return ctx


_SSL_MARKERS = ("CERTIFICATE_VERIFY_FAILED", "SSLCertVerificationError",
                "unable to get local issuer certificate", "ca_bundle")

# 拉模型列表失败时，"切代理开关" 只在真正的网络故障下才有意义。中转站常见
# 的是 401（key 坏/无权限）和 404（压根没实现 /v1/models），必须给对的出路。
_HTTP_HINTS = (
    ("HTTP Error 401", "API Key 无效或没有 /v1/models 权限：检查平台凭证里的 key（可用「测试连通性」验证、重置坏 key），或直接手填模型名"),
    ("HTTP Error 403", "API Key 没有 /v1/models 权限：换一把 key，或直接手填模型名"),
    ("HTTP Error 404", "该平台未提供 /v1/models 列表接口：请直接手填模型名（不影响正常调用）"),
    ("HTTP Error 405", "该平台未提供 /v1/models 列表接口：请直接手填模型名（不影响正常调用）"),
    ("HTTP Error 429", "平台限流：稍后重试，或直接手填模型名"),
)
_SERVER_ERRORS = tuple(f"HTTP Error {code}" for code in range(500, 600))


def network_error_hint(message):
    """Actionable hint for a failed outbound request — single source of wording.

    TLS failures are NOT proxy problems: toggling the proxy switch cannot fix a
    missing CA.  Say so explicitly instead of sending the user in circles.
    """
    msg = str(message)
    if any(marker in msg for marker in _SSL_MARKERS):
        return ("证书校验失败，切换代理开关无效：可先手填模型名；若本地代理会替换证书，"
                "把它的根证书 PEM 路径写进 config.yaml 的 network.ca_bundle")
    for marker, hint in _HTTP_HINTS:
        if marker in msg:
            return hint
    if any(marker in msg for marker in _SERVER_ERRORS):
        return "平台服务端错误：稍后重试，或直接手填模型名"
    return "可切换代理开关重试，或手填模型名"


def _opener(proxy_handler=None):
    """build_opener with our merged CA context, plus an optional ProxyHandler.

    Not passing a ProxyHandler at all makes urllib read env proxies — that is
    deliberately used for the fallback leg only.
    """
    import urllib.request
    handlers = []
    if proxy_handler is not None:
        handlers.append(proxy_handler)
    handlers.append(urllib.request.HTTPSHandler(context=ssl_context()))
    return urllib.request.build_opener(*handlers).open


def urllib_opener(use_proxy=None):
    """A urlopen-compatible callable honoring the policy.

    use_proxy=True/False (from a UI toggle) temporarily overrides the policy;
    None means "follow config".  auto still means "direct first" — the proxy
    leg is supplied by `urllib_fallback_opener()`.
    """
    import urllib.request
    mode, proxy_url, no_proxy = get_policy()
    if use_proxy is not None:
        mode = "proxy" if use_proxy else "direct"

    if mode == "proxy" and not proxy_url:
        # forced proxy but no URL configured: trust env proxies (legacy behavior)
        return _opener()
    if mode == "proxy":
        proxies = {"http": proxy_url, "https": proxy_url}
        if no_proxy:
            os.environ["NO_PROXY"] = no_proxy
            os.environ["no_proxy"] = no_proxy
        return _opener(urllib.request.ProxyHandler(proxies))
    # direct + auto: explicit empty proxy handler really bypasses env proxies
    return _opener(urllib.request.ProxyHandler({}))


def urllib_fallback_opener():
    """The other side of auto mode: env proxies, with the same merged CA context."""
    return _opener()
