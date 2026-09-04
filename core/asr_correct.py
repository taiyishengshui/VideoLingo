"""ASR word-correction engine: fix systematic recognition errors in the
word-level transcription store, then sync the fix everywhere downstream.

Design (confirmed with user):
- Layer 1 (local, zero API cost): entity unification by frequency + domain
  hotword fuzzy-match against asr_hints.json.
- Layer 2 (LLM): batch review of LOW-CONFIDENCE words only (whisperx gives
  every word a 0-1 alignment score; errors cluster in low scores), with
  context windows and domain prior. ~40 suspects per call.
- Layer 3 (write-back): precise (index, text) anchored mutation on the JSON
  store — timecodes and row count never touched. xlsx is re-exported.

Safety:
- idempotent: a correction report file marks completion; re-run skips.
- original xlsx/JSON backed up to .bak before first mutation
- per-call fix-rate guard (>15% = suspicious, abort that batch)
- every change logged with reason for human audit
"""

import os
import json
import shutil
import difflib
from collections import Counter

from core.utils import rprint, load_key
from core.utils.models import _2_CLEANED_CHUNKS
from core.utils.words_store import (
    _2_CLEANED_CHUNKS_JSON, load_words_json, save_words_json, export_words_xlsx,
)

_HINTS_FILE = "output/log/asr_checkpoint/asr_hints.json"
_CORRECTION_REPORT = "output/log/asr_correction_report.json"

# --- Layer 1: local, deterministic rules ----------------------------------

def _word_key(word: str) -> str:
    """Comparable key: lowercase, punctuation stripped (apostrophes kept)."""
    return "".join(c for c in word.lower() if c.isalnum() or c == "'")


def _unify_entities(words, min_count_gap=2):
    """Same-entity spelling drift: group by (len bucket, first letter, edit
    distance <= 1); minority spellings fold into the majority spelling."""
    counts = Counter(w["text"] for w in words)
    changes = []
    vocab = sorted(counts)
    for i, w in enumerate(words):
        key = _word_key(w["text"])
        if len(key) < 4:  # too short to judge safely
            continue
        candidates = []
        for other in vocab:
            if other == w["text"]:
                continue
            ok = _word_key(other), key
            if ok[0][0] == key[0] and abs(len(ok[0]) - len(key)) <= 1:
                ratio = difflib.SequenceMatcher(None, key, ok[0]).ratio()
                if ratio >= 0.85:
                    candidates.append((counts[other], other))
        if candidates:
            best_count, best_spelling = max(candidates)
            # majority must clearly dominate both the minority AND be common
            if best_count >= counts[w["text"]] + min_count_gap and counts[best_spelling] >= 3:
                changes.append({
                    "index": i, "old": w["text"], "new": best_spelling,
                    "reason": f"entity unification: '{w['text']}'x{counts[w['text']]} → '{best_spelling}'x{best_count}",
                    "layer": 1,
                })
    return changes


def _hotword_corrections(words, hints):
    """Domain-word repairs driven by the detected hotwords (e.g. whisper heard
    'meta program' but the domain says 'metaprogram'). Multi-word hotwords
    are matched across consecutive word entries."""
    hotwords = [h.strip() for h in (hints.get("hotwords") or "").split(",") if h.strip()]
    if not hotwords:
        return []
    hw_keys = {}
    for h in hotwords:
        parts = [_word_key(p) for p in h.split()]
        if parts and all(len(p) >= 2 for p in parts):
            hw_keys[h] = parts

    changes = []
    for i in range(len(words)):
        for hw, parts in hw_keys.items():
            n = len(parts)
            if i + n > len(words):
                continue
            window = [_word_key(words[j]["text"]) for j in range(i, i + n)]
            if len(parts) == n:
                sim = sum(difflib.SequenceMatcher(None, a, b).ratio()
                          for a, b in zip(window, parts)) / n
                joined = "".join(window)
                target_joined = "".join(parts)
                # recognition split the word into parts, or near-miss spelling
                if (n > 1 and 0.7 <= sim and joined != target_joined) or \
                   (n == 1 and 0.75 <= sim < 1.0 and len(window[0]) >= 4):
                    changes.append({
                        "index": i, "old": " ".join(words[j]["text"] for j in range(i, i + n)),
                        "new": hw, "span": n,
                        "reason": f"hotword match ({sim:.2f}) to domain term '{hw}'",
                        "layer": 1,
                    })
    return changes


