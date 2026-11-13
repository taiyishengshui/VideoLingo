"""ASR word-correction engine v2: sentence-level LLM review (trial).

Design evolution (discussed with user, 2026-09-04):
- The word-level ±5-context review was rejected: same-domain near-words
  ("metaphor" vs "metaprogram") can BOTH be legitimate in a Bandler seminar —
  only a full-sentence semantic read can judge. score < 0.5 is an alignment
  metric, not a correctness verdict, so it works as a SUSPICION MARKER, not
  evidence.
- L1a (local, auto): frequency-based entity unification (conservative:
  majority ≥3, gap ≥2, similarity ≥0.85). Kept automatic — spelling drift of
  the SAME entity is safe to normalize by frequency.
- L1b (local, flag-only): domain-hotword fuzzy match is DEMOTED to a
  suggestion. It no longer rewrites anything — it marks words for the
  LLM to arbitrate (this is the metaphor/metaprogram trap fix).
- L2 (LLM, sentence level): the word stream is re-grouped into sentences via
  whisper's punctuation tails (zero cost, independent of 3-1/3-2 pipeline).
  Only sentences containing ≥1 suspect (low score or local flag) are sent,
  packed ≤500 words per call. The LLM sees the full sentence + flagged
  suspects + previous sentence + domain vocabulary, and fixes only words
  whose replacement fits the sentence.
- L3 (write-back): index-anchored mutation; multi-word joins merge into the
  first slot; row count and ALL timestamps unchanged; xlsx re-exported.

Safety (unchanged in spirit): idempotent via report file; .bak backups before
first mutation; every LLM fix must echo a flagged suspect of the SAME
sentence (index anchor); per-call fix-rate guard discards that call's fixes.

Crash-resume (file-based, 2026-09-04): progress is checkpointed to
output/log/asr_correct_ckpt.json after EVERY LLM call (atomic tmp+rename,
fingerprinted against the word store). Killing the process, closing the
terminal or powering off loses at most the one in-flight call — restart
`python -m core.asr_correct` and it continues after the last saved batch.
The checkpoint is deleted when the run completes. ask_gpt's prompt cache
remains as a second, independent safety net.

Failure ledger + auto-sweep (2026-09-08): a batch that exhausts ask_gpt's
retry ladder (transient API outage, e.g. Cloudflare 504s) is recorded to
ckpt.failed_batches[] instead of being silently skipped. After the main
scan, the failed batches are retried automatically in up to 3 sweep rounds
with growing cooldowns (120s/180s/300s, honoring Cloudflare's retry_after).
Successes are checkpointed and cleared from the ledger; leftovers stay in
the ledger (never marked done) so any future run retries them for free, and
they are listed under 'unresolved_batches' in the report.

Concurrency (2026-09-04): _MAX_WORKERS parallel LLM calls via threads
(IO-bound wait, not CPU); batch identity is schedule-independent, so
concurrent completion is resume-safe. Concurrency level is deliberately NOT
part of the cfg fingerprint — changing it never invalidates a checkpoint.

Trial commands:
    python -m core.asr_correct stats    # counts only, ZERO API calls
    python -m core.asr_correct          # full run (auto-resumes if ckpt exists)
    python -m core.asr_correct force    # re-run even if report exists
"""

import os
import re
import json
import sys
import shutil
import difflib
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import Counter

from core.utils import rprint, load_key
from core.utils.models import _2_CLEANED_CHUNKS
from core.utils.words_store import (
    _2_CLEANED_CHUNKS_JSON, load_words_json, save_words_json, export_words_xlsx,
)

_HINTS_FILE = "output/log/asr_checkpoint/asr_hints.json"
_CORRECTION_REPORT = "output/log/asr_correction_report.json"
# session log: append-only plain-text mirror of the console progress, so a
# long silent stretch is always auditable after the fact
_SESSION_LOG = "output/log/asr_correct_session.log"
# crash-resume checkpoint: written after EVERY LLM call (atomic), validated
# by word-store fingerprint on load
_CKPT_FILE = "output/log/asr_correct_ckpt.json"

# --- tunables for the trial; adjust freely ----------------------------------
_SCORE_THRESHOLD = 0.5      # word alignment score below this = suspect
_PACK_WORDS = 500           # target words packed per LLM call
_MAX_SENTENCE_WORDS = 60    # hard cap per pseudo-sentence (whisper caps anyway)
_FIX_RATE_GUARD = 0.25      # >25% of suspects fixed in one call → suspicious
_PREV_CONTEXT = True        # include previous sentence as context
_MAX_WORKERS = 3            # concurrent LLM calls (IO-bound: waiting on the
                            # API dominates, so threads overlap the waiting)
_SWEEP_COOLDOWNS = (120, 180, 300)  # seconds before auto-sweep round 1/2/3
_SWEEP_MAX_ROUNDS = 3       # max auto-retry rounds for ledger-recorded failures


# --- Layer 1a: local entity unification (auto, conservative) ----------------

