"""Word-level transcription store: JSON is the primary format, xlsx the
human-friendly export.

Why a JSON primary store (design doc):
- save_results used to wrap every word in literal double quotes to survive
  Excel, and every downstream consumer had to manually strip them back
  (split_by_mark / _4_2_translate / _6_gen_sub). JSON has quote semantics,
  so the workaround disappears.
- Timecodes are millisecond floats; xlsx numeric round-trips can silently
  alter them, breaking the character-exact matching in _6_gen_sub. JSON
  round-trips losslessly.
- Corrections (asr_correct) mutate single words via (index, text) anchors;
  JSON read-modify-write preserves field order and types exactly.

Layout:
    output/log/cleaned_chunks.json  (primary, machine)
    output/log/cleaned_chunks.xlsx  (export, humans; format identical to the
                                    legacy file so old tools still work)

Schema: {"version": 1, "words": [{"text", "start", "end", "speaker_id"?}]}
"""

import json
import os

_2_CLEANED_CHUNKS_JSON = "output/log/cleaned_chunks.json"

SCHEMA_VERSION = 1


def save_words_json(words, path=_2_CLEANED_CHUNKS_JSON):
    """words: list of dicts with text/start/end (+optional speaker_id/score).
    Atomic write: tmp + rename, so a crash never leaves a half file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": SCHEMA_VERSION, "words": words},
                  f, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load_words_json(path=_2_CLEANED_CHUNKS_JSON):
    """Returns list[dict] or None if missing/corrupt."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        words = data.get("words") if isinstance(data, dict) else data
        if isinstance(words, list):
            return words
    except (json.JSONDecodeError, OSError):
        pass
    return None


def export_words_xlsx(words, xlsx_path, df=None):
    """Write the human-facing xlsx export from JSON words.

    Kept byte-compatible with the legacy save_results output: the text column
    carries the same double-quote wrapping, the same column order (text,
    start, end, speaker_id), skip-empty-text rows — so any external tooling
    (or muscle memory in Excel) built against the old file keeps working.
    """
    import pandas as pd
    rows = []
    for w in words:
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        row = {
            "text": f'"{text}"',
            "start": w.get("start"),
            "end": w.get("end"),
        }
        if "speaker_id" in w and w["speaker_id"] is not None:
            row["speaker_id"] = w["speaker_id"]
        rows.append(row)
    df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(xlsx_path), exist_ok=True)
    df.to_excel(xlsx_path, index=False)
    return xlsx_path


def load_words(xlsx_path=None, json_path=_2_CLEANED_CHUNKS_JSON):
    """Unified accessor for all consumers.

    Priority: JSON (if present) → xlsx fallback (legacy projects).
    Returns a pandas DataFrame with columns text/start/end(/speaker_id),
    text unquoted and stripped — ready for processing, no manual quote
    stripping at call sites anymore.
    """
    import pandas as pd

    words = load_words_json(json_path)
    if words is not None:
        rows = []
        for w in words:
            text = str(w.get("text", "")).strip()
            if not text:
                continue
            row = {"text": text, "start": w.get("start"), "end": w.get("end")}
            if "speaker_id" in w and w["speaker_id"] is not None:
                row["speaker_id"] = w["speaker_id"]
            rows.append(row)
        return pd.DataFrame(rows)

    # legacy fallback: read xlsx exactly the way consumers did before
    if xlsx_path and os.path.exists(xlsx_path):
        df = pd.read_excel(xlsx_path)
        if "text" in df.columns:
            df["text"] = df["text"].astype(str).str.strip('"').str.strip()
        return df

    return pd.DataFrame(columns=["text", "start", "end"])
