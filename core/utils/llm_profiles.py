"""LLM multi-model configuration: endpoints + models + per-task chains.

Config schema (config.yaml, "three tables" design 2026-09-08, named chains
2026-09-09):

    llm:
      retries_per_model: 2
      endpoints:                     # platform credentials (base_url + keys)
        llmpm:
          base_url: 'https://api.llm.pm'
          key: 'sk-...'              # single-key mode
        yhshu:
          base_url: 'https://www.yhshu.ai/'
          key_file: 'D:/keys.txt'    # pool mode: one key per line
      models:                        # model entries reference an endpoint
        glm-flash:
          endpoint: llmpm
          model: glm-5.3-flash
          llm_support_json: true
          thinking_type: 'disabled'
      chains:                        # NAMED reusable model sequences
        fast-cheap:                  # id: ^[a-z0-9-]+$, used by bindings
          label: 省钱快跑            # display alias (renamable)
          models: [gemini-flash, glm-flash]
        quality-first:
          label: 质量优先
          models: [glm-5.3, gemini-flash]
      tasks:                         # task -> chain ID binding
        default: fast-cheap
        translate_faith: quality-first

Key pools: a key file uses `name----key` per line (---- separator, name is a
label). Dead keys are marked PERMANENTLY in output/key_state.json (the user
confirmed dead keys never recover); a UI action can reset the marks.

Runtime shape: get_profile_chain() returns a list of resolved profile dicts
{ name, key, keys, base_url, model, llm_support_json, thinking_type } —
identical fields to the historical shape plus `keys` (the full pool, for
rotation). Consumers (ask_gpt, settings_api, models_api) are unchanged
apart from the key-rotation logic in ask_gpt.
"""

from core.utils.config_utils import load_key

_DEFAULT_RETRIES = 2

# ---------------- key pools ----------------

KEY_SEP = "----"          # `label----sk-...` in key files
KEY_STATE_FILE = "output/key_state.json"
_DEAD = "dead"            # permanent — user confirmed keys never recover


def _try_load(key, default=None):
    try:
        return load_key(key)
    except KeyError:
        return default


def load_key_state() -> dict:
    """{endpoint_name: {key_tail: status}} — persisted dead-key marks."""
    import json
    try:
        with open(KEY_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_key_state(state: dict) -> None:
    import json
    import os
    os.makedirs(os.path.dirname(KEY_STATE_FILE), exist_ok=True)
    tmp = KEY_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, KEY_STATE_FILE)


def _key_id(key: str) -> str:
    """Stable short id of a key (first 8 + last 4 chars) for state marks."""
    k = key.strip()
    if len(k) <= 12:
        return k
    return f"{k[:8]}..{k[-4:]}"


def mark_key_dead(endpoint_name: str, key: str) -> None:
    state = load_key_state()
    marks = state.setdefault(endpoint_name, {})
    marks[_key_id(key)] = _DEAD
    save_key_state(state)


def reset_endpoint_keys(endpoint_name: str | None) -> int:
    """Clear dead marks for one endpoint (or all when None). Returns how
    many marks were cleared."""
    state = load_key_state()
    if endpoint_name is None:
        n = sum(len(v) for v in state.values())
        state = {}
    else:
        n = len(state.get(endpoint_name, {}))
        state.pop(endpoint_name, None)
    if n:
        save_key_state(state)
    return n


def _is_key_dead(endpoint_name: str, key: str) -> bool:
    marks = load_key_state().get(endpoint_name, {})
    return marks.get(_key_id(key)) == _DEAD


def parse_key_file(path: str) -> list:
    """Read `label----key` lines. Lines without the separator are treated as
    a bare key (label = ''). Blank lines and # comments skipped."""
    import os
    keys = []
    with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if KEY_SEP in line:
                label, key = line.split(KEY_SEP, 1)
                key = key.strip()
                if key:
                    keys.append(key)
            else:
                keys.append(line)
    return keys