def _word_key(word: str) -> str:
    """Comparable key: lowercase, punctuation stripped (apostrophes kept)."""
    return "".join(c for c in word.lower() if c.isalnum() or c == "'")


def _unify_entities(words, min_count_gap=2):
    """Same-entity spelling drift: group by (first letter, edit distance ≤1);
    minority spellings fold into the majority spelling."""
    counts = Counter(w["text"] for w in words)
    changes = []
    total = len(words)

    # vocab pre-filter: only entries whose normalized key is ≥4 chars. The
    # raw word stream occasionally carries punctuation-only entries (".",
    # ",") — their normalized key is the EMPTY string, and taking ok[0][0]
    # on it crashed (IndexError). They could never pass the outer ≥4 gate
    # anyway, so excluding them from the vocab is behavior-neutral and
    # skips pointless comparisons (perf bonus on a 109k-word store).
    vocab = [v for v in sorted(counts) if len(_word_key(v)) >= 4]

    # Precompute normalized keys ONCE and bucket by first letter: the inner
    # loop used to call _word_key(other) for every (word × vocab) pair —
    # 109k × 7.8k = ~860M char-level calls, tens of minutes in Python.
    # Bucketing makes each word only scan same-first-letter candidates
    # (~1/26th of vocab); total runtime drops to well under a minute.
    from collections import defaultdict
    by_first = defaultdict(list)
    for v in vocab:
        k = _word_key(v)
        by_first[k[0]].append((k, v, counts[v]))

    key_cache = {}
    next_progress = 5000  # progress print interval
    for i, w in enumerate(words):
        if i >= next_progress:
            rprint(f"[dim]   L1a entity scan: {i}/{total} words, {len(changes)} candidates[/dim]")
            next_progress += 5000
        t = w["text"]
        key = key_cache.get(t)
        if key is None:
            key = _word_key(t)
            key_cache[t] = key
        if len(key) < 4:  # too short to judge safely
            continue
        candidates = []
        for ok, other, cnt in by_first.get(key[0], ()):
            if other == t:
                continue
            # GUARD 1 — case/punct variants have IDENTICAL normalized keys;
            # unifying them would lowercase sentence-initial capitals and
            # strip sentence-ending periods (breaks 3-1 sentence splitting).
            if ok == key:
                continue
            # GUARD 2 — plural/singular of the same word (steps/step,
            # images/image): ASR heard the morphological form; that's not
            # spelling drift.
            if ok == key + "s" or key == ok + "s":
                continue
            if abs(len(ok) - len(key)) <= 1:
                ratio = difflib.SequenceMatcher(None, key, ok).ratio()
                if ratio >= 0.85:
                    candidates.append((cnt, other))
        if candidates:
            best_count, best_spelling = max(candidates)
            # majority must clearly dominate the minority AND be common
            if best_count >= counts[t] + min_count_gap and counts[best_spelling] >= 3:
                # GUARD 3 — replacement must PRESERVE casing style and
                # punctuation tail of the word it replaces (sentence-initial
                # capitals stay capitalized; trailing periods stay).
                new_text = _align_word_form(t, best_spelling)
                if new_text is None:
                    continue
                changes.append({
                    "index": i, "old": t, "new": new_text,
                    "reason": f"entity unification: '{t}'x{counts[t]} → '{best_spelling}'x{best_count}",
                    "layer": 1,
                })
    return changes


def _align_word_form(original, replacement):
    """Adapt the replacement's casing/punctuation to the slot's existing form:
    sentence-initial or all-caps originals keep their style; trailing periods
    are preserved so sentence boundaries survive. Returns None if the form
    cannot be mapped safely (then the change is dropped)."""
    import re as _re
    tail = ""
    m = _re.search(r"[.!?,:;]+$", original)
    if m:
        tail = m.group(0)
    core_o = original[:len(original) - len(tail)] if tail else original
    core_r = replacement.rstrip(string_punct)
    if core_r != core_r.strip():
        return None
    if core_o.isupper() and len(core_o) > 1:      # ALLCAPS → ALLCAPS
        new = core_r.upper()
    elif core_o[:1].isupper() and core_r[:1].islower():  # Capitalized → capitalize
        new = core_r[:1].upper() + core_r[1:]
    elif core_o[:1].islower() and core_r[:1].isupper():  # lowercase → lowercase
        new = core_r[:1].lower() + core_r[1:]
    else:
        new = core_r
    return new + tail


string_punct = " .!?,:;\"'()[]-"


# --- Layer 1b: hotword fuzzy match (flag-only, NEVER rewrites) --------------
# Why demoted: "metaphor" and "metaprogram" are both real words in this
# domain; a local similarity score cannot tell which one the speaker said.
# It can only raise its hand; the sentence-level LLM arbitrates.

