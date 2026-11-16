"""Split playground API — L1/L2/L3 verification of translation chunking.

Routes:
- POST /api/translate/preview  (L1+L2, free): split a text with given
  params, return per-chunk stats + cost estimate
- POST /api/translate/probe    (L3, paid): translate SELECTED chunks via
  the real translate_lines path (translate_faith task chain), report
  success/line-count per chunk. NEVER writes pipeline artifacts.

All chunking goes through core/services/translate_service.split_chunks —
the same code the pipeline step uses (single source of truth).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.services.translate_service import (
    active_term_names, build_shared_context, chunk_stats, split_chunks,
    load_active_terms, load_job_terms, DEFAULTS,
)

router = APIRouter(prefix="/api/translate", tags=["translate"])

_SPLIT_NLP = os.path.join("output", "log", "split_by_nlp.txt")


class PreviewRequest(BaseModel):
    text: str | None = None          # pasted text (L1 input)
    use_split_file: bool = False     # load output/log/split_by_nlp.txt instead
    chunk_size: int | None = None
    max_sentences: int | None = None


@router.post("/preview")
def preview(req: PreviewRequest):
    """L1 (split preview) + L2 (cost estimate). Free, no LLM calls."""
    if req.use_split_file:
        try:
            with open(_SPLIT_NLP, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            raise HTTPException(404, f"未找到 {_SPLIT_NLP}——先跑切句步骤，或粘贴文本")
    else:
        text = (req.text or "").strip()
        if not text:
            raise HTTPException(400, "文本为空：粘贴文本或选择载入切句结果")

    chunks = split_chunks(text, req.chunk_size, req.max_sentences)
    stats = chunk_stats(chunks)

    # L2: cost estimate under the CURRENT config (reflect toggle)
    try:
        from core.utils.config_utils import load_key
        reflect = bool(load_key("reflect_translate"))
        calls_per_chunk = 2 if reflect else 1
        active = active_term_names()
    except Exception:
        calls_per_chunk, active = 1, ["default"]

    # EXACT upload size per chunk: render the real faith prompt through
    # the same path translate_lines uses (template + shared context +
    # lines + json example). Pure string building — free and fast.
    try:
        from core._4_1_summarize import load_full_summary
        summary_text = load_full_summary()
    except Exception:
        summary_text = ""
    terms = load_job_terms()
    from core.prompts import get_prompt_faithfulness

    per_chunk = []
    prompt_total = 0
    from core.services.translate_service import render_shared_context
    for i, c in enumerate(chunks):
        prev = None if i == 0 else chunks[i - 1].split("\n")[-3:]
        after = None if i == len(chunks) - 1 else chunks[i + 1].split("\n")[:2]
        ctx = render_shared_context(
            build_shared_context(summary_text, terms, prev, after))
        prompt_chars = len(get_prompt_faithfulness(c, ctx))
        prompt_total += prompt_chars
        per_chunk.append({
            "index": i,
            "chars": len(c),
            "sentences": c.count("\n") + 1,
            "prompt_chars": prompt_chars,
            "preview": c[:400] + ("…" if len(c) > 400 else ""),
        })

    return {
        "chunks": per_chunk,
        "stats": stats,
        "params": {
            "chunk_size": req.chunk_size or DEFAULTS["chunk_size"],
            "max_sentences": req.max_sentences or DEFAULTS["max_sentences"],
        },
        "estimate": {
            "llm_calls": stats["count"] * calls_per_chunk,
            "calls_per_chunk": calls_per_chunk,
            "reflect": calls_per_chunk == 2,
            "active_term_sets": active,
            # the glossary actually injected into every chunk (4.1's
            # per-job file when present, else the checked base sets)
            "terms_count": len(terms.get("terms") or []),
            # total upload volume if EVERY chunk is translated (faith only)
            "prompt_total_chars": prompt_total,
            "prompt_avg_chars": prompt_total // max(stats["count"], 1),
            "prompt_max_chars": max((p["prompt_chars"] for p in per_chunk), default=0),
            # how much of it is the repeated shared context (summary+terms)
            "context_chars": len(render_shared_context(
                build_shared_context(summary_text, terms, None, None))),
        },
    }


class ProbeRequest(BaseModel):
    text: str | None = None
    use_split_file: bool = False
    chunk_size: int | None = None
    max_sentences: int | None = None
    chunk_indices: list[int]        # which chunks to actually translate
    reflect: bool = False           # L3 probes faithfulness only by default
    use_cache: bool = False         # True = replay cached results when the
                                    # prompt matches (free); False = real call


class ProbeChunkResult(BaseModel):
    index: int
    ok: bool
    line_count_src: int = 0
    line_count_trans: int = 0
    error: str | None = None
    translation: str | None = None
    source: str | None = None       # the chunk's original text (for the
                                    # side-by-side comparison in the UI)


@router.post("/probe")
def probe(req: ProbeRequest):
    """L3: real translation of selected chunks (paid). Uses the SAME
    translate_lines + build_shared_context path as the pipeline, but writes
    NOTHING to the pipeline artifacts — results are returned inline only."""
    from core.translate_lines import translate_lines
    from core.services.translate_service import build_shared_context

    if req.use_split_file:
        try:
            with open(_SPLIT_NLP, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            raise HTTPException(404, f"未找到 {_SPLIT_NLP}")
    else:
        text = (req.text or "").strip()
        if not text:
            raise HTTPException(400, "文本为空")

    chunks = split_chunks(text, req.chunk_size, req.max_sentences)
    unknown = [i for i in req.chunk_indices if not (0 <= i < len(chunks))]
    if unknown:
        raise HTTPException(400, f"块序号越界: {unknown}（共 {len(chunks)} 块）")
    if not req.chunk_indices:
        raise HTTPException(400, "未选择任何块")

    # real context: full summary (if 4.1 ran) + the per-job glossary
    # (checked base sets + freshly extracted terms), same as step 4.2
    try:
        from core._4_1_summarize import load_full_summary
        summary_text = load_full_summary()
    except Exception:
        summary_text = ""
    terms = load_job_terms()

    results = []
    for i in req.chunk_indices:
        chunk = chunks[i]
        prev = None if i == 0 else chunks[i - 1].split("\n")[-3:]
        after = None if i == len(chunks) - 1 else chunks[i + 1].split("\n")[:2]
        ctx = build_shared_context(summary_text, terms, prev, after)
        try:
            translation, _ = translate_lines(chunk, ctx, i, reflect=req.reflect,
                                             bypass_cache=not req.use_cache)
            results.append(ProbeChunkResult(
                index=i, ok=True,
                line_count_src=chunk.count("\n") + 1,
                line_count_trans=translation.count("\n") + 1,
                translation=translation, source=chunk))
        except Exception as e:
            results.append(ProbeChunkResult(index=i, ok=False, error=str(e)[:500]))

    ok_count = sum(1 for r in results if r.ok)
    return {
        "results": [r.model_dump() for r in results],
        "summary": {
            "probed": len(results), "ok": ok_count,
            "failed": len(results) - ok_count,
            "chunk_total": len(chunks),
            "params": {
                "chunk_size": req.chunk_size or DEFAULTS["chunk_size"],
                "max_sentences": req.max_sentences or DEFAULTS["max_sentences"],
                "reflect": req.reflect,
            },
        },
    }


# ---------------- terminology sets (terms/*.json) ----------------

class TermSetSave(BaseModel):
    name: str
    desc: str | None = None   # None = 未提交描述，保留库里原有的值
    terms: list[dict]
    create: bool = False       # True = 新建：同名库需先确认，不能静默覆盖
    overwrite: bool = False    # 用户对同名 409 的确认


@router.get("/terms")
def get_term_sets():
    """Term sets + the per-job glossary review data + the full term catalog
    in ONE request.

    `job.available` is False when 4.1 never produced a glossary — the UI
    must NOT show the checked bottom plate as if it were extracted terms.
    `catalog.rows` is the deduped union; `catalog.sources` is each source
    file's COMPLETE list (every terms/*.json + output/log/terminology.json),
    so #/terms can display the whole content of each file without opening
    an editor. `history` indexes the archived glossaries available for
    import. Extending this response (instead of adding GET /terms/review,
    /terms/catalog or /terms/history) avoids the dynamic GET /terms/{name}
    route swallowing the sub-path. All reads/decisions live in
    translate_service; this layer only adapts HTTP -> service (G-01).
    """
    from core.services.translate_service import (
        active_term_names, history_term_sources, list_term_sets,
        load_active_terms, load_job_terms, term_catalog,
    )
    sets = list_term_sets()
    job = load_job_terms(allow_fallback=False)
    plate = load_active_terms(warn=False)
    return {
        "sets": sets,
        "active": active_term_names(),
        "job": {
            "available": job is not None,
            "terms": (job or {}).get("terms", []),
            "plate_srcs": [t["src"].strip().lower() for t in plate["terms"]],
            "sets": [s["name"] for s in sets],
        },
        # v1.21: all terms in one read-only view (every base set, checked or
        # not, + the per-job glossary). Extending GET /terms instead of adding
        # a sub-route: the dynamic GET /terms/{name} would swallow it.
        "catalog": term_catalog(),
        # v1.23: archived glossaries (metadata only) for the import picker.
        "history": history_term_sources(),
    }


@router.put("/terms")
def put_term_set(req: TermSetSave):
    """Create / edit a WHOLE base term set. Name validation, entry cleanup
    and the atomic write all live in translate_service (one decision)."""
    from core.services.translate_service import create_or_replace_term_set
    payload = {"terms": req.terms}
    if req.desc is not None:
        payload["desc"] = req.desc
    try:
        result = create_or_replace_term_set(
            req.name, payload, create=req.create, overwrite=req.overwrite)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileExistsError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, **result}


class WriteBackRequest(BaseModel):
    target: str                # existing set name, or a NEW name to create
    srcs: list[str]            # selected keys ONLY — server re-reads values
    overwrite: bool = False    # default: keep manually maintained tgt/note


@router.post("/terms/writeback")
def write_back(req: WriteBackRequest):
    """Checked per-job glossary entries -> one base term set. Never touches
    output/log/terminology.json, so the current translation / checkpoint /
    GPT cache stay valid (no re-translation cost)."""
    from core.services.translate_service import write_back_terms
    try:
        stats = write_back_terms(req.target, req.srcs, req.overwrite)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **stats}


class HistoryPreviewRequest(BaseModel):
    names: list[str]           # exact history/<name> directory names


@router.post("/terms/history/preview")
def history_preview(req: HistoryPreviewRequest):
    """Read-only dedup preview of selected archived glossaries. Never writes
    anything. POST (not GET) because GET /terms/{name} would swallow the
    sub-path; path resolution + merge both live in translate_service."""
    from core.services.translate_service import history_import_preview
    try:
        return history_import_preview(req.names)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class HistoryImportRequest(BaseModel):
    target: str                # existing set name, or a NEW name to create
    names: list[str]           # archives to re-read server-side
    srcs: list[str]            # selected KEYS only — values are re-read
    overwrite: bool = False    # default: keep manually maintained tgt/note
    desc: str | None = None    # optional description for a NEW set


@router.post("/terms/history/import")
def history_import(req: HistoryImportRequest):
    """Checked archived terms -> ONE base term set. Only terms/*.json is
    written: output/, history/, checkpoint and GPT cache stay untouched."""
    from core.services.translate_service import import_history_terms
    try:
        stats = import_history_terms(req.target, req.names, req.srcs,
                                     req.overwrite, req.desc)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, **stats}


class TermSetDelete(BaseModel):
    name: str


@router.delete("/terms")
def delete_term_set(req: TermSetDelete):
    import os
    from core.services.translate_service import TERMS_DIR, active_term_names
    if req.name in active_term_names():
        raise HTTPException(409, f"术语库 {req.name} 正在激活中，先取消激活再删除")
    path = os.path.join(TERMS_DIR, f"{req.name}.json")
    if not os.path.isfile(path):
        raise HTTPException(404, f"术语库不存在: {req.name}")
    os.remove(path)
    return {"ok": True}


@router.get("/terms/{name}")
def get_term_set(name: str):
    from core.services.translate_service import load_term_set
    try:
        return load_term_set(name)
    except (OSError, ValueError):
        raise HTTPException(404, f"术语库不存在: {name}")


class ActiveUpdate(BaseModel):
    active: list[str]


@router.put("/terms/active")
def put_active(req: ActiveUpdate):
    from core.services.translate_service import list_term_sets
    known = {s["name"] for s in list_term_sets()}
    unknown = [n for n in req.active if n not in known]
    if unknown:
        raise HTTPException(400, f"术语库不存在: {', '.join(unknown)}")
    from core.utils.config_utils import update_key
    # update_key returns False when the key is missing in config.yaml —
    # never report success for a selection that was NOT actually saved.
    if update_key("terminology.active", req.active) is False:
        raise HTTPException(500, "config.yaml 缺少 terminology.active，"
                                "勾选未保存（请补齐该键后重试）")
    return {"ok": True, "active": req.active}


# ---------------- prompt overrides (prompts/*.md) ----------------

def _language_defaults() -> dict:
    """src_lang/tgt_lang defaults for the preview UI — read from config."""
    from core.utils.config_utils import load_key
    try:
        src = load_key("whisper.detected_language") or ""
    except KeyError:
        src = ""
    try:
        tgt = load_key("target_language") or ""
    except KeyError:
        tgt = ""
    return {"src_lang": src, "tgt_lang": tgt}


@router.get("/prompts")
def get_prompts():
    from core.prompts import OVERRIDABLE_PROMPTS, get_default_template
    import os
    prompts_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
    out = []
    for name, desc in OVERRIDABLE_PROMPTS.items():
        path = os.path.join(prompts_dir, f"{name}.md")
        overridden = os.path.isfile(path)
        # editors ALWAYS see content: the override if present, otherwise the
        # built-in default template (so the user can start from something)
        if overridden:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        else:
            content = get_default_template(name)
        out.append({"name": name, "desc": desc, "overridden": overridden,
                    "content": content})
    return {"prompts": out, "language_defaults": _language_defaults()}


class PromptSave(BaseModel):
    name: str
    content: str


@router.put("/prompts")
def put_prompt(req: PromptSave):
    from core.prompts import OVERRIDABLE_PROMPTS
    import os
    if req.name not in OVERRIDABLE_PROMPTS:
        raise HTTPException(404, f"未知提示词: {req.name}")
    prompts_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
    os.makedirs(prompts_dir, exist_ok=True)
    with open(os.path.join(prompts_dir, f"{req.name}.md"), "w",
              encoding="utf-8") as f:
        f.write(req.content)
    return {"ok": True, "name": req.name}


class PromptName(BaseModel):
    name: str


@router.delete("/prompts")
def reset_prompt(req: PromptName):
    """Delete the override file -> the built-in default takes over again."""
    from core.prompts import OVERRIDABLE_PROMPTS
    import os
    if req.name not in OVERRIDABLE_PROMPTS:
        raise HTTPException(404, f"未知提示词: {req.name}")
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "prompts", f"{req.name}.md")
    if os.path.isfile(path):
        os.remove(path)
    return {"ok": True}


# ---------------- call log (output/gpt_log/calls.jsonl) ----------------

@router.get("/calls")
def get_call_log(task: str = "", limit: int = 200, only_fail: bool = False):
    """Traceability: the append-only record of every LLM call attempt.
    Returns the newest `limit` entries, optionally filtered by task /
    failures-only."""
    import os
    path = os.path.join("output", "gpt_log", "calls.jsonl")
    rows = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    import json as _json
                    rec = _json.loads(line)
                except ValueError:
                    continue
                if task and rec.get("task") != task:
                    continue
                if only_fail and rec.get("ok"):
                    continue
                rows.append(rec)
    rows.reverse()  # newest first
    return {"calls": rows[:max(1, min(limit, 1000))], "total": len(rows)}


# ---------------- translation verification (L4 QA) ----------------

class VerifyRequest(BaseModel):
    source: str                    # the chunk's original text
    translation: str               # the translation to review
    use_cache: bool = True         # default ON: free replay of past reviews;
                                   # uncheck for a real (paid) fresh review


class VerifyResult(BaseModel):
    ok: bool
    score: int | None = None
    issues: list[dict] = []
    suggestion: str | None = None
    error: str | None = None
    cached: bool = False


@router.post("/verify")
def verify(req: VerifyRequest):
    """L4: ask an LLM (translate_verify chain — preferably a DIFFERENT
    model than the one that translated) to review one chunk's translation.
    Results are inline-only; calls land in calls.jsonl for traceability."""
    from core.prompts import get_verify_prompt
    from core.utils.ask_gpt import ask_gpt

    source = (req.source or "").strip().replace("\r\n", "\n")
    translation = (req.translation or "").strip().replace("\r\n", "\n")
    if not source or not translation:
        raise HTTPException(400, "原文和译文都不能为空")

    prompt = get_verify_prompt(source, translation, _sample_shared_context())

    def _valid(data):
        if not isinstance(data, dict) or "score" not in data:
            return {"status": "error", "message": "missing 'score'"}
        return {"status": "success", "message": "ok"}

    try:
        result = ask_gpt(prompt, resp_type="json", valid_def=_valid,
                         log_title="translate_verify",
                         bypass_cache=not req.use_cache)
        return VerifyResult(
            ok=True,
            score=int(result.get("score", 0)),
            issues=result.get("issues") or [],
            suggestion=result.get("suggestion"),
        )
    except Exception as e:
        return VerifyResult(ok=False, error=str(e)[:500])


# ---------------- prompt preview (render with a sample sentence) --------

class PromptPreviewRequest(BaseModel):
    name: str                  # summary | shared | faith | express
    sample: str = ""           # sample sentence(s), one per line
    content: str | None = None # editor content — render THIS without saving
                               # (None -> current override/default template)
    src_lang: str | None = None  # language variable overrides (editable in UI)
    tgt_lang: str | None = None


SAMPLE_DEFAULT = ("This videotape is edited from an advanced training in "
                  "Boulder, Colorado, May 1988.\n"
                  "Many of the participants had also attended two advanced "
                  "trainings with Richard six and nine months earlier.")


@router.post("/prompts/preview")
def preview_prompt(req: PromptPreviewRequest):
    """Render a prompt EXACTLY as it would be sent to the LLM, using a
    sample sentence + the real per-job glossary and summary. Free (no
    LLM call) — this is what the model will actually receive.
    Does NOT save anything: ``content`` is rendered in memory."""
    from core.prompts import (get_prompt_faithfulness,
                              get_prompt_expressiveness,
                              generate_shared_prompt, get_summary_prompt)
    lines = (req.sample or SAMPLE_DEFAULT).strip().replace("\r\n", "\n")
    template = req.content.strip() if req.content else None
    kw = {"template": template,
          "src_lang": req.src_lang, "tgt_lang": req.tgt_lang}

    if req.name == "summary":
        prompt = get_summary_prompt(lines, load_active_terms(), **kw)
    elif req.name == "shared":
        # render exactly as translate_lines does: four raw pieces through
        # the shared template (headers printed once, by the template only)
        try:
            from core._4_1_summarize import load_full_summary
            summary_text = load_full_summary()
        except Exception:
            summary_text = ""
        terms = load_job_terms()
        from core.services.translate_service import build_shared_context
        parts = build_shared_context(summary_text, terms, None, None)
        prompt = generate_shared_prompt(
            parts["previous_content"], parts["subsequent_content"],
            parts["summary"], parts["terms"], template=template)
    elif req.name == "faith":
        prompt = get_prompt_faithfulness(lines, _sample_shared_context(), **kw)
    elif req.name == "express":
        ctx = _sample_shared_context()
        # faith result shape the express prompt expects
        faith = {str(i): {"origin": l, "direct": f"(直译 {i})"}
                 for i, l in enumerate(lines.split("\n"), 1)}
        prompt = get_prompt_expressiveness(faith, lines, ctx, **kw)
    else:
        raise HTTPException(404, f"未知提示词: {req.name}")

    return {"name": req.name, "prompt": prompt,
            "language_defaults": _language_defaults()}


def _sample_shared_context() -> str:
    """Shared context block built exactly like translate_lines builds it:
    four raw pieces, assembled through the shared template."""
    try:
        from core._4_1_summarize import load_full_summary
        summary_text = load_full_summary()
    except Exception:
        summary_text = ""
    from core.services.translate_service import (build_shared_context,
                                                 render_shared_context)
    return render_shared_context(
        build_shared_context(summary_text, load_job_terms(), None, None))