def endpoint_keys(raw_endpoint: dict) -> list:
    """Ordered live keys of an endpoint (dead keys filtered out)."""
    name = raw_endpoint.get("_name", "")
    keys = raw_endpoint.get("keys") or []
    if not keys and raw_endpoint.get("key"):
        keys = [raw_endpoint["key"]]
    return [k for k in keys if not _is_key_dead(name, k)]


# ---------------- base_url normalization ----------------

def normalize_base_url(base_url: str) -> str:
    """Canonical API base for OpenAI-compatible endpoints.

    Mirrors the historical logic in ask_gpt._call_once so the UI test button
    and fetch-models hit the exact same URL the pipeline uses:
      - ark endpoints   -> https://ark.cn-beijing.volces.com/api/v3
      - no '/v1' suffix -> append '/v1' (e.g. https://www.yhshu.ai/ -> .../v1)
    """
    base_url = (base_url or "").strip()
    if 'ark' in base_url:
        return "https://ark.cn-beijing.volces.com/api/v3"
    if 'v1' not in base_url:
        return base_url.strip('/') + '/v1'
    return base_url


# ---------------- config resolution ----------------

def _resolve_endpoints(llm: dict) -> dict:
    """endpoints section -> {name: {base_url, keys, _name}}."""
    endpoints = {}
    for name, raw in (llm.get("endpoints") or {}).items():
        if not isinstance(raw, dict) or not raw.get("base_url"):
            continue
        keys = []
        if raw.get("key_file"):
            try:
                keys = parse_key_file(raw["key_file"])
            except OSError:
                keys = []
        if raw.get("keys") and not raw.get("key_file"):
            keys = [k for k in raw["keys"] if k]
        if not keys and raw.get("key"):
            keys = [raw["key"]]
        endpoints[name] = {"base_url": raw["base_url"], "keys": keys,
                           "_name": name}
    return endpoints


def _resolve_models(llm: dict, endpoints: dict) -> dict:
    """models section -> {name: model entry}."""
    models = {}
    for name, raw in (llm.get("models") or {}).items():
        if not isinstance(raw, dict):
            continue
        ep = raw.get("endpoint")
        if ep not in endpoints:
            continue  # dangling reference — skip; chain validation reports it
        models[name] = {
            "name": name,
            "endpoint": ep,
            "model": raw.get("model", ""),
            "llm_support_json": bool(raw.get("llm_support_json", False)),
            "thinking_type": raw.get("thinking_type") or None,
        }
    return models


def load_llm_config():
    """Resolve the full LLM config: {endpoints, models, chains, tasks,
    retries_per_model, profiles}. `profiles` keeps the resolved flat shape
    (consumers read profile dicts, they never touch endpoints/models)."""
    llm = _try_load("llm")
    if llm is None:
        raise ValueError(
            "config.yaml has no llm section — configure endpoints/models "
            "in 设置→模型管理 (three-table: llm.endpoints / llm.models / llm.chains)")

    endpoints = _resolve_endpoints(llm)
    models = _resolve_models(llm, endpoints)
    chains, tasks = _resolve_chains_and_tasks(llm, models)

    retries = llm.get("retries_per_model", _DEFAULT_RETRIES)
    if not isinstance(retries, int) or retries < 1:
        raise ValueError("llm.retries_per_model must be a positive integer")

    profiles = {name: _as_profile(name, m, endpoints)
                for name, m in models.items()}
    return {"endpoints": endpoints, "models": models, "chains": chains,
            "tasks": tasks, "retries_per_model": retries, "profiles": profiles}


def _validate_model_refs(owner, chain, models):
    for name in chain:
        if name not in models:
            raise ValueError(
                f"{owner} references unknown model '{name}' "
                f"(defined models: {', '.join(models)})")


import re as _re

