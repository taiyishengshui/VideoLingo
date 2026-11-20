"""Models REST API: three-table LLM config (endpoints / models / task
chains) + connectivity test + key-pool management.

Routes:
- GET  /api/models            -> resolved view: endpoints (masked) + models + chains
- PUT  /api/models/endpoint   -> upsert an endpoint (single key or key_file pool)
- DELETE /api/models/endpoint -> remove an endpoint (blocked if referenced)
- POST /api/models/endpoint/reset-keys -> clear dead-key marks of a pool
- PUT  /api/models/model      -> upsert a model entry (references an endpoint)
- DELETE /api/models/model    -> remove a model entry (blocked if in chains)
- PUT  /api/models/tasks      -> set task chains (validated against models)
- POST /api/models/test       -> connectivity test of one model entry
- POST /api/models/fetch-models -> /v1/models listing via an endpoint's key
"""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ruamel.yaml import YAML

from core.utils.config_utils import CONFIG_PATH
# chain-id 格式只有一个来源：写路径（这里）与运行时解析都用它
from core.utils.llm_profiles import chain_id_ok

router = APIRouter(prefix="/api/models", tags=["models"])

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
yaml = YAML()
yaml.preserve_quotes = True


def _load() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.load(f)


def _save(data: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f)


def _llm(data: dict) -> dict:
    return data.setdefault("llm", {})


def _models_in_use(llm: dict) -> dict:
    """{model_name: [chain ids]} — reverse index for reference checks.
    Models are referenced by named chains; tasks only bind to chain ids."""
    in_use = {}
    for cid, raw in (llm.get("chains") or {}).items():
        seq = raw.get("models") if isinstance(raw, dict) else raw
        for m in (seq or []):
            in_use.setdefault(m, []).append(cid)
    # legacy inline task lists still reference models directly
    for task, val in (llm.get("tasks") or {}).items():
        if isinstance(val, list):
            for m in val:
                in_use.setdefault(m, []).append(task)
    return in_use


# ---------------- GET: the resolved three-table view ----------------

@router.get("")
def get_models():
    from core.utils.llm_profiles import load_key_state
    data = _load()
    llm = data.get("llm") or {}

    endpoints = {}
    for name, raw in (llm.get("endpoints") or {}).items():
        safe = dict(raw)
        safe.pop("key", None)
        safe.pop("keys", None)
        safe["_has_key"] = bool(raw.get("key") or raw.get("key_file"))
        safe["_pool"] = _pool_stats(name, raw)
        endpoints[name] = safe

    models = {}
    for name, raw in (llm.get("models") or {}).items():
        models[name] = dict(raw)

    # decorate models with runtime key info from the resolver
    try:
        from core.utils.llm_profiles import load_llm_config
        resolved = load_llm_config()["profiles"]
        for name in models:
            if name in resolved:
                models[name]["_has_key"] = bool(resolved[name]["key"])
                models[name]["_key_count"] = len(resolved[name]["keys"])
    except Exception:
        pass

    # resolve chains through the same code path as runtime (validates refs,
    # synthesizes named chains from legacy inline task lists)
    try:
        from core.utils.llm_profiles import load_llm_config as _llc
        cfg = _llc()
        chains = cfg["chains"]
    except Exception:
        chains = llm.get("chains") or {}

    # task bindings as stored (task -> chain name or legacy list)
    tasks = llm.get("tasks") or {}

    return {
        "endpoints": endpoints,
        "models": models,
        "chains": chains,
        "tasks": tasks,
        "retries_per_model": llm.get("retries_per_model", 2),
    }


def _pool_stats(name: str, raw: dict) -> dict:
    """{total, dead} for a key pool (file or inline); zeros for single keys."""
    from core.utils.llm_profiles import (load_key_state, parse_key_file,
                                         _key_id)
    try:
        if raw.get("key_file"):
            keys = parse_key_file(raw["key_file"])
        elif raw.get("keys"):
            keys = [k for k in raw["keys"] if k]
        else:
            return {"total": 1 if raw.get("key") else 0, "dead": 0}
        marks = load_key_state().get(name, {})
        dead = sum(1 for k in keys if marks.get(_key_id(k)) == "dead")
        return {"total": len(keys), "dead": dead}
    except Exception:
        return {"total": 0, "dead": 0}


# ---------------- endpoints CRUD ----------------

class EndpointUpsert(BaseModel):
    name: str
    base_url: str
    key: str | None = None        # single-key mode; empty = keep existing
    key_file: str | None = None   # pool mode: `label----key` lines


@router.put("/endpoint")
def upsert_endpoint(req: EndpointUpsert):
    if not _NAME_RE.match(req.name):
        raise HTTPException(400, "endpoint 名只能含字母/数字/-/_，最长 32")
    if not req.base_url:
        raise HTTPException(400, "base_url 不能为空")

    data = _load()
    llm = _llm(data)
    endpoints = llm.setdefault("endpoints", {})
    existing = endpoints.get(req.name, {})

    entry = {"base_url": req.base_url.strip()}
    key_file = (req.key_file or "").strip() or existing.get("key_file")
    if key_file:
        if not os.path.isfile(os.path.expanduser(key_file)):
            raise HTTPException(400, f"key 文件不存在: {key_file}")
        entry["key_file"] = key_file
    else:
        key = (req.key or "").strip() or existing.get("key")
        if not key:
            raise HTTPException(400, "新建 endpoint 必须填 API Key 或 key 文件路径")
        entry["key"] = key
    endpoints[req.name] = entry
    _save(data)
    return {"ok": True, "name": req.name}


