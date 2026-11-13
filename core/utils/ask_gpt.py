import os
import json
import re
import time
from threading import Lock
import json_repair
from openai import OpenAI, APIConnectionError
from core.utils.llm_profiles import (get_profile_chain, retries_per_model,
                                     normalize_base_url)
from core.utils import net_policy
from core.utils.log_hooks import rprint

LOCK = Lock()
GPT_LOG_FOLDER = 'output/gpt_log'
_CALL_LOG = os.path.join(GPT_LOG_FOLDER, 'calls.jsonl')
_CACHE_MISS = object()

# 429 rate-limit waits allowed per profile before moving to the next model.
# The wait LENGTH is a decision (llm_service.rate_limit_wait); the retry-loop
# BUDGET for those waits lives here with the loop that spends it.
MAX_RATE_LIMIT_WAITS = 3


def _log_call(**fields):
    """Append ONE line to output/gpt_log/calls.jsonl — the append-only trace
    of every LLM call attempt (success AND failure): which task, which
    endpoint/model/key, how long, what went wrong. Traceability without
    terminal scrolling. Logging must NEVER break the pipeline.

    Keys are MASKED here (first8..last4): the file is meant to be shared
    with AI/others for debugging — no plaintext secrets on disk."""
    try:
        with LOCK:
            os.makedirs(GPT_LOG_FOLDER, exist_ok=True)
            key = fields.get("key")
            if isinstance(key, str) and len(key) > 12:
                fields["key"] = key[:8] + ".." + key[-4:]
            rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **fields}
            with open(_CALL_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

# --- failure taxonomy -------------------------------------------------------
# Decision logic (what each failure kind MEANS and what we DO about it) lives
# in core/services/llm_service.py — the single source of truth. This module
# only keeps the kind constants for its retry loop.
from core.services.llm_service import (TRANSIENT, CONTENT, PROFILE_DEAD,
                                       RATE_LIMIT)


def _classify_error(e):
    from core.services.llm_service import classify_error
    return classify_error(e)


def _save_cache(model, prompt, resp_content, resp_type, resp, message=None, log_title="default"):
    with LOCK:
        logs = []
        file = os.path.join(GPT_LOG_FOLDER, f"{log_title}.json")
        os.makedirs(os.path.dirname(file), exist_ok=True)
        if os.path.exists(file):
            with open(file, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        logs.append({"model": model, "prompt": prompt, "resp_content": resp_content, "resp_type": resp_type, "resp": resp, "message": message})
        with open(file, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=4)

def _load_cache(prompt, resp_type, log_title):
    with LOCK:
        file = os.path.join(GPT_LOG_FOLDER, f"{log_title}.json")
        if os.path.exists(file):
            with open(file, 'r', encoding='utf-8') as f:
                for item in json.load(f):
                    if item["prompt"] == prompt and item["resp_type"] == resp_type:
                        return item["resp"]
        return _CACHE_MISS


def _strip_markdown_json(text: str) -> str:
    # unwrap outer string literal if present (e.g. `"\"```json...\""`  )
    stripped = text.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        try:
            stripped = json.loads(stripped)
        except Exception:
            pass
    # find the last ```json ... ``` block
    matches = list(re.finditer(r'```(?:json[s]?)?\s*([{\[].*?)```', stripped, re.DOTALL))
    if matches:
        return matches[-1].group(1).strip()
    # fallback: find first { or [ to end
    match = re.search(r'([{\[].*)', stripped, re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped



def _sanitize_proxy_env():
    """Strip leading/trailing whitespace (and quotes) from proxy env vars.

    A malformed value like " http://127.0.0.1:7890" (note the leading space,
    URL-encoded as %20 in httpx's error) makes httpx fail every request with
    "Unknown scheme for proxy URL" — retries can't help. Windows users often
    pick up such values from system environment variable settings. Fixing
    them in-process is safe: httpx only reads these at client construction.
    """
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        val = os.environ.get(key)
        if not val:
            continue
        cleaned = val.strip().strip('"').strip("'").split()[0] if val.strip() else ""
        if cleaned and cleaned != val:
            rprint(f"[yellow]⚠️ Proxy env {key} had stray whitespace/quotes — cleaned.[/yellow]")
            os.environ[key] = cleaned
        elif not cleaned:
            # value was only whitespace → drop it so httpx ignores it entirely
            del os.environ[key]


def _call_once(profile, prompt, resp_type, valid_def, log_title,
               bypass_cache=False, chain_info=""):
    """Single attempt against one profile. Raises on any failure; caching of
    successful responses happens here (keyed by the ORIGINAL prompt, so cache
    hits stay model-agnostic)."""
    model = profile["model"]
    thinking_type = profile["thinking_type"]
    base_url = profile["base_url"]
    base_url = normalize_base_url(profile["base_url"])
    response_format = {"type": "json_object"} if resp_type == "json" and profile["llm_support_json"] else None

    effective_prompt = prompt
    if resp_type == "json" and not profile["llm_support_json"]:
        effective_prompt = prompt + "\n\nIMPORTANT: Respond with a valid JSON object only. Do not wrap in markdown. Do not return a list."

    params = dict(
        model=model,
        messages=[{"role": "user", "content": effective_prompt}],
        response_format=response_format,
        temperature=0.4,
        timeout=300
    )
    if thinking_type:
        params["extra_body"] = {
            "thinking": {
                "type": thinking_type
            }
        }

    def _request_with_mode(mode):
        client = net_policy.make_openai_client(
            profile["key"], base_url, mode, timeout=params["timeout"])
        return client.chat.completions.create(**params)

    modes = net_policy.ordered_modes(base_url)
    # request log: what exactly is being called (user-requested visibility)
    key = profile.get("key", "")
    from core.utils.llm_profiles import _key_id
    rprint(f"🌐 LLM 请求 [{log_title}] {base_url} | model={model} | "
           f"key={_key_id(key)} ({profile.get('name', '?')})")
    resp_raw = None
    last_exc = None
    t0 = time.time()
    try:
        for mode in modes:
            try:
                resp_raw = _request_with_mode(mode)
                net_policy.remember_mode(base_url, mode)
                break
            except APIConnectionError as e:
                # Could be the proxy (or its absence) breaking this connection.
                # If another mode is allowed by the policy, fall through to it.
                last_exc = e
                continue
            except Exception:
                raise
        if resp_raw is None:
            raise last_exc
    except Exception as e:
        _log_call(task=log_title, profile=profile.get("name"), model=model,
                  base_url=base_url, key=key, ok=False, chain=chain_info,
                  error=f"{type(e).__name__}: {e}"[:300], secs=round(time.time() - t0, 1))
        raise

    if resp_raw is None:
        raise ValueError("❎ API returned an empty response (None)")

    # response sanity rules live in the service layer (single source)
    from core.services.llm_service import check_response
    resp_content, err = check_response(resp_raw)
    if err:
        raise ValueError(f"❎ {err}")

    if resp_type == "json":
        cleaned = _strip_markdown_json(resp_content)
        resp = json_repair.loads(cleaned)
        if isinstance(resp, list):
            resp = {str(i + 1): v for i, v in enumerate(resp)}
        elif not isinstance(resp, dict):
            raise ValueError(f"❎ Expected JSON object, got {type(resp).__name__}: {repr(resp_content[:200])}")
    else:
        resp = resp_content

    if valid_def:
        valid_resp = valid_def(resp)
        if valid_resp['status'] != 'success':
            _log_call(task=log_title, profile=profile.get("name"), model=model,
                      base_url=base_url, key=key, ok=False, chain=chain_info,
                      error=f"validation: {valid_resp['message']}"[:300],
                      secs=round(time.time() - t0, 1))
            _save_cache(model, prompt, resp_content, resp_type, resp, log_title="error", message=valid_resp['message'])
            raise ValueError(f"❎ API response error: {valid_resp['message']}")

    _log_call(task=log_title, profile=profile.get("name"), model=model,
              base_url=base_url, key=key, ok=True, chain=chain_info,
              in_chars=len(prompt), out_chars=len(resp_content or ""),
              secs=round(time.time() - t0, 1))
    if not bypass_cache:
        _save_cache(model, prompt, resp_content, resp_type, resp, log_title=log_title)
    return resp


def ask_gpt(prompt, resp_type=None, valid_def=None, log_title="default",
            bypass_cache=False):
    """Ask an LLM, walking the task's profile chain on failure.

    bypass_cache=True skips the response cache entirely (both load AND
    save) — used by the split playground's L3 probe, where re-testing the
    same chunk must exercise the REAL call path, not replay yesterday's
    cached answer.

    Chain resolution: log_title → llm.tasks chain (or 'default'); each
    profile gets `llm.retries_per_model` attempts before moving to the next.
    Old single-`api:` configs transparently become a one-profile chain.

    Key pools: when a profile's endpoint carries multiple keys and the
    failure is PROFILE_DEAD (401/402/403 = key rejected), the NEXT key of
    the same endpoint is tried first — same model, no backoff, no style
    change mid-task. Only when the pool is exhausted does the chain move to
    the next profile. Dead keys are marked permanently in
    output/key_state.json (see llm_profiles.mark_key_dead).

    Rate limits (429): the provider throttled us, not a broken model —
    llm_service.rate_limit_wait parses the wait (Retry-After header /
    "maximum N requests in M minutes" / "try again in Xs"), we sleep it and
    retry the SAME profile WITHOUT spending retries_per_model. Bounded by
    MAX_RATE_LIMIT_WAITS per profile, then the chain moves on."""
    if not bypass_cache:
        cached = _load_cache(prompt, resp_type, log_title)
        if cached is not _CACHE_MISS:
            rprint("use cache response")
            # NOTE: no local import here — the module-level import is the
            # single binding; a function-level `from ... import` would make
            # the name local to the whole function and break the
            # bypass_cache path below ("referenced before assignment")
            chain_info = ">".join(p["name"] for p in get_profile_chain(log_title))
            # a cache hit IS an execution — record it (free, no request sent)
            _log_call(task=log_title, profile="(cache)", model="(cache)",
                      key="—", ok=True, cached=True, chain=chain_info,
                      in_chars=len(prompt), secs=0.0)
            return cached

    _sanitize_proxy_env()
    chain = get_profile_chain(log_title)
    if not chain[0]["key"]:
        raise ValueError("API key is not set")
    per_model = retries_per_model()
    last_exc = None

    for pi, profile in enumerate(chain):
        keys = list(profile.get("keys") or [profile["key"]])
        ki = 0  # index into the endpoint's key pool
        attempts_left = per_model
        rate_waits = 0  # 429 waits this profile — bounded separately from
        # the model-retry budget: a rate limit says "too fast", not "model
        # can't do it", so waiting must not burn retries_per_model.
        # chain context for the call log: which chain, which position —
        # a bare "glm53 ✓" doesn't say whether it's the first pick or the
        # fallback that saved the run
        chain_info = (f"{'>'.join(p['name'] for p in chain)} "
                      f"(#{pi + 1}/{len(chain)})")
        while attempts_left > 0:
            work = dict(profile)
            work["key"] = keys[ki]
            try:
                return _call_once(work, prompt, resp_type, valid_def, log_title,
                                  bypass_cache=bypass_cache,
                                  chain_info=chain_info)
            except Exception as e:
                last_exc = e
                kind = _classify_error(e)
                if kind == RATE_LIMIT:
                    # decision (how long to wait) lives in llm_service
                    from core.services.llm_service import rate_limit_wait
                    rate_waits += 1
                    if rate_waits > MAX_RATE_LIMIT_WAITS:
                        rprint(f"[yellow]⚠️ {profile['name']} 已连续限流 "
                               f"{MAX_RATE_LIMIT_WAITS} 次 —— 切链上下一模型"
                               f"[/yellow]")
                        break
                    wait = rate_limit_wait(e)
                    rprint(f"[yellow]⏳ 限流（{profile['name']}）："
                           f"{str(e)[:160]}[/yellow]")
                    rprint(f"[yellow]   等 {wait:.0f}s 让限流窗口重置后重试，"
                           f"不计入重试预算（还剩 "
                           f"{MAX_RATE_LIMIT_WAITS - rate_waits + 1}"
                           f" 次等待）[/yellow]")
                    time.sleep(wait)
                    continue  # attempts_left untouched
                attempts_left -= 1
                tag = (f"[{profile['name']}|{profile['model']}|key#{ki + 1}] "
                       f"attempt {per_model - attempts_left}/{per_model}")
                rprint(f"[red]GPT request failed {tag}: {e}[/red]")
                if kind == PROFILE_DEAD:
                    # decision logic (mark dead + rotate preference) lives in
                    # llm_service.on_failure — single source of truth
                    from core.services.llm_service import on_failure
                    decision = on_failure(profile.get("endpoint", profile["name"]),
                                          keys[ki], e,
                                          next_key_exists=ki + 1 < len(keys))
                    if decision == "rotate":
                        ki += 1
                        attempts_left = per_model  # fresh budget for a fresh key
                        rprint(f"[yellow]🔑 key#{ki} rejected — rotating to next "
                               f"key of endpoint '{profile.get('endpoint')}' "
                               f"({len(keys) - ki} left)[/yellow]")
                        continue
                    break  # pool exhausted — next profile immediately
                if kind == CONTENT:
                    if attempts_left <= 0:
                        break  # model can't do the task — don't burn time
                    continue   # one immediate second chance, no backoff
                if attempts_left > 0:  # transient: backoff then retry
                    time.sleep(2 ** (per_model - attempts_left - 1))
        if pi + 1 < len(chain):
            nxt = chain[pi + 1]
            rprint(f"[yellow]⚠️ {profile['name']} ({profile['model']}) failed — "
                   f"switching → {nxt['name']} ({nxt['model']})[/yellow]")

    raise last_exc


if __name__ == '__main__':
    from core.utils.log_hooks import rprint
    result = ask_gpt("""test respond ```json\n{\"code\": 200, \"message\": \"success\"}\n```""", resp_type="json")
    rprint(f"Test json output result: {result}")