# --- Layer 2: LLM review of low-confidence words ---------------------------

def _llm_review(words, hints, score_threshold=0.5, batch_size=40):
    """Batch-review suspicious words (score below threshold) with context."""
    try:
        from core.utils import ask_gpt
    except ImportError:
        return []

    suspects = [
        (i, w) for i, w in enumerate(words)
        if isinstance(w.get("score"), (int, float)) and w["score"] < score_threshold
        and len(_word_key(w["text"])) >= 2
    ]
    if not suspects:
        return []

    domain = hints.get("domain", "") if hints else ""
    hotwords = hints.get("hotwords", "") if hints else ""
    changes = []

    for b in range(0, len(suspects), batch_size):
        batch = suspects[b:b + batch_size]
        items = []
        for i, w in batch:
            ctx = " ".join(words[j]["text"] for j in range(max(0, i - 5), min(len(words), i + 6)))
            items.append({"index": i, "word": w["text"], "context": ctx})

        prompt = (
            "You fix speech-recognition (ASR) errors in a transcript word list.\n"
            f"Audio domain: {domain}\nDomain vocabulary (authoritative spellings): {hotwords}\n\n"
            "Below are low-confidence words with surrounding context. For EACH word decide:\n"
            "- 'fix' if you are CERTAIN it is a simple misrecognition/mis-split/mis-join/\n"
            "  misspelling, and provide the corrected word (keep original casing style)\n"
            "- 'keep' otherwise (including all uncertain cases)\n\n"
            "Only fix word FORM. Never: change tone, add/remove meaning, fix grammar,\n"
            "censor, or 'improve' style. When in doubt, keep.\n\n"
            "Items:\n" + json.dumps(items, ensure_ascii=False) + "\n\n"
            "Respond with a JSON object: {\"fixes\": [{\"index\": <int>, "
            "\"old\": \"...\", \"new\": \"...\", \"reason\": \"...\"}, ...]}. "
            "Return an empty fixes array if nothing is certain."
        )

        def _valid(resp):
            if not isinstance(resp, dict) or "fixes" not in resp:
                return {"status": "error", "message": "missing fixes"}
            for f in resp["fixes"]:
                if not all(k in f for k in ("index", "old", "new")):
                    return {"status": "error", "message": "fix missing keys"}
            return {"status": "success", "message": ""}

        try:
            resp = ask_gpt(prompt, resp_type="json", valid_def=_valid,
                           log_title="asr_word_fix")
        except Exception as e:
            rprint(f"[yellow]⚠️ LLM review batch failed, skipping batch: {e}[/yellow]")
            continue

        batch_map = {i: w["text"] for i, w in batch}
        for f in resp.get("fixes", []):
            i = f.get("index")
            if i not in batch_map:
                continue
            if f.get("new") == f.get("old") or not f.get("new"):
                continue
            # sanity: LLM must echo the old word it claims to fix
            if _word_key(str(f.get("old", ""))) != _word_key(batch_map[i]):
                continue
            changes.append({
                "index": i, "old": batch_map[i], "new": str(f["new"]),
                "reason": f.get("reason", "llm"),
                "layer": 2,
            })

        # fix-rate guard: >15% in one batch smells like hallucinated 'fixes'
        fixed = len([f for f in changes if f["layer"] == 2])
        reviewed = b + len(batch)
        if fixed > 0.15 * reviewed:
            rprint(f"[red]❌ Fix rate too high ({fixed}/{reviewed}); aborting LLM review — "
                   f"audio quality or prompt issue. Local fixes kept.[/red]")
            return [c for c in changes if c["layer"] == 1]

    return changes