class NameRequest(BaseModel):
    name: str


@router.delete("/endpoint")
def delete_endpoint(req: NameRequest):
    data = _load()
    llm = _llm(data)
    endpoints = llm.get("endpoints") or {}
    if req.name not in endpoints:
        raise HTTPException(404, f"endpoint 不存在: {req.name}")
    referenced = [m for m, v in (llm.get("models") or {}).items()
                  if v.get("endpoint") == req.name]
    if referenced:
        raise HTTPException(409,
                            f"endpoint 被模型引用: {', '.join(referenced)}，先改模型再删")
    del endpoints[req.name]
    _save(data)
    return {"ok": True}


@router.post("/endpoint/reset-keys")
def reset_keys(req: NameRequest):
    """Clear permanently-dead key marks for one endpoint (or all: name='*')."""
    from core.utils.llm_profiles import reset_endpoint_keys
    target = None if req.name == "*" else req.name
    n = reset_endpoint_keys(target)
    return {"ok": True, "cleared": n}


@router.post("/endpoint/reveal-key")
def reveal_endpoint_key(req: NameRequest):
    """Full key of an endpoint (explicit request, single-user local tool)."""
    from core.utils.llm_profiles import parse_key_file
    data = _load()
    try:
        raw = data["llm"]["endpoints"][req.name]
    except (KeyError, TypeError):
        raise HTTPException(404, f"endpoint 不存在: {req.name}")
    if raw.get("key"):
        return {"name": req.name, "keys": [raw["key"]]}
    if raw.get("key_file"):
        try:
            return {"name": req.name, "keys": parse_key_file(raw["key_file"])}
        except OSError as e:
            raise HTTPException(400, f"读取 key 文件失败: {e}")
    return {"name": req.name, "keys": []}


# ---------------- models CRUD ----------------

class ModelUpsert(BaseModel):
    name: str
    endpoint: str
    model: str
    llm_support_json: bool = True
    thinking_type: str | None = None
    api_type: str = "chat_completions"


@router.put("/model")
def upsert_model(req: ModelUpsert):
    if not _NAME_RE.match(req.name):
        raise HTTPException(400, "模型名只能含字母/数字/-/_，最长 32")
    data = _load()
    llm = _llm(data)
    endpoints = llm.get("endpoints") or {}
    if req.endpoint not in endpoints:
        raise HTTPException(404, f"endpoint 不存在: {req.endpoint}（先在平台凭证卡创建）")
    if not req.model:
        raise HTTPException(400, "model 不能为空")
    if req.api_type not in ("chat_completions", "responses"):
        raise HTTPException(400, "api_type 须为 chat_completions 或 responses")
    entry = {"endpoint": req.endpoint, "model": req.model.strip(),
             "llm_support_json": req.llm_support_json,
             "api_type": req.api_type}
    if req.thinking_type:
        entry["thinking_type"] = req.thinking_type
    llm.setdefault("models", {})[req.name] = entry
    _save(data)
    return {"ok": True, "name": req.name}


@router.delete("/model")
def delete_model(req: NameRequest):
    data = _load()
    llm = _llm(data)
    models = llm.get("models") or {}
    if req.name in models:
        in_use = _models_in_use(llm)
        if req.name in in_use:
            raise HTTPException(409,
                                f"模型被链引用: {', '.join(in_use[req.name])}，先改链再删")
        del models[req.name]
        _save(data)
        return {"ok": True}
    raise HTTPException(404, f"模型不存在: {req.name}")


# ---------------- chains + task bindings ----------------

class ChainEntry(BaseModel):
    label: str = ""               # display alias (Chinese OK)
    models: list[str]


class ChainsUpdate(BaseModel):
    chains: dict[str, ChainEntry]   # {"fast-cheap": {label: "省钱快跑", models: [...]}}
    tasks: dict[str, str]           # {"default": "fast-cheap", ...} (task -> chain id)
    retries_per_model: int | None = None


