"""LLM service — the ONLY home for request-failure decisions.

Single source of truth for:
- classify(exc):        what KIND of failure is this?  (dead/content/transient/
                        rate_limit)
- rate_limit_wait(exc): after a 429, how many seconds until we may retry?
- on_failure(...):      what do we DO about it?        (rotate key / retry /
                        next model / stop)
- probe(profile, key):  one round-trip with one key, with the FULL response
                        sanity checks (bare-string HTML, no-choices, empty
                        content) that every entry must apply.

Consumers (keep thin!):
- core/utils/ask_gpt.ask_gpt       — pipeline hot path; adds caching + retry
                                     budgets + chain walking on top
- server/settings_api.test_llm     — UI test button; walks the key pool

Rule (enforced by check_single_source.js): the literals 401/402/403 and the
"mark dead + rotate" decision must appear ONLY in this file.
"""

from __future__ import annotations

# --- failure taxonomy -------------------------------------------------------
# TRANSIENT    network/timeout/5xx — worth retrying the same profile
#              after a backoff; the provider may recover in seconds.
# RATE_LIMIT   429 — the provider throttled us (e.g. new_api "maximum 10
#              requests in 1 minutes"). The model is FINE, we were just too
#              fast: waiting out the window is cheaper than burning the
#              retry budget and switching models mid-task.
# CONTENT      our own ValueErrors: bad JSON, failed validation, empty
#              content — the model itself can't produce what we need.
# PROFILE_DEAD 401/402/403 — this KEY is rejected (invalid / banned /
#              balance exhausted); no retry of this key will ever help.
DEAD_STATUS_CODES = (401, 402, 403)   # single source of truth
RATE_LIMIT_STATUS = 429               # single source of truth
TRANSIENT, CONTENT, PROFILE_DEAD = "transient", "content", "profile_dead"
RATE_LIMIT = "rate_limit"


def classify_error(e: Exception) -> str:
    """What kind of failure is this? -> transient | rate_limit | content | profile_dead."""
    from openai import APIConnectionError, APITimeoutError, APIStatusError
    if isinstance(e, (APIConnectionError, APITimeoutError)):
        return TRANSIENT
    if isinstance(e, APIStatusError):
        if e.status_code in DEAD_STATUS_CODES:
            return PROFILE_DEAD
        if e.status_code == RATE_LIMIT_STATUS:
            return RATE_LIMIT
        return TRANSIENT
    if isinstance(e, ValueError):
        return CONTENT
    return TRANSIENT  # unknown: assume recoverable, bounded by caller budget


# --- rate-limit wait parsing (single source) --------------------------------
# How long to sleep after a 429. Sources, in order of trust:
#   1. Retry-After response header (seconds, or an HTTP date)
#   2. the provider's message:  new_api/one-api style
#      "maximum 10 requests in 1 minutes"  -> wait out the whole window (+1s
#      pad, so the oldest counted request has certainly left the window)
#   3. "try again in 12.5 seconds" / "retry after 30s"
#   4. fallback 30s (unknown shape — conservative middle ground)
import re as _re
from datetime import datetime, timezone as _tz
from email.utils import parsedate_to_datetime as _parsedate

_WINDOW_RE = _re.compile(
    r"maximum\s+(\d+)\s+requests?\s+in\s+(\d+(?:\.\d+)?)\s*"
    r"(second|minute|hour)s?", _re.I)
_TRY_AGAIN_RE = _re.compile(
    r"(?:try\s+again|retry(?:ing)?(?:\s+again)?)\s+in\s+([\d.]+)\s*"
    r"(s|sec|secs|second|seconds)\b", _re.I)
_WINDOW_UNIT = {"second": 1.0, "minute": 60.0, "hour": 3600.0}

MAX_RATE_LIMIT_WAIT = 120.0    # never stall the pipeline longer than this
DEFAULT_RATE_LIMIT_WAIT = 30.0


