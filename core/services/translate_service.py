"""Translate service — chunk splitting + checkpoint resume + terminology.

Single source of truth for step-4 translation mechanics, shared by:
- core/_4_1_summarize.get_summary      (build the per-job glossary)
- core/_4_2_translate.translate_all    (the pipeline step)
- server/translate_api                 (the split playground: L1~L4)

Functions:
- split_chunks(sentences, chunk_size, max_sentences) -> [chunks]
    THE chunking rule (characters AND sentence-count bounded). No other
    module may re-implement chunk splitting.
- TranslationCkpt                      per-chunk checkpoint store
    (output/log/translate_ckpt.json, atomic writes, thread-safe)
- load_active_terms()                  merge the CHECKED base term sets
    (terms/*.json) into the "bottom plate" for step 4.1
- load_job_terms()                     the per-job glossary 4.1 wrote:
    bottom plate + freshly extracted terms. THE source for 4.2 / playground
- merge_terms()                        the one dedup / override rule
- validate_term_set_name()             the one filename rule (create/edit/
                                       write-back)
- create_or_replace_term_set()         save a WHOLE base set (new / edit)
- write_back_terms()                   checked per-job terms -> ONE base set
- term_catalog()                       read-only view of EVERY term for #/terms:
                                       union rows + each source file's FULL list
- build_shared_context(...)            full summary + full terms + neighbors

Config keys (config.yaml):
  translate:
    chunk_size: 2400        # max characters per chunk
    max_sentences: 10       # max sentences per chunk
  terminology:
    active: [default]       # which terms/*.json sets are merged, in order
                            # [] = check NONE (no implicit default fallback)
"""

from __future__ import annotations

import json
import os
import re
import threading

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TERMS_DIR = os.path.join(_PROJECT_ROOT, "terms")
CKPT_FILENAME = "output/log/translate_ckpt.json"

DEFAULTS = {"chunk_size": 2400, "max_sentences": 10}
_TERMS_LOCK = threading.Lock()       # serialise read-modify-write on terms/
_TERM_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff-]{1,48}$")


def _cfg_int(section: str, key: str, default: int) -> int:
    try:
        from core.utils.config_utils import load_key
        val = load_key(f"{section}.{key}")
        return int(val) if val else default
    except Exception:
        return default


# ---------------------------------------------------------------- chunking