# --- Layer 3: write-back ----------------------------------------------------

def apply_corrections(words, changes):
    """Mutate words in place via (index) anchors. Multi-word span fixes
    (hotword joins like 'meta program'→'metaprogram') merge into the first
    slot and blank the rest — row count and ALL timecodes stay identical."""
    changes = sorted(changes, key=lambda c: c["index"])
    for c in changes:
        i = c["index"]
        if i >= len(words):
            continue
        # verify anchor still matches (layers don't overlap indices? they may —
        # later change wins only if old text still present)
        if _word_key(words[i]["text"]) != _word_key(str(c["old"]).split()[0]):
            continue
        span = c.get("span", 1)
        words[i]["text"] = c["new"]
        for j in range(i + 1, min(i + span, len(words))):
            words[j]["text"] = ""  # consumed by the joined word
    return words


def correct_transcription(force=False):
    """Main entry. Idempotent: a report file marks done unless force=True."""
    if os.path.exists(_CORRECTION_REPORT) and not force:
        rprint("[cyan]♻️ asr_correct already applied (report exists); skip. "
               "Use force=True to re-run.[/cyan]")
        return

    words = load_words_json()
    if words is None:
        rprint("[yellow]⚠️ cleaned_chunks.json not found — run _2_asr first. "
               "(Legacy xlsx-only projects: needs the JSON store.)[/yellow]")
        return

    hints = None
    if os.path.exists(_HINTS_FILE):
        try:
            with open(_HINTS_FILE, "r", encoding="utf-8") as f:
                hints = json.load(f).get("hints")
        except (json.JSONDecodeError, OSError):
            pass

    rprint(f"[cyan]🔧 Correcting transcription: {len(words)} words, "
           f"domain hints {'available' if hints else 'absent'}.[/cyan]")

    # Layer 1: local
    changes = _unify_entities(words)
    if hints:
        changes += _hotword_corrections(words, hints)
    # de-duplicate by index, keep first (layer 1 has priority)
    seen = set()
    layer1 = []
    for c in sorted(changes, key=lambda c: 0 if c["layer"] == 1 else 1):
        if c["index"] not in seen:
            layer1.append(c)
            seen.add(c["index"])

    # Layer 2: LLM (only on words layer 1 didn't already fix)
    llm_changes = _llm_review(words, hints)
    llm_changes = [c for c in llm_changes if c["index"] not in seen]
    all_changes = layer1 + llm_changes

    if not all_changes:
        rprint("[green]✅ No corrections needed.[/green]")
        _write_report(words, [])
        return

    # Backup originals once
    for src in (_2_CLEANED_CHUNKS_JSON, _2_CLEANED_CHUNKS):
        if os.path.exists(src):
            bak = src + ".bak"
            if not os.path.exists(bak):
                shutil.copy2(src, bak)

    apply_corrections(words, all_changes)

    # sync stores: JSON is primary, xlsx re-exported from it
    save_words_json(words)
    export_words_xlsx(words, _2_CLEANED_CHUNKS)

    rprint(f"[green]✅ Applied {len(all_changes)} corrections "
           f"(layer1 local: {len(layer1)}, layer2 LLM: {len(llm_changes)}). "
           f"Backups: *.json.bak / *.xlsx.bak[/green]")
    _write_report(words, all_changes)


def _write_report(words, changes):
    os.makedirs("output/log", exist_ok=True)
    report = {
        "total_words": len(words),
        "total_changes": len(changes),
        "changes": changes,
    }
    with open(_CORRECTION_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    rprint(f"[green]📋 Correction report saved to {_CORRECTION_REPORT}[/green]")


if __name__ == "__main__":
    correct_transcription()