def rate_limit_wait(e: Exception,
                    default: float = DEFAULT_RATE_LIMIT_WAIT) -> float:
    """Seconds to sleep before the next attempt after a 429.

    Kept here (not in ask_gpt) because "what the provider's answer MEANS"
    is a decision, and all request-failure decisions live in this module.
    Never raises — worst case it returns the default.
    """
    try:
        # 1) Retry-After header
        resp = getattr(e, "response", None)
        headers = getattr(resp, "headers", None)
        if headers is not None:
            try:
                ra = headers.get("retry-after") or headers.get("Retry-After")
            except Exception:                                  # noqa: BLE001
                ra = None
            if ra:
                try:
                    return min(max(float(ra), 0.0) + 0.5, MAX_RATE_LIMIT_WAIT)
                except (TypeError, ValueError):
                    try:  # HTTP-date form
                        when = _parsedate(str(ra))
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=_tz.utc)
                        secs = ((when - datetime.now(_tz.utc)).total_seconds())
                        return min(max(secs, 1.0), MAX_RATE_LIMIT_WAIT)
                    except Exception:                          # noqa: BLE001
                        pass

        # 2/3) message parsing
        msg = str(e)
        m = _WINDOW_RE.search(msg)
        if m:
            window = float(m.group(2)) * _WINDOW_UNIT[m.group(3).lower()]
            # N requests per window: the oldest one may leave the window any
            # time in (0, window] — waiting the full window guarantees it
            # has. +1s pad for clock skew / rounding.
            return min(window + 1.0, MAX_RATE_LIMIT_WAIT)
        m = _TRY_AGAIN_RE.search(msg)
        if m:
            return min(float(m.group(1)) + 0.5, MAX_RATE_LIMIT_WAIT)

        return default
    except Exception:                                          # noqa: BLE001
        return default


def on_failure(endpoint: str, key: str, exc: Exception,
               next_key_exists: bool) -> str:
    """What do we do about a failed attempt? -> rotate | retry | next_model | stop.

    This is THE place that encodes "401/402/403 => mark the key permanently
    dead, then prefer the next key of the SAME endpoint (no backoff, no
    style change) over switching models". Both ask_gpt and test_llm call
    this; neither may re-implement it.
    """
    kind = classify_error(exc)
    if kind == PROFILE_DEAD:
        from core.utils.llm_profiles import mark_key_dead
        mark_key_dead(endpoint, key)          # permanent (user-confirmed)
        return "rotate" if next_key_exists else "next_model"
    if kind == CONTENT:
        # model can't produce what we need — caller retries once cheaply
        # then must move on (ask_gpt: next profile; test: stop & report)
        return "retry"
    return "retry"  # transient: backoff-and-retry, bounded by caller budget


# --- response sanity checks (shared by every entry) ------------------------

def _response_dict(resp):
    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    if isinstance(resp, dict):
        return resp
    return dict(resp) if hasattr(resp, "__dict__") else {}


def _responses_text(resp_dict):
    text = resp_dict.get("output_text")
    if isinstance(text, str) and text:
        return text
    parts = []
    for item in resp_dict.get("output") or []:
        item = item if isinstance(item, dict) else {}
        for content in item.get("content") or []:
            content = content if isinstance(content, dict) else {}
            if content.get("type") in ("output_text", "text"):
                value = content.get("text")
                if isinstance(value, str):
                    parts.append(value)
    return "".join(parts)


def check_response(resp, api_type="chat_completions") -> tuple:
    """Validate a Chat Completions or Responses API response."""
    if isinstance(resp, str):
        snippet = resp[:120].lstrip().lower()
        if snippet.startswith("<!doctype") or snippet.startswith("<html"):
            return None, ("已连通但返回的是网页(HTML)——base_url 疑似缺少 /v1 后缀，"
                          "请检查网关地址")
        return None, f"已连通但响应非标准(网关返回裸字符串): {resp[:200]}"
    if resp is None:
        return None, "API returned an empty response (None)"

    resp_dict = _response_dict(resp)
    if api_type == "responses":
        content = _responses_text(resp_dict)
        if not content:
            status = resp_dict.get("status", "unknown")
            detail = resp_dict.get("error") or resp_dict.get("incomplete_details") or status
            return None, f"API returned empty Responses output (status={detail})"
        return content, None

    choices = resp_dict.get("choices")
    if not choices or not isinstance(choices, list):
        error_detail = resp_dict.get("error", resp_dict.get("message", "unknown"))
        return None, f"API response has no valid choices: {error_detail}"
    first_choice = choices[0]
    if isinstance(first_choice, dict):
        message = first_choice.get("message", {})
        resp_content = message.get("content")
        finish_reason = first_choice.get("finish_reason", "unknown")
    else:
        message = getattr(first_choice, "message", None)
        resp_content = getattr(message, "content", None) if message else None
        finish_reason = getattr(first_choice, "finish_reason", "unknown")
    if resp_content is None:
        return None, f"API returned empty content (finish_reason={finish_reason})"
    return resp_content, None