def split_chunks(sentences: list, chunk_size: int | None = None,
                 max_sentences: int | None = None) -> list:
    """Group sentences into chunks bounded by BOTH character count and
    sentence count. A single over-long sentence becomes its own chunk
    (never truncated, never merged with neighbors).

    `sentences`: list of sentence strings, OR a single raw text string
    (the playground passes raw text) — normalized here: CRLF stripped,
    blank lines dropped."""
    if isinstance(sentences, str):
        # raw text input (playground / paste): normalize line endings
        sentences = [s for s in
                     (t.strip() for t in
                      sentences.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
                     if s]
    elif sentences and isinstance(sentences[0], str) and "\n" in sentences[0]:
        # a list whose first element is a multi-line BLOB (only \n counts —
        # a single line ending in \r is a CRLF artifact, not a blob)
        sentences = [s for s in
                     (t.strip() for t in
                      sentences[0].replace("\r\n", "\n").replace("\r", "\n").split("\n"))
                     if s]
    sentences = [s.replace("\r", "").strip() for s in sentences]
    chunk_size = chunk_size or _cfg_int("translate", "chunk_size",
                                        DEFAULTS["chunk_size"])
    max_sentences = max_sentences or _cfg_int("translate", "max_sentences",
                                              DEFAULTS["max_sentences"])
    chunks, chunk, n_chars, n_sents = [], [], 0, 0
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        would = n_chars + len(s) + (1 if chunk else 0)
        if chunk and (would > chunk_size or n_sents >= max_sentences):
            chunks.append("\n".join(chunk))
            chunk, n_chars, n_sents = [], 0, 0
        chunk.append(s)
        n_chars += len(s) + (1 if n_sents else 0)
        n_sents += 1
    if chunk:
        chunks.append("\n".join(chunk))
    return chunks


def chunk_stats(chunks: list) -> dict:
    """Playground L1/L2 stats for a chunk list."""
    sizes = [len(c) for c in chunks]
    sents = [c.count("\n") + 1 for c in chunks]
    return {
        "count": len(chunks),
        "chars": {"total": sum(sizes), "min": min(sizes) if sizes else 0,
                  "max": max(sizes) if sizes else 0,
                  "avg": round(sum(sizes) / len(sizes)) if sizes else 0},
        "sentences": {"total": sum(sents),
                      "min": min(sents) if sents else 0,
                      "max": max(sents) if sents else 0},
    }


# ------------------------------------------------------- checkpoint resume

def chunks_fingerprint(sentences: list, n_chunks: int,
                       extra: str = "") -> str:
    """Identity of THIS chunking: source sentences + how they were grouped
    + optional extra input signature (the terminology signature).

    Change the chunking params (or re-run step 3) and every stored chunk
    index now points at different text — reusing old translations would
    silently pair translations with the wrong source. Change the glossary
    and old chunks silently keep translations made with the OLD terms —
    so step 4.2 passes `terms_signature(terms)` as `extra`.
    The caller passes this to `TranslationCkpt.ensure_fingerprint` before
    resuming.
    """
    import hashlib
    if isinstance(sentences, str):
        sentences = [sentences]
    h = hashlib.sha1()
    h.update("\n".join(s for s in sentences if s).encode("utf-8"))
    h.update(f"|{n_chunks}".encode())
    if extra:
        h.update(f"|{extra}".encode())
    return h.hexdigest()[:16]


class TranslationCkpt:
    """Per-chunk translation results, persisted between runs.

    Shape: {"version": 1, "fingerprint": str, "chunks": {"<idx>": {"lines":
    N, "translation": "..."}}}. Atomic writes (tmp + rename) — safe against
    kill/power-off. A rerun resumes from the first missing chunk, but ONLY
    if the fingerprint still matches (see `ensure_fingerprint`).
    ask_gpt's prompt cache remains the inner safety net for single-call
    retries."""

    def __init__(self, path: str = CKPT_FILENAME):
        self.path = path
        self._lock = threading.Lock()
        self._data = None

    def load(self) -> dict:
        if self._data is not None:
            return self._data
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self._data = {"version": 1, "chunks": {}}
        self._data.setdefault("chunks", {})
        return self._data

    def get(self, idx: int) -> dict | None:
        with self._lock:
            return self.load()["chunks"].get(str(idx))

    def put(self, idx: int, result: dict) -> None:
        with self._lock:
            d = self.load()
            d["chunks"][str(idx)] = result
            self._save()

    def missing(self, total: int) -> list:
        with self._lock:
            have = self.load()["chunks"]
            return [i for i in range(total) if str(i) not in have]

    def ensure_fingerprint(self, fingerprint: str) -> bool:
        """Keep the stored chunks only if they belong to THIS chunking.

        Returns True when an existing checkpoint was kept, False when it was
        dropped (first run, or the input/params changed since the last run).
        """
        with self._lock:
            d = self.load()
            if d.get("fingerprint") == fingerprint:
                return True
            d["fingerprint"] = fingerprint
            d["chunks"] = {}
            self._save()
            return False

    def clear(self) -> None:
        with self._lock:
            fp = self.load().get("fingerprint")
            self._data = {"version": 1, "fingerprint": fp, "chunks": {}}
            self._save()

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


# ------------------------------------------------------------ terminology

def list_term_sets() -> list:
    """[{name, desc, count}] for every terms/*.json (sorted by name)."""
    os.makedirs(TERMS_DIR, exist_ok=True)
    out = []
    for fn in sorted(os.listdir(TERMS_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(TERMS_DIR, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
            terms = data.get("terms", [])
            out.append({"name": fn[:-5], "desc": data.get("desc", ""),
                        "count": len(terms)})
        except (OSError, ValueError):
            continue
    return out


def load_term_set(name: str) -> dict:
    with open(os.path.join(TERMS_DIR, f"{name}.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def save_term_set(name: str, data: dict) -> None:
    os.makedirs(TERMS_DIR, exist_ok=True)
    path = os.path.join(TERMS_DIR, f"{name}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def validate_term_set_name(name: str) -> str:
    """THE rule for a term-set filename — create / edit / write-back all
    share it. No dots or separators, so nothing can escape terms/."""
    name = (name or "").strip()
    if not _TERM_NAME_RE.fullmatch(name):
        raise ValueError("术语库名只能含中文/字母/数字/-/_，最长 48 字符")
    return name


def create_or_replace_term_set(name: str, data: dict, *, create: bool = False,
                               overwrite: bool = False) -> dict:
    """Save a WHOLE base set from the #/terms editor (new / edit).

    Name validation, entry normalisation (merge_terms drops empty src and
    dedups), and the atomic write all live here — no second decision in the
    API layer. `create=True` refuses to clobber an existing set unless the
    user confirmed (`overwrite=True`); the API maps FileExistsError to 409.
    """
    name = validate_term_set_name(name)
    terms = merge_terms(data.get("terms") or [])
    with _TERMS_LOCK:
        exists = os.path.isfile(os.path.join(TERMS_DIR, f"{name}.json"))
        if create and exists and not overwrite:
            raise FileExistsError(
                f"术语库 {name} 已存在，覆盖会替换全部词条，请确认")
        desc = data.get("desc", "")
        if exists and "desc" not in data:
            try:
                desc = load_term_set(name).get("desc", "")
            except (OSError, ValueError):
                pass
        save_term_set(name, {"desc": desc, "terms": terms})
    return {"name": name, "count": len(terms), "created": not exists}


def active_term_names() -> list:
    """Checked base term sets, in merge order.

    `[]` deliberately means "check NONE" (the user can uncheck every set
    for a new video). Only a MISSING / unreadable config key falls back to
    the legacy `default` set — an explicit empty list must never be
    silently replaced (v1.15).
    """
    try:
        from core.utils.config_utils import load_key
        names = load_key("terminology.active")
    except Exception:
        return ["default"]
    if names is None:
        return ["default"]
    return [names] if isinstance(names, str) else list(names)


def merge_terms(*groups, warn_conflicts: bool = False) -> list:
    """THE dedup / override rule for every glossary in this project.

    Groups are merged left to right on `src.strip().lower()`; a LATER
    group overrides `tgt` / `note` of an earlier one while keeping the
    first occurrence's position and spelling. Entries without a usable
    `src` are dropped. Pass `(label, terms)` tuples together with
    `warn_conflicts=True` to announce cross-set overrides in the log.

    Used by:
    - load_active_terms(): later CHECKED set wins (labelled + warned)
    - step 4.1:            the checked bottom plate wins over freshly
                           extracted LLM terms (plate appended last)
    - load_job_terms():    normalise the on-disk per-job glossary
    """
    merged, seen = [], {}
    for group in groups:
        if isinstance(group, dict):
            group = group.get("terms")
        label, items = group if isinstance(group, tuple) else (None, group)
        for t in items or []:
            if not isinstance(t, dict) or not t.get("src"):
                continue
            src = str(t["src"]).strip()
            if not src:
                continue
            key = src.lower()
            entry = {"src": src, "tgt": str(t.get("tgt") or ""),
                     "note": str(t.get("note") or "")}
            if key in seen:
                if warn_conflicts and label:
                    from core.utils.log_hooks import rprint
                    rprint(f"[yellow]⚠️ 术语冲突: '{src}' 在 '{label}' 中覆盖了"
                           f"先前激活库的译文 → '{entry['tgt']}'[/yellow]")
                seen[key]["tgt"] = entry["tgt"]
                seen[key]["note"] = entry["note"]
                continue
            seen[key] = entry
            merged.append(entry)
    return merged


def load_active_terms(warn: bool = True) -> dict:
    """Merge the CHECKED base term sets (terms/*.json) — the bottom plate
    step 4.1 starts from. Returns {"terms": [...]}. `warn=False` is for
    read-only UI polls that would otherwise repeat conflict warnings."""
    groups = []
    for name in active_term_names():
        try:
            data = load_term_set(name)
        except (OSError, ValueError):
            continue       # broken / vanished set must not kill the pipeline
        groups.append((name, data.get("terms", [])))
    return {"terms": merge_terms(*groups, warn_conflicts=warn)}


def load_job_terms(allow_fallback: bool = True) -> dict | None:
    """Terminology ACTUALLY used by step 4.2 = checked bottom plate + the
    terms 4.1 freshly extracted, i.e. the per-job glossary 4.1 wrote.

    This file is per-task (relative cwd — batch mirror workspaces each get
    their own), while terms/*.json is project-wide. When it is missing or
    malformed (running 4.2 alone, a fresh workspace, the playground before
    step 4.1), fall back to the checked bottom plate so nothing breaks.
    An existing, well-formed glossary with `terms: []` is respected as an
    intentional EMPTY glossary — no silent re-injection of base sets.

    `allow_fallback=False` is the STRICT read used by the write-back UI:
    return None instead of the plate when 4.1 never produced a glossary.
    Otherwise a fallback plate could be mistaken for freshly extracted
    terms and written straight back into a base set.
    """
    from core.utils.models import _4_1_TERMINOLOGY
    try:
        with open(_4_1_TERMINOLOGY, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return load_active_terms() if allow_fallback else None
    if (not isinstance(data, dict) or "terms" not in data
            or not isinstance(data["terms"], list)):
        return load_active_terms() if allow_fallback else None
    return {"terms": merge_terms(data["terms"])}


def write_back_terms(target: str, srcs: list, overwrite: bool = False) -> dict:
    """THE write-back decision: checked per-job glossary entries -> ONE base
    term set (terms/*.json).

    `srcs` are KEYS only — the server re-reads src/tgt/note from the 4.1
    glossary, so a stale browser editor can never inject a different
    translation into a shared base set. An entry already present in the
    target is SKIPPED by default (a manually maintained base term outranks
    an LLM guess, exactly like step 4.1); `overwrite=True` replaces its
    tgt/note instead. A missing target is created with shared name
    validation; a CORRUPT target is refused rather than silently rebuilt.
    Only terms/*.json is touched — the per-job glossary, translation
    checkpoint and GPT cache all stay untouched (no re-translation cost).
    """
    name = validate_term_set_name(target)
    job = load_job_terms(allow_fallback=False)
    if job is None:
        raise ValueError("当前没有任务术语表：请先跑第 4.1 步（总结与抽术语）")
    wanted = {str(s).strip().lower() for s in (srcs or []) if str(s).strip()}
    if not wanted:
        raise ValueError("没有勾选任何术语")
    picked = [t for t in job["terms"] if t["src"].strip().lower() in wanted]
    if not picked:
        raise ValueError("勾选的术语在任务术语表里不存在")

    with _TERMS_LOCK:
        exists = os.path.isfile(os.path.join(TERMS_DIR, f"{name}.json"))
        if exists:
            try:
                current = load_term_set(name)
            except (OSError, ValueError):
                raise ValueError(f"术语库 {name} 读取失败或文件损坏，"
                                 f"请先在编辑页修复后再回写")
            desc = current.get("desc", "")
            merged = merge_terms(current.get("terms") or [])
        else:
            desc = "从任务术语表回写"
            merged = []
        index = {t["src"].strip().lower(): t for t in merged}
        added = updated = skipped = 0
        for t in picked:
            key = t["src"].strip().lower()
            if key in index:
                if not overwrite:
                    skipped += 1
                    continue
                index[key]["tgt"] = t["tgt"]
                index[key]["note"] = t["note"]
                updated += 1
            else:
                entry = {"src": t["src"], "tgt": t["tgt"], "note": t["note"]}
                index[key] = entry
                merged.append(entry)
                added += 1
        if added or updated:
            save_term_set(name, {"desc": desc, "terms": merged})
    return {"target": name, "added": added, "updated": updated,
            "skipped": skipped, "total": len(merged)}


def term_catalog() -> dict:
    """Read-only view of EVERY term in the project (v1.21, FR-13).

    Two complementary views, both decided here in ONE read:
    - `rows`:  one row per unique `src` (case-insensitive) — the merged
      union of ALL base sets (checked AND unchecked) + the per-job glossary.
      Good for "what does 4.2 actually inject".
    - `sources`: each source file's COMPLETE, verbatim entry list, one
      source per `terms/*.json` plus `output/log/terminology.json`. The
      union folds the same `src` appearing in several base sets into one
      row, which is NOT enough to read a whole base library — v1.22 keeps
      these per-file lists side by side so every term in every file is
      visible without opening an editor.

    Pure read: no writes, no config changes, no LLM calls, no checkpoint
    invalidation. Merge order, dedup and case folding stay in `merge_terms`;
    the "effective" value is taken from the SAME glossary 4.2 uses
    (`load_job_terms`, with its documented bottom-plate fallback), so an
    unchecked base set or a stale 4.1 output can never look "in use".

    Returns {rows: [...], stats: {...}, sources: [...]}. Row fields:
      src/tgt/note     effective value, or the first base variant when the
                       term is NOT in use (so the row is still readable)
      sets, variants   which base sets hold it and each set's own value
      effective        whether 4.2 would inject it into every prompt
      in_plate/in_job  present in the CURRENT checked plate / 4.1 output
      job_fresh        only in the per-job glossary (4.1 new, not yet written
                       back into any base set)
      conflict         base sets disagree on tgt/note for this src
      drift            4.1 froze a value that no current base set holds
    Source fields:
      id/kind/name/path/label  which file this list came from
      active           (base only) currently checked in terminology.active
      available        (job only) whether 4.1 really produced the glossary
      count/terms      the file's full normalised entry list, file order
    """
    sets = list_term_sets()
    active = active_term_names()
    active_names = {str(n) for n in active}
    plate = load_active_terms(warn=False)["terms"]
    job = load_job_terms(allow_fallback=False)      # None = 4.1 never ran
    effective = job["terms"] if job is not None else plate
    eff_index = {t["src"].strip().lower(): t for t in effective}
    plate_keys = {t["src"].strip().lower() for t in plate}
    job_index = ({t["src"].strip().lower(): t for t in job["terms"]}
                 if job is not None else {})

    rows, by_key = [], {}

    def row_for(src: str) -> dict:
        key = src.strip().lower()
        if key not in by_key:
            row = {"key": key, "src": src.strip(), "tgt": "", "note": "",
                   "sets": [], "variants": []}
            by_key[key] = row
            rows.append(row)
        return by_key[key]

    base_entries = 0
    source_list = []
    for meta in sets:
        name = meta["name"]
        try:
            raw = load_term_set(name).get("terms") or []
        except (OSError, ValueError):
            continue           # a broken set is skipped by list_term_sets too
        entries = merge_terms(raw)
        is_active = name in active_names
        source_list.append({
            "id": f"base:{name}", "kind": "base", "name": name,
            "path": f"terms/{name}.json", "label": f"terms/{name}.json",
            "active": is_active, "available": True,
            "count": len(entries), "terms": entries,
        })
        for t in entries:
            base_entries += 1
            row = row_for(t["src"])
            row["sets"].append(name)
            row["variants"].append({
                "set": name, "src": t["src"], "tgt": t["tgt"],
                "note": t["note"], "active": is_active,
            })
    # the per-job glossary = checked bottom plate + 4.1 fresh extraction.
    # This is the COMPLETE glossary 4.2 translates with, shown verbatim.
    job_terms = job["terms"] if job is not None else []
    source_list.append({
        "id": "job", "kind": "job", "name": "任务术语表",
        "path": "output/log/terminology.json",
        "label": "output/log/terminology.json",
        "active": True, "available": job is not None,
        "count": len(job_terms), "terms": job_terms,
    })
    # terms that exist ONLY in the per-job glossary (4.1 fresh extraction)
    for t in job_terms:
        row_for(t["src"])

    for row in rows:
        key = row["key"]
        eff = eff_index.get(key)
        variants = row["variants"]
        if eff is not None:
            row["tgt"], row["note"] = eff["tgt"], eff["note"]
            row["effective"] = True
        else:
            # inactive base-only term: show its own value, clearly NOT in use
            first = variants[0] if variants else None
            row["tgt"] = first["tgt"] if first else ""
            row["note"] = first["note"] if first else ""
            row["effective"] = False
        row["in_plate"] = key in plate_keys
        row["in_job"] = key in job_index
        row["job_fresh"] = row["in_job"] and not row["sets"]
        row["conflict"] = len({(v["tgt"], v["note"]) for v in variants}) > 1
        # 4.1 froze the plate at run time; if the base sets were edited since,
        # the injected value can differ from every current base variant.
        row["drift"] = bool(eff and variants
                            and (eff["tgt"], eff["note"]) not in
                            {(v["tgt"], v["note"]) for v in variants})

    return {
        "rows": rows,
        "sources": source_list,
        "stats": {
            "total": len(rows),
            "sets": len(sets),
            "active": len(active),
            "base_entries": base_entries,
            "job_available": job is not None,
            "job_count": len(job["terms"]) if job is not None else 0,
            "effective": sum(1 for r in rows if r["effective"]),
            "job_fresh": sum(1 for r in rows if r["job_fresh"]),
            "conflicts": sum(1 for r in rows if r["conflict"]),
        },
    }


def terms_signature(terms: dict) -> str:
    """Stable identity of the glossary 4.2 is about to use; feeds the
    translation checkpoint fingerprint so a changed glossary invalidates
    old chunk translations instead of silently mixing term conventions."""
    import hashlib
    raw = json.dumps(terms.get("terms") or [], ensure_ascii=False,
                     sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def migrate_custom_terms_xlsx() -> int:
    """One-time: build terms/default.json. Priority:
    1. custom_terms.xlsx (legacy user-maintained table) if it has rows
    2. the 4.1 per-job glossary (what the LLM actually extracted last run)
    Returns the number of terms migrated (0 = nothing to do / already done).
    """
    from core.utils.models import _4_1_TERMINOLOGY
    target = os.path.join(TERMS_DIR, "default.json")
    if os.path.exists(target):
        # already migrated — but if it's empty, still allow the fallback
        try:
            with open(target, "r", encoding="utf-8") as f:
                if json.load(f).get("terms"):
                    return 0
        except (OSError, ValueError):
            pass
    terms = []

    xlsx = os.path.join(_PROJECT_ROOT, "custom_terms.xlsx")
    if os.path.exists(xlsx):
        try:
            import pandas as pd
            df = pd.read_excel(xlsx).dropna(how="all")
            terms = [{"src": str(r.iloc[0]), "tgt": str(r.iloc[1]),
                      "note": str(r.iloc[2])}
                     for _, r in df.iterrows()]
            terms = [t for t in terms
                     if t["src"].strip() and t["src"] != "nan"]
        except Exception:
            terms = []

    desc = "从 custom_terms.xlsx 迁移"
    if not terms:
        # fallback: the last real terminology output of step 4.1
        try:
            with open(_4_1_TERMINOLOGY, "r", encoding="utf-8") as f:
                data = json.load(f)
            terms = [t for t in data.get("terms", [])
                     if isinstance(t, dict) and t.get("src")]
            desc = f"从上次 4.1 输出导入（{_4_1_TERMINOLOGY}）"
        except (OSError, ValueError):
            terms = []

    if not terms:
        return 0
    save_term_set("default", {"desc": desc, "terms": terms})
    return len(terms)


# ------------------------------------------------------- shared context

def build_shared_context(summary_text: str, terms: dict,
                         previous_lines: list | None,
                         after_lines: list | None) -> dict:
    """The four RAW content pieces every translation prompt carries:
    full summary + full terminology + neighboring lines. (2026-09-09
    redesign: modern 1M-context models do better with the whole picture.)

    Returns {summary, terms, previous_content, subsequent_content} — pure
    content, NO headers. Headers belong to the shared TEMPLATE (the single
    place that formats them), so preview == pipeline == exactly one
    "### Summary" etc. This fixed the doubled-headers bug where the context
    block carried its own headers AND the template printed them again."""
    term_lines = [f"- {t['src']} => {t['tgt']}"
                  + (f"  ({t['note']})" if t.get("note") else "")
                  for t in (terms.get("terms") or [])]
    return {
        "summary": summary_text or "",
        "terms": "\n".join(term_lines),
        "previous_content": "\n".join(previous_lines) if previous_lines else "",
        "subsequent_content": "\n".join(after_lines) if after_lines else "",
    }


def render_shared_context(parts: dict) -> str:
    """Assemble the shared-context BLOCK through the shared template —
    the one and only path used by BOTH the pipeline and the preview.
    Editing the shared prompt in the UI changes this output for real."""
    from core.prompts import generate_shared_prompt
    return generate_shared_prompt(
        parts.get("previous_content", ""),
        parts.get("subsequent_content", ""),
        parts.get("summary", ""),
        parts.get("terms", ""),
    )
