import os
import json
import re
from threading import Lock
import json_repair
from openai import OpenAI
from core.utils.config_utils import load_key
from rich import print as rprint
from core.utils.decorator import except_handler

LOCK = Lock()
GPT_LOG_FOLDER = 'output/gpt_log'
_CACHE_MISS = object()

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



@except_handler("GPT request failed", retry=5)
def ask_gpt(prompt, resp_type=None, valid_def=None, log_title="default"):
    if not load_key("api.key"):
        raise ValueError("API key is not set")
    cached = _load_cache(prompt, resp_type, log_title)
    if cached is not _CACHE_MISS:
        rprint("use cache response")
        return cached

    model = load_key("api.model")
    base_url = load_key("api.base_url")
    if 'ark' in base_url:
        base_url = "https://ark.cn-beijing.volces.com/api/v3"
    elif 'v1' not in base_url:
        base_url = base_url.strip('/') + '/v1'
    client = OpenAI(api_key=load_key("api.key"), base_url=base_url)
    response_format = {"type": "json_object"} if resp_type == "json" and load_key("api.llm_support_json") else None

    effective_prompt = prompt
    if resp_type == "json" and not load_key("api.llm_support_json"):
        effective_prompt = prompt + "\n\nIMPORTANT: Respond with a valid JSON object only. Do not wrap in markdown. Do not return a list."

    params = dict(
        model=model,
        messages=[{"role": "user", "content": effective_prompt}],
        response_format=response_format,
        timeout=300
    )
    resp_raw = client.chat.completions.create(**params)

    if resp_raw is None:
        raise ValueError("❎ API returned an empty response (None)")

    if hasattr(resp_raw, 'model_dump'):
        resp_dict = resp_raw.model_dump()
    elif isinstance(resp_raw, dict):
        resp_dict = resp_raw
    else:
        resp_dict = dict(resp_raw) if hasattr(resp_raw, '__dict__') else {}

    choices = resp_dict.get("choices")
    if not choices or not isinstance(choices, list) or len(choices) == 0:
        error_detail = resp_dict.get("error", resp_dict.get("message", "unknown"))
        raise ValueError(f"❎ API response has no valid choices: {error_detail}")

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
        raise ValueError(f"❎ API returned empty content (finish_reason={finish_reason})")

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
            _save_cache(model, prompt, resp_content, resp_type, resp, log_title="error", message=valid_resp['message'])
            raise ValueError(f"❎ API response error: {valid_resp['message']}")

    _save_cache(model, prompt, resp_content, resp_type, resp, log_title=log_title)
    return resp


if __name__ == '__main__':
    from rich import print as rprint
    result = ask_gpt("""test respond ```json\n{\"code\": 200, \"message\": \"success\"}\n```""", resp_type="json")
    rprint(f"Test json output result: {result}")