def create_text_response(client, api_type, model, prompt, *, max_tokens,
                         temperature=None, response_format=None, thinking_type=None,
                         timeout=None):
    """Dispatch a text request using the selected OpenAI-compatible protocol."""
    if api_type == "responses":
        params = {"model": model, "input": prompt, "max_output_tokens": max_tokens}
        if temperature is not None:
            params["temperature"] = temperature
        if response_format:
            params["text"] = {"format": {"type": "json_object"}}
        if thinking_type in ("enabled", "disabled"):
            params["reasoning"] = {
                "effort": "high" if thinking_type == "enabled" else "none"}
        if timeout is not None:
            params["timeout"] = timeout
        return client.responses.create(**params)

    params = {"model": model, "messages": prompt, "max_tokens": max_tokens,
              "response_format": response_format, "extra_body": (
                  {"thinking": {"type": thinking_type}} if thinking_type else None)}
    if temperature is not None:
        params["temperature"] = temperature
    if timeout is not None:
        params["timeout"] = timeout
    return client.chat.completions.create(**params)


def probe(profile: dict, key: str, timeout: int = 15, prompt: str = "Reply with the single word: ok") -> tuple:
    """One cheap round-trip with ONE key and a caller-selected prompt.

    Honors the global network policy (direct/proxy/auto with fallback).
    Returns (ok, result_dict, status_code_or_None):
      ok=True          -> {"reply": text, "via": mode, "policy": mode}
      ok=False         -> {"error": msg, "via"?: mode, "policy"?: mode}
      status_code      -> set when the failure was an APIStatusError, so the
                          caller can ask on_failure() without re-classifying.
    Used by the UI test button (settings_api.test_llm)."""
    from core.utils import net_policy
    from core.utils.llm_profiles import normalize_base_url
    from openai import APIStatusError

    base_url = normalize_base_url(profile["base_url"])
    mode, _, _ = net_policy.get_policy()
    clients = {}
    if mode in ("direct", "auto"):
        clients["direct"] = net_policy.make_openai_client(
            key, base_url, "direct", timeout)
    if mode in ("proxy", "auto"):
        clients["proxy"] = net_policy.make_openai_client(
            key, base_url, "proxy", timeout)

    api_type = profile.get("api_type", "chat_completions")
    # Connectivity probes must stay cheap; never enable reasoning here.
    probe_thinking = "disabled" if profile.get("thinking_type") else None
    last_err, status = "", None
    for label, client in clients.items():
        try:
            resp = create_text_response(
                client, api_type, profile["model"],
                [{"role": "user", "content": prompt}], max_tokens=32,
                thinking_type=probe_thinking, timeout=timeout)
            content, err = check_response(resp, api_type=api_type)
            if err:
                return False, {"error": err, "via": label}, None
            return True, {"reply": (content or "").strip(), "via": label,
                          "policy": mode}, None
        except APIStatusError as e:
            last_err = f"[{label}] {str(e)[:200]}"
            status = e.status_code
        except Exception as e:
            last_err = f"[{label}] {str(e)[:200]}"
    return False, {"error": last_err, "policy": mode}, status


def probe_pool(profile: dict, prompt: str = "Reply with the single word: ok") -> dict:
    """Test-button entry point: walk the profile's key pool.

    401/402/403 -> key marked permanently dead -> next key automatically
    (same decision pipeline runs use). Other failures stop and report the
    current key's error. Returns a JSON-serializable result dict."""
    from core.utils.llm_profiles import load_llm_config
    prof = (load_llm_config().get("profiles") or {}).get(profile
                                                          if isinstance(profile, str)
                                                          else profile["name"])
    if prof is None:
        return {"ok": False, "error": f"模型不存在: {profile}"}
    if not prof.get("key"):
        return {"ok": False, "error": "API key is not set"}

    keys = list(prof.get("keys") or [prof["key"]])
    endpoint = prof.get("endpoint", prof["name"])
    dead_marked = []
    for ki, key in enumerate(keys):
        ok, result, status = probe(prof, key, prompt=prompt)
        if ok:
            if dead_marked:
                result["rotated"] = True
                result["dead_marked"] = dead_marked
                result["key_index"] = ki + 1
            result["key_total"] = len(keys)
            return {"ok": True, **result}
        if status in DEAD_STATUS_CODES:
            decision = on_failure(endpoint, key, _status_error(status),
                                  next_key_exists=ki + 1 < len(keys))
            if decision == "rotate":
                dead_marked.append(ki + 1)
                continue
            dead_marked.append(ki + 1)
            break  # pool exhausted
        result["key_total"] = len(keys)
        if dead_marked:
            result["dead_marked"] = dead_marked
        return {"ok": False, **result}
    return {"ok": False, "key_total": len(keys), "dead_marked": dead_marked,
            "error": f"endpoint 的 {len(keys)} 个 key 全部被拒（401/402/403），已全部标记失效"}


def _status_error(status_code: int) -> Exception:
    """Synthesize a classified exception for on_failure() from a raw status."""
    from openai import APIStatusError
    try:
        e = APIStatusError(
            f"Error code: {status_code}", response=None, body=None)
        e.status_code = status_code
        return e
    except Exception:
        return RuntimeError(f"status {status_code}")