def _hotword_suggestions(words, hints):
    """Flag words/windows that fuzzy-resemble a domain hotword. Returns
    suggestions {index, span, matched, hotword, sim} — no text changes."""
    hotwords = [h.strip() for h in (hints.get("hotwords") or "").split(",") if h.strip()]
    if not hotwords:
        return []
    hw_keys = {}
    for h in hotwords:
        parts = [_word_key(p) for p in h.split()]
        if parts and all(len(p) >= 2 for p in parts):
            hw_keys[h] = parts

    suggestions = []
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
                if (n > 1 and 0.7 <= sim and joined != target_joined) or \
                   (n == 1 and 0.75 <= sim < 1.0 and len(window[0]) >= 4):
                    suggestions.append({
                        "index": i, "span": n,
                        "matched": " ".join(words[j]["text"] for j in range(i, i + n)),
                        "hotword": hw, "sim": round(sim, 2),
                    })
    return suggestions


# --- Sentence builder (punctuation-based, free) ------------------------------

_SENT_END = re.compile(r"[.!?][\"')\]]*$")


def _build_sentences(words):
    """Group the word stream into pseudo-sentences by sentence-ending
    punctuation. whisper words carry their punctuation tails, so this needs
    no model. Returns a list of {indices, text} — indices are global word
    store indices, so LLM verdicts map 1:1 back onto the store."""
    sentences = []
    cur = []
    for i, w in enumerate(words):
        text = str(w.get("text") or "")
        if not text.strip():
            continue
        cur.append(i)
        if _SENT_END.search(text) or len(cur) >= _MAX_SENTENCE_WORDS:
            sentences.append({"indices": cur,
                             "text": " ".join(words[j]["text"] for j in cur)})
            cur = []
    if cur:
        sentences.append({"indices": cur,
                         "text": " ".join(words[j]["text"] for j in cur)})
    return sentences


# --- Crash-resume checkpoint (file-based, survives power-off) ----------------
# Design: the packing of eligible sentences into batches is DETERMINISTIC
# (given the same word store + code tunables), so batch identity = (first
# sentence index, sentence count). After every LLM call, the accepted fixes
# plus the batch id are appended to an atomic JSON file stamped with a
# fingerprint of the word store. On restart, the same deterministic packing
# is recomputed, completed batch ids are skipped (fixes reloaded from disk),
# and the LLM is only called for the remaining batches. The ask_gpt prompt
# cache still makes double-calls FREE — this checkpoint removes even the
# ONE in-flight call risk and makes resume explicit/inspectable on disk.

_CKPT_SCHEMA = 1


def _cfg_fingerprint():
    """Fingerprint of the code tunables that determine BATCH PACKING and
    SUSPECT SELECTION. If any of these change between runs, the batch
    sequence changes and (first_k, n) identities are no longer stable —
    so a checkpoint from a differently-tuned run must be discarded."""
    return f"{_SCORE_THRESHOLD}|{_PACK_WORDS}|{_MAX_SENTENCE_WORDS}|{_FIX_RATE_GUARD}|{_PREV_CONTEXT}"


_CKPT_EMPTY = {
    "schema": _CKPT_SCHEMA,
    "fingerprint": None,     # hash of the word store this ckpt belongs to
    "cfg": None,             # tunables fingerprint (batch packing reproducibility)
    "done_batches": [],      # [{first_k, n, len}] batch identities completed
    "failed_batches": [],    # [{first_k, n, attempts, last_error, last_failed}]
    "fixes": [],             # accepted L2 change records, in call order
    "stats": {"skipped": 0, "loaded_fixes": 0},
}


def _store_fingerprint(words):
    """Cheap structural fingerprint of the word store: length + first/last
    word texts. Guards against resuming against a DIFFERENT store (e.g. a
    fresh _2_asr run) which would invalidate all index anchors."""
    if not words:
        return "empty"
    a = str(words[0].get("text", ""))
    b = str(words[-1].get("text", ""))
    return f"{len(words)}:{a}:{b}"


def _ckpt_load(fingerprint, cfg):
    """Load a matching checkpoint, or None if absent/stale/mismatched."""
    if not os.path.exists(_CKPT_FILE):
        return None
    try:
        with open(_CKPT_FILE, "r", encoding="utf-8") as f:
            ckpt = json.load(f)
        if (ckpt.get("schema") != _CKPT_SCHEMA
                or ckpt.get("fingerprint") != fingerprint
                or ckpt.get("cfg") != cfg
                or not isinstance(ckpt.get("done_batches"), list)
                or not isinstance(ckpt.get("failed_batches"), list)
                or not isinstance(ckpt.get("fixes"), list)):
            return None
        return ckpt
    except (json.JSONDecodeError, OSError, KeyError):
        # truncated write (power-off mid-rename is impossible thanks to
        # os.replace, but be defensive anyway): treat as no checkpoint
        return None


def _ckpt_save(ckpt):
    """Atomic write: tmp file + os.replace (all-or-nothing on disk)."""
    tmp = _CKPT_FILE + ".tmp"
    os.makedirs(os.path.dirname(_CKPT_FILE), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ckpt, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _CKPT_FILE)


def _ckpt_delete():
    try:
        os.remove(_CKPT_FILE)
    except OSError:
        pass


def _ckpt_done_set(ckpt):
    """Set of batch identity tuples for O(1) resume lookups."""
    if not ckpt:
        return set()
    return {(b["first_k"], b["n"]) for b in ckpt.get("done_batches", [])}