CHAIN_ID_RE = _re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def chain_id_ok(cid: str) -> bool:
    """Is this a usable chain id? THE single source of the id format —
    the write path (server/models_api) and the runtime (this module) share
    it, so a hand-edited config can never drift from what the UI accepts.

    English-only by design: the id is what task bindings reference, so it
    must survive YAML round-trips and stay rename-safe (the Chinese label
    is display-only and may change freely)."""
    return bool(CHAIN_ID_RE.match(cid))


def _resolve_chains_and_tasks(llm: dict, models: dict):
    """Resolve llm.chains + llm.tasks (task -> chain-ID binding).

    Chain entry (new format, v2 2026-09-09):
        <english-id>:
          label: 中文别名            # display only, renamable
          models: [model, ...]      # non-empty, all defined above

    A bare list value (old v1 format: chain-name -> [models]) is still
    accepted with its Chinese/any name kept as the key and label = key.

    Returns (chains, tasks):
      chains: {id: {"label": str, "models": [names]}}
      tasks:  {task: [model names]} — the resolved sequence consumers expect
    """
    chains = {}
    for cid, raw in (llm.get("chains") or {}).items():
        if isinstance(raw, dict):
            seq = raw.get("models")
            label = str(raw.get("label") or cid).strip() or cid
        else:
            # v1 bare-list format
            seq = raw
            label = cid
        if isinstance(seq, str):
            seq = [seq]
        if not isinstance(seq, list) or not seq:
            raise ValueError(f"llm.chains.{cid} must have a non-empty models list")
        _validate_model_refs(f"llm.chains.{cid}", seq, models)
        chains[cid] = {"label": label, "models": list(seq)}

    tasks = {}
    for task, val in (llm.get("tasks") or {}).items():
        if isinstance(val, str):
            if val not in chains:
                raise ValueError(
                    f"llm.tasks.{task} references unknown chain '{val}' "
                    f"(defined chains: {', '.join(chains) or 'none'})")
            tasks[task] = list(chains[val]["models"])
        elif isinstance(val, list) and val:
            # v0 legacy inline chain (task -> [model names]) — still resolves
            _validate_model_refs(f"llm.tasks.{task}", val, models)
            tasks[task] = list(val)
        else:
            raise ValueError(f"llm.tasks.{task} must be a chain id or a non-empty list")

    if "default" not in tasks:
        if not models:
            raise ValueError("llm config must define models")
        tasks["default"] = [next(iter(models))]

    return chains, tasks


def _as_profile(name, model_entry, endpoints):
    """Synthesize the flat runtime profile dict consumers expect."""
    ep = endpoints[model_entry["endpoint"]]
    keys = endpoint_keys(ep)
    return {
        "name": name,
        "endpoint": model_entry["endpoint"],
        "key": keys[0] if keys else "",
        "keys": keys,           # full pool for rotation in ask_gpt
        "base_url": ep["base_url"],
        "model": model_entry["model"],
        "llm_support_json": model_entry["llm_support_json"],
        "thinking_type": model_entry["thinking_type"],
    }


def get_profile_chain(task=None):
    """Ordered list of resolved profile dicts for a task (log_title)."""
    cfg = load_llm_config()
    names = cfg["tasks"].get(task) or cfg["tasks"]["default"]
    return [cfg["profiles"][name] for name in names]


def retries_per_model():
    return load_llm_config()["retries_per_model"]


def list_all_keys():
    """Every key string in the config (endpoint key pools), deduped.
    Used to unmask the call-log's masked keys for the local UI."""
    try:
        cfg = load_llm_config()
        seen = []
        for ep in (cfg.get("endpoints") or {}).values():
            for k in ep.get("keys") or []:
                if k and k not in seen:
                    seen.append(k)
        return seen
    except Exception:
        return []


def default_profile_config_prefix():
    """Dotted config path of the entry the sidebar should edit:
    'llm.models.<first model of default chain>'."""
    cfg = load_llm_config()
    first = cfg["tasks"]["default"][0]
    return f"llm.models.{first}"