@router.put("/chains")
def put_chains(req: ChainsUpdate):
    """Save named chains + task->chain bindings in one round-trip.

    Chain id must match ^[a-z0-9][a-z0-9-]{0,31}$ (English, stable —
    bindings reference it); label is display-only and freely renamable.
    Validation: every chain references defined models (non-empty); every
    task binding references a defined chain; 'default' binding must exist."""
    data = _load()
    llm = _llm(data)
    known_models = set(llm.get("models") or {})

    if not req.chains:
        raise HTTPException(400, "至少需要一条链")
    for cid, entry in req.chains.items():
        if not chain_id_ok(cid):
            raise HTTPException(400,
                                f"链 id '{cid}' 不合法（小写字母/数字/-，最长 32）")
        if not entry.models:
            raise HTTPException(
                400, f"链 {cid} 不能为空（请添加模型，或在任务链卡片删除该链；"
                     "删除会把绑定它的任务自动改绑到默认链）")
        unknown = [p for p in entry.models if p not in known_models]
        if unknown:
            raise HTTPException(400,
                                f"链 {cid} 引用不存在的模型: {', '.join(unknown)}")

    if not req.tasks.get("default"):
        raise HTTPException(400, "必须为 default 绑定一条链")
    for task, cid in req.tasks.items():
        if cid not in req.chains:
            raise HTTPException(400,
                                f"任务 {task} 绑定的链 '{cid}' 不存在")

    # chain entries are stored with a label (default = id when blank)
    llm["chains"] = {cid: {"label": (e.label or "").strip() or cid,
                           "models": e.models}
                     for cid, e in req.chains.items()}
    llm["tasks"] = req.tasks
    if req.retries_per_model is not None:
        if not (0 <= req.retries_per_model <= 10):
            raise HTTPException(400, "retries_per_model 须在 0-10")
        llm["retries_per_model"] = req.retries_per_model
    _save(data)
    return {"ok": True}


# ---------------- test + fetch-models ----------------

class TestRequest(BaseModel):
    profile: str   # model entry name
    prompt: str


@router.post("/test")
def test_profile(req: TestRequest):
    if not req.prompt.strip():
        raise HTTPException(400, "测试请求不能为空")
    from server.settings_api import test_llm as _test
    return _test(req)


class FetchModelsRequest(BaseModel):
    base_url: str
    key: str | None = None        # explicit key
    endpoint: str | None = None   # reuse the (first live) key of an endpoint
    use_proxy: bool | None = None # None = follow network.proxy_mode


@router.post("/fetch-models")
def fetch_models(req: FetchModelsRequest):
    """List models from an OpenAI-compatible /v1/models endpoint so the UI
    can offer a dropdown instead of asking the user to type model names.

    Auth: explicit key or the saved key/pool of `endpoint`."""
    key = (req.key or "").strip()
    if not key and req.endpoint:
        try:
            raw = _load()["llm"]["endpoints"][req.endpoint]
        except (KeyError, TypeError):
            raise HTTPException(404, f"endpoint 不存在: {req.endpoint}")
        from core.utils.llm_profiles import endpoint_keys
        ep = dict(raw)
        ep["_name"] = req.endpoint
        if raw.get("key_file"):
            try:
                from core.utils.llm_profiles import parse_key_file
                ep["keys"] = parse_key_file(raw["key_file"])
            except OSError:
                ep["keys"] = []
        keys = endpoint_keys(ep)
        if not keys:
            raise HTTPException(400, f"endpoint {req.endpoint} 没有可用 key（可能全部已标记失效）")
        key = keys[0]
    if not key:
        raise HTTPException(400, "缺少 API Key（填入 key 或指定 endpoint）")

    from core.utils.llm_profiles import normalize_base_url
    url = normalize_base_url(req.base_url).rstrip("/") + "/models"

    import urllib.request
    import json as _json
    from core.utils import net_policy
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}"})

    def _try(opener_open):
        with opener_open(request, timeout=15) as resp:
            return _json.loads(resp.read().decode("utf-8"))

    try:
        # use_proxy (UI toggle) overrides the global policy; None = follow config
        primary = net_policy.urllib_opener(req.use_proxy)
        fallback = (net_policy.urllib_fallback_opener()
                    if net_policy.get_policy()[0] == "auto" and req.use_proxy is None
                    else None)
        try:
            payload = _try(primary)
        except Exception:
            if fallback is None:
                raise
            # direct failed — maybe the site REQUIRES the proxy; retry once
            payload = _try(fallback)
    except Exception as e:
        msg = f"{url}: {e}"
        return {"ok": False, "error": msg[:300], "models": [],
                "hint": net_policy.network_error_hint(msg)}

    # OpenAI shape: {"data": [{"id": "gpt-4", ...}, ...]}; some proxies
    # return a bare list of strings or dicts — handle all three.
    items = payload.get("data") if isinstance(payload, dict) else payload
    models = []
    if isinstance(items, list):
        for it in items:
            mid = it.get("id") if isinstance(it, dict) else str(it)
            if mid:
                models.append(mid)
    models.sort()
    return {"ok": True, "url": url, "models": models[:500]}


@router.post("/reveal-key")
def reveal_key(req: NameRequest):
    """Explicitly return the full key behind a model entry — the user must be
    able to see which key is in use when editing. Local single-user tool
    (bound to 127.0.0.1); the GET list still never carries keys."""
    data = _load()
    llm = data.get("llm") or {}
    m = (llm.get("models") or {}).get(req.name)
    if m and m.get("endpoint"):
        ep_req = NameRequest(name=m["endpoint"])
        return reveal_endpoint_key(ep_req)
    raise HTTPException(404, f"模型不存在: {req.name}")