# --- Layer 2: sentence-level LLM review --------------------------------------

def _llm_review(words, hints, suggestions):
    """Review suspect-bearing sentences in full, with the previous sentence
    as context. Returns a list of change records (index-anchored)."""
    try:
        from core.utils import ask_gpt
    except ImportError:
        return []

    sentences = _build_sentences(words)
    sugg_map = {s["index"]: s for s in suggestions}

    # eligibility: sentence contains ≥1 low-score word or ≥1 hotword flag
    eligible = []
    for k, s in enumerate(sentences):
        suspects = []
        for i in s["indices"]:
            w = words[i]
            score = w.get("score")
            is_low = isinstance(score, (int, float)) and score < _SCORE_THRESHOLD \
                     and len(_word_key(str(w["text"]))) >= 2
            if i in sugg_map:
                suspects.append({
                    "index": i, "word": w["text"], "score": score,
                    "local_hint": f"resembles domain term '{sugg_map[i]['hotword']}' (sim {sugg_map[i]['sim']})",
                })
            elif is_low:
                suspects.append({"index": i, "word": w["text"], "score": score})
        if suspects:
            eligible.append((k, s, suspects))

    if not eligible:
        return []

    domain = hints.get("domain", "") if hints else ""
    hotwords = hints.get("hotwords", "") if hints else ""

    changes = []
    stats = {"calls": 0, "reviewed": 0, "accepted": 0, "dropped_batches": 0,
             "resumed_skipped": 0}
    total_calls_est = max(1, (sum(len(s["indices"]) for _, s, _ in eligible) + _PACK_WORDS - 1) // _PACK_WORDS)
    import time as _time
    t_llm_start = _time.time()

    # crash-resume: load checkpoint from a previous interrupted session
    _ckpt = _ckpt_load(_store_fingerprint(words), _cfg_fingerprint())
    if _ckpt is None:
        import copy as _copy
        _ckpt = _copy.deepcopy(_CKPT_EMPTY)
        _ckpt["fingerprint"] = _store_fingerprint(words)
        _ckpt["cfg"] = _cfg_fingerprint()
    _done = _ckpt_done_set(_ckpt)
    if _ckpt["fixes"]:
        # reload accepted fixes from disk; they bypass the LLM entirely
        changes.extend(_ckpt["fixes"])
        stats["accepted"] += len(_ckpt["fixes"])
        _ckpt["stats"]["loaded_fixes"] = len(_ckpt["fixes"])
        rprint(f"[cyan]♻️ Resuming interrupted run: {len(_ckpt['done_batches'])} batch(es) "
               f"already done, {len(_ckpt['fixes'])} fix(es) reloaded from disk (zero API cost).[/cyan]")

    def _ckpt_exists(batch_id):
        return batch_id in _done

    _ckpt_lock = threading.Lock()
    _stats_lock = threading.Lock()

    def _ckpt_record_failure(batch_id, error):
        """Ledger entry for a batch that exhausted ask_gpt's retry ladder.
        Atomic write keeps evidence across crashes; the batch is retried
        automatically by the sweep below (and by any future run, since it is
        NOT marked done). Deduplicates by batch identity."""
        with _ckpt_lock:
            if batch_id in _done:
                return  # raced success — nothing to record
            failed = _ckpt.setdefault("failed_batches", [])
            entry = None
            for f in failed:
                if (f["first_k"], f["n"]) == batch_id:
                    entry = f
                    break
            if entry is None:
                entry = {"first_k": batch_id[0], "n": batch_id[1], "attempts": 0,
                         "first_failed": _time.strftime("%H:%M:%S")}
                failed.append(entry)
            entry["attempts"] += 1
            entry["last_error"] = str(error)[:200]
            entry["last_failed"] = _time.strftime("%H:%M:%S")
            _ckpt_save(_ckpt)

    def _ckpt_save_batch(batch_id, batch_changes):
        """Persist one completed batch atomically. batch_id uniqueness is
        guaranteed by the deterministic packing (first sentence index).
        Thread-safe: concurrent workers serialize their writes through the
        lock, and each batch is appended+saved whole under it."""
        with _ckpt_lock:
            if batch_id in _done:
                return
            _done.add(batch_id)
            _ckpt["fixes"].extend(batch_changes)
            _ckpt["done_batches"].append(
                {"first_k": batch_id[0], "n": batch_id[1], "len": len(batch_changes)})
            # a successful batch clears its own earlier failure records
            if _ckpt.get("failed_batches"):
                _ckpt["failed_batches"] = [
                    f for f in _ckpt["failed_batches"]
                    if (f["first_k"], f["n"]) != batch_id]
            _ckpt["stats"]["skipped"] = stats["resumed_skipped"]
            _ckpt_save(_ckpt)

    def _log(msg):
        """Progress to terminal AND session log file (so a long silent run
        is auditable after the fact)."""
        line = f"[{_time.strftime('%H:%M:%S')}] {msg}"
        rprint(line)
        try:
            with open(_SESSION_LOG, "a", encoding="utf-8") as f:
                f.write(_re_sub_rich(msg) + "\n")
        except OSError:
            pass

    import re as _re_mod
    def _re_sub_rich(s):
        # strip rich markup tags like [cyan]...[/cyan] for the plain log
        return _re_mod.sub(r"\[/?[a-zA-Z0-9_ #=]+\]", "", s)

    def _flush(batch_items):
        """One LLM call. Collects fixes locally; the fix-rate guard discards
        the whole batch (never partially) if it smells like hallucination.
        Thread-safe: called concurrently by the worker pool."""
        if not batch_items:
            return
        # resume: skip batches already processed in a previous (interrupted)
        # session. Batch identity = first sentence index + sentence count.
        batch_id = (batch_items[0]["k"], len(batch_items))
        if _ckpt_exists(batch_id):
            with _stats_lock:
                stats["calls"] += 1  # keep global counters honest
                stats["resumed_skipped"] += 1
            return
        with _stats_lock:
            stats["calls"] += 1
            call_no = stats["calls"]
            all_suspects = sum(len(item["suspects"]) for item in batch_items)
            stats["reviewed"] += all_suspects
        sentences_in_batch = len(batch_items)
        _log(f"[cyan]📞 LLM call {call_no}/{total_calls_est}: "
             f"{sentences_in_batch} sentences, {all_suspects} suspects...[/cyan]")

        items = []
        for item in batch_items:
            sus_idx = {s["index"] for s in item["suspects"]}
            marked = " ".join(
                (f"<{words[j]['text']}>" if j in sus_idx else words[j]["text"])
                for j in item["sentence"]["indices"]
            )
            items.append({
                "sentence": item["k"],
                "previous_sentence": (item.get("prev") or "")[:300],
                "text": marked,
                "suspects": item["suspects"],
            })

        prompt = (
            "You fix speech-recognition (ASR) errors in an English transcript.\n\n"
            f"Audio domain: {domain}\n"
            f"Domain vocabulary (authoritative spellings — but NEVER force them "
            f"where they don't fit the sentence): {hotwords}\n\n"
            "Each item below is ONE sentence, plus the previous sentence for "
            "context. Words wrapped in <angle brackets> are flagged as "
            "low-confidence or as resembling a domain term. The suspects list "
            "gives each flagged word's global index.\n\n"
            "For each sentence, decide which flagged words are actual ASR "
            "errors, and give the replacement that FITS THE SENTENCE MEANING. "
            "A flagged word that makes sense in context is NOT an error — keep "
            "it (short function words like 'are/it/does' often align poorly "
            "yet are correct; a common word can be right even when a domain "
            "term resembles it). Fix only word FORM (misrecognition, "
            "mis-split, misspelling). Never rewrite grammar, tone or meaning.\n\n"
            "Rules:\n"
            '- "old" must be copied EXACTLY from the flagged word\n'
            '- "index" must be that suspect\'s global index from the same '
            "sentence's suspects list\n"
            "- you may join a short word sequence into one word if that is "
            "what was said (e.g. 'meta program' → 'metaprogram'); then old is "
            "the full sequence and index is its first word's index\n"
            "- when in doubt, keep\n\n"
            "Items:\n" + json.dumps(items, ensure_ascii=False) + "\n\n"
            'Respond with a JSON object: {"fixes": [{"sentence": <int>, '
            '"index": <int>, "old": "...", "new": "...", "reason": "..."}]}. '
            "Return an empty fixes array if nothing is certain."
        )

        def _valid(resp):
            if not isinstance(resp, dict) or "fixes" not in resp:
                return {"status": "error", "message": "missing fixes"}
            for f in resp["fixes"]:
                if not all(kk in f for kk in ("sentence", "index", "old", "new")):
                    return {"status": "error", "message": "fix missing keys"}
            return {"status": "success", "message": ""}

        try:
            resp = ask_gpt(prompt, resp_type="json", valid_def=_valid,
                           log_title="asr_sentence_fix")
        except Exception as e:
            # exhausted the whole retry ladder (e.g. API 504s): do NOT skip
            # silently — record to the failure ledger; the auto-sweep after
            # the main scan retries this batch, and any future run picks it
            # up too (it is never marked done)
            _ckpt_record_failure(batch_id, e)
            _log(f"[yellow]⚠️ call {call_no} failed after retries — recorded "
                 f"in failure ledger for auto-sweep: {str(e)[:160]}[/yellow]")
            return

        by_sentence = {item["k"]: item for item in batch_items}
        batch_changes = []
        for f in resp.get("fixes", []):
            item = by_sentence.get(f.get("sentence"))
            if item is None:
                continue  # hallucinated sentence reference
            suspect_idx = {s["index"] for s in item["suspects"]}
            i = f.get("index")
            if i not in suspect_idx:
                continue  # index must be a flagged suspect of THAT sentence
            old, new = str(f.get("old", "")), str(f.get("new", ""))
            if not new or new == old:
                continue
            first_old = old.split()[0] if old.split() else ""
            if _word_key(first_old) != _word_key(str(words[i]["text"])):
                continue  # echo anchor failed
            batch_changes.append({
                "index": i, "span": max(1, len(old.split())),
                "old": old, "new": new,
                "reason": f.get("reason", "llm sentence review"),
                "layer": 2,
            })

        # fix-rate guard: hallucinated 'fixes' cluster in batches
        if batch_changes and len(batch_changes) > _FIX_RATE_GUARD * max(1, all_suspects):
            _log(f"[red]❌ Fix rate too high ({len(batch_changes)}/{all_suspects} "
                 f"suspects in call #{call_no}) — dropping this call's "
                 f"fixes as likely hallucination.[/red]")
            with _stats_lock:
                stats["dropped_batches"] += 1
            # batch is FINAL (dropped, not retried) → mark done in ckpt with 0 fixes
            _ckpt_save_batch(batch_id, [])
            return  # batch_changes discarded wholesale

        with _stats_lock:
            changes.extend(batch_changes)
            stats["accepted"] += len(batch_changes)
        # durable checkpoint AFTER every survived batch: flush accepted fixes
        # to disk so a crash/power-off loses at most the in-flight call
        _ckpt_save_batch(batch_id, batch_changes)
        if batch_changes:
            sample = "; ".join(f"'{c['old']}'→'{c['new']}'" for c in batch_changes[:3])
            _log(f"[green]✓ call {call_no}: {len(batch_changes)} fix(es): {sample}[/green]")
        else:
            _log(f"[dim]call {call_no}: no certain fixes[/dim]")

    # pack eligible sentences into batches by word budget (deterministic —
    # independent of worker scheduling, so resume batch ids stay stable)
    batches = []
    batch, batch_words = [], 0
    for k, s, suspects in eligible:
        prev = sentences[k - 1]["text"] if (_PREV_CONTEXT and k > 0) else ""
        if batch and batch_words + len(s["indices"]) > _PACK_WORDS:
            batches.append(batch)
            batch, batch_words = [], 0
        batch.append({"k": k, "sentence": s, "suspects": suspects, "prev": prev})
        batch_words += len(s["indices"])
    if batch:
        batches.append(batch)

    # fan out over concurrent workers: each batch is an independent IO-bound
    # LLM call. Batch identity (first sentence index, count) does not depend
    # on completion order, so concurrent completion is resume-safe.
    n_workers = max(1, min(_MAX_WORKERS, len(batches) or 1))
    if n_workers > 1:
        rprint(f"[cyan]⚡ Concurrency: {n_workers} parallel LLM calls "
               f"({len(batches)} batches to run).[/cyan]")
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            list(ex.map(_flush, batches))
    else:
        for b in batches:
            _flush(b)

    # --- auto-sweep: retry ledger-recorded failures after cooldowns ----------
    # A batch only lands in the ledger after ask_gpt's full retry ladder
    # failed — that signals a transient API outage (e.g. Cloudflare 504s
    # advising retry_after: 120). Retrying immediately would likely fail
    # again, so the main scan finishes first, then failed batches get up to
    # _SWEEP_MAX_ROUNDS rounds with growing cooldowns. Successful retries
    # flow through _flush → checkpointed and removed from the ledger;
    # anything still failing afterwards stays in the ledger (never marked
    # done), so the next run's resume retries it automatically, for free.
    for sweep in range(1, _SWEEP_MAX_ROUNDS + 1):
        failed_ids = {(f["first_k"], f["n"]) for f in _ckpt.get("failed_batches", [])}
        if not failed_ids:
            break
        delay = _SWEEP_COOLDOWNS[min(sweep - 1, len(_SWEEP_COOLDOWNS) - 1)]
        _log(f"[yellow]⏳ {len(failed_ids)} batch(es) failed during the main "
             f"scan — auto-sweep round {sweep}/{_SWEEP_MAX_ROUNDS} starts in "
             f"{delay}s (letting the API recover).[/yellow]")
        _time.sleep(delay)
        redo = [b for b in batches if (b[0]["k"], len(b)) in failed_ids]
        if not redo:
            _log(f"[red]⚠️ Failed batches no longer rebuildable from current "
                 f"packing (store changed?) — ledger kept for manual "
                 f"inspection.[/red]")
            break
        _log(f"[cyan]🔄 Sweep {sweep}/{_SWEEP_MAX_ROUNDS}: retrying "
             f"{len(redo)} batch(es) with {n_workers} workers...[/cyan]")
        if n_workers > 1:
            with ThreadPoolExecutor(max_workers=n_workers) as ex:
                list(ex.map(_flush, redo))
        else:
            for b in redo:
                _flush(b)

    leftover = _ckpt.get("failed_batches", [])
    if leftover:
        stats["unresolved"] = len(leftover)
        _log(f"[yellow]⚠️ {len(leftover)} batch(es) still unresolved after "
             f"{_SWEEP_MAX_ROUNDS} sweep round(s). Their ledger entries "
             f"survive in {_CKPT_FILE}; any future run of "
             f"`python -m core.asr_correct` retries them automatically "
             f"(never marked done). No cost lost: only failed batches are "
             f"ever re-sent.[/yellow]")

    elapsed = _time.time() - t_llm_start
    _log(f"[cyan]📞 sentence review done: {stats['calls']} calls | suspects reviewed: "
         f"{stats['reviewed']} | fixes accepted: {stats['accepted']}"
         + (f" | guarded batches dropped: {stats['dropped_batches']}"
            if stats['dropped_batches'] else "")
         + (f" | unresolved batches: {stats['unresolved']}"
            if stats.get('unresolved') else "")
         + f" | elapsed {elapsed:.0f}s[/cyan]")
    return changes


# --- Layer 3: write-back ------------------------------------------------------

def apply_corrections(words, changes):
    """Mutate words in place via (index) anchors. Multi-word span fixes
    (e.g. 'meta program'→'metaprogram') merge into the first slot and blank
    the rest — row count and ALL timecodes stay identical."""
    changes = sorted(changes, key=lambda c: c["index"])
    for c in changes:
        i = c["index"]
        if i >= len(words):
            continue
        # verify anchor still matches
        if _word_key(words[i]["text"]) != _word_key(str(c["old"]).split()[0]):
            continue
        span = c.get("span", 1)
        words[i]["text"] = c["new"]
        for j in range(i + 1, min(i + span, len(words))):
            words[j]["text"] = ""  # consumed by the joined word
    return words


# --- Helpers ------------------------------------------------------------------

def _load_hints():
    if not os.path.exists(_HINTS_FILE):
        return None
    try:
        with open(_HINTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("hints")
    except (json.JSONDecodeError, OSError):
        return None


def _sentence_stats(words, hints):
    """Shared counting for the stats mode (zero API calls)."""
    sentences = _build_sentences(words)
    suggestions = _hotword_suggestions(words, hints) if hints else []
    sugg_idx = {s["index"] for s in suggestions}

    eligible_sents, total_suspects, eligible_words = 0, 0, 0
    low_only = 0
    for s in sentences:
        sus = 0
        for i in s["indices"]:
            w = words[i]
            if i in sugg_idx:
                sus += 1
            elif isinstance(w.get("score"), (int, float)) and w["score"] < _SCORE_THRESHOLD \
                    and len(_word_key(str(w["text"]))) >= 2:
                sus += 1
                low_only += 1
        if sus:
            eligible_sents += 1
            total_suspects += sus
            eligible_words += len(s["indices"])
    est_calls = max(1, (eligible_words + _PACK_WORDS - 1) // _PACK_WORDS)
    return {
        "sentences": len(sentences),
        "eligible_sents": eligible_sents,
        "suggestions": len(suggestions),
        "low_only": low_only,
        "total_suspects": total_suspects,
        "eligible_words": eligible_words,
        "est_calls": est_calls,
    }


def _dry_stats():
    """Counts only — lets the user see the exact cost before spending."""
    words = load_words_json()
    if words is None:
        rprint("[yellow]⚠️ cleaned_chunks.json not found — run _2_asr first.[/yellow]")
        return
    hints = _load_hints()
    st = _sentence_stats(words, hints)
    rprint(f"[cyan]📊 words: {len(words)} | sentences: {st['sentences']}[/cyan]")
    rprint(f"[cyan]📊 suspect-bearing sentences: {st['eligible_sents']} "
           f"(hotword flags: {st['suggestions']}, low-score suspects: {st['low_only']})[/cyan]")
    rprint(f"[cyan]📊 suspects total: {st['total_suspects']} | "
           f"words inside eligible sentences: ~{st['eligible_words']}[/cyan]")
    rprint(f"[cyan]💰 estimated LLM calls at ≤{_PACK_WORDS} words/call: "
           f"≈{st['est_calls']}[/cyan]")


# --- Main entry ---------------------------------------------------------------

def correct_transcription(force=False):
    """Main entry. Idempotent: a report file marks done unless force=True."""
    import time as _time
    t0 = _time.time()

    def _stage_log(msg):
        elapsed = _time.time() - t0
        line = f"[{_time.strftime('%H:%M:%S')}] {msg} elapsed {elapsed:.0f}s"
        rprint(line)
        try:
            with open(_SESSION_LOG, "a", encoding="utf-8") as f:
                import re as _re
                f.write(_re.sub(r"\[/?[a-zA-Z0-9_ #=]+\]", "", msg) + "\n")
        except OSError:
            pass

    words = load_words_json()
    if words is None:
        rprint("[yellow]⚠️ cleaned_chunks.json not found — run _2_asr first. "
               "(Legacy xlsx-only projects: needs the JSON store.)[/yellow]")
        return

    if os.path.exists(_CORRECTION_REPORT) and not force:
        # A report CAN carry unresolved batches when the store was never
        # mutated (zero-correction runs): in that case the checkpoint is
        # still fingerprint-valid, and a plain re-run finishes the leftover
        # batches instead of skipping. Store-mutated runs retire the ckpt
        # themselves, so the validity check below simply fails for them.
        rpt = _read_report()
        pending = _ckpt_load(_store_fingerprint(words), _cfg_fingerprint())
        if rpt.get("unresolved_batches") and pending and pending.get("failed_batches"):
            rprint(f"[cyan]♻️ Previous run left {len(pending['failed_batches'])} "
                   f"unresolved batch(es) — retrying them now (done batches "
                   f"stay free).[/cyan]")
        else:
            rprint("[cyan]♻️ asr_correct already applied (report exists); skip. "
                   "Use force=True (or `python -m core.asr_correct force`) to re-run.[/cyan]")
            return
    if force:
        # a forced re-run makes any crash-resume state meaningless: the
        # store was already mutated by the previous completed run, so its
        # fingerprint (and every index anchor) is stale anyway
        _ckpt_delete()
        try:
            if os.path.exists(_SESSION_LOG):
                os.remove(_SESSION_LOG)  # force = fresh audit trail too
        except OSError:
            pass

    hints = _load_hints()
    # session log: APPEND mode — resumed runs keep the previous session's
    # history (a crash-resume run must not erase its own audit trail)
    try:
        os.makedirs("output/log", exist_ok=True)
        with open(_SESSION_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n=== asr_correct session {_time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"force={force} words={len(words)} "
                    f"resume={'yes' if os.path.exists(_CKPT_FILE) else 'no'} ===\n")
    except OSError:
        pass

    _stage_log(f"[cyan]🔧 Correcting transcription (sentence-level review v2): "
               f"{len(words)} words, domain hints {'available' if hints else 'absent'}.[/cyan]")

    # L1a: local entity unification (auto-applied, conservative)
    changes = _unify_entities(words)
    _stage_log(f"[cyan]L1a entity unification done: {len(changes)} changes[/cyan]")
    # L1b: hotword fuzzy flags (advisory only — the LLM arbitrates)
    suggestions = _hotword_suggestions(words, hints) if hints else []
    if suggestions:
        _stage_log(f"[cyan]🚩 {len(suggestions)} hotword-resemblance flags handed to "
                   f"the LLM for arbitration (NO local rewrite).[/cyan]")

    # L2: sentence-level LLM review (skips words L1a already fixed)
    fixed_idx = {c["index"] for c in changes}
    llm_changes = _llm_review(words, hints, suggestions)
    llm_changes = [c for c in llm_changes if c["index"] not in fixed_idx]
    all_changes = changes + llm_changes

    if not all_changes:
        _stage_log("[green]✅ No corrections needed.[/green]")
        unresolved = _read_ledger_failures()
        if unresolved:
            # narrow edge: zero corrections yet some batches never ran —
            # the store stays untouched BUT the ledger must survive, or
            # those failures would be lost forever
            _stage_log(f"[yellow]⚠️ {len(unresolved)} batch(es) unresolved — "
                       f"keeping the checkpoint so a future run retries "
                       f"them for free.[/yellow]")
            _write_report(words, [], suggestions, unresolved=unresolved)
            return
        _write_report(words, [], suggestions)
        _ckpt_delete()  # run is terminal — nothing left to resume
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

    _stage_log(f"[green]✅ Applied {len(all_changes)} corrections "
               f"(L1 entity: {len(changes)}, L2 sentence: {len(llm_changes)}). "
               f"Backups: *.json.bak / *.xlsx.bak[/green]")
    unresolved = _read_ledger_failures()
    if unresolved:
        # store is ALREADY mutated here: sentence indices may have shifted
        # (L2 joins blank consumed slots), so the ledger's batch identities
        # no longer map onto the new store — the checkpoint is retired below
        # and the report is the durable record instead
        _stage_log(f"[yellow]⚠️ {len(unresolved)} batch(es) unresolved — "
                   f"listed under 'unresolved_batches' in the report. The "
                   f"store was already mutated, so a full `force` re-run is "
                   f"the way to re-review those sentences.[/yellow]")
    _write_report(words, all_changes, suggestions, unresolved=unresolved)
    # run succeeded end-to-end: the checkpoint has served its purpose
    _ckpt_delete()


def _write_report(words, changes, suggestions=None, unresolved=None):
    os.makedirs("output/log", exist_ok=True)
    report = {
        "mode": "sentence-level-v2",
        "total_words": len(words),
        "total_changes": len(changes),
        "hotword_flags": len(suggestions) if suggestions else 0,
        "changes": changes,
    }
    if unresolved:
        report["unresolved_batches"] = unresolved
    with open(_CORRECTION_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    rprint(f"[green]📋 Correction report saved to {_CORRECTION_REPORT}[/green]")


def _read_ledger_failures():
    """Failed-batch ledger entries from the checkpoint (post-sweep leftovers).
    [] when nothing failed or the file is unreadable."""
    try:
        with open(_CKPT_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("failed_batches", [])
    except (OSError, json.JSONDecodeError):
        return []


def _read_report():
    try:
        with open(_CORRECTION_REPORT, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


if __name__ == "__main__":
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if arg in ("stats", "--stats", "stat"):
        _dry_stats()
    elif arg == "force":
        correct_transcription(force=True)
    else:
        correct_transcription()
