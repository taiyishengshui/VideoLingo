"""FR-44 read-only ASR/ForcedAligner layer diagnostic.

Run in the qw environment:
    D:/app/miniconda3/envs/qw/python.exe check_asr_layers.py

No pipeline imports or writes to output/. JSON is written under the system
 temporary directory unless --out is explicitly provided.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNNER_PATH = ROOT / "core" / "asr_backend" / "qwen3_asr_runner.py"
DEFAULT_AUDIO = ROOT / "output" / "audio" / "raw.mp3"
SAMPLE_RATE = 16000
WINDOWS = {
    "production": (268.5, 270.0),
    "diagnostic": (440.0, 80.0),
}


def normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def extract_words(items) -> list[dict]:
    """Convert ForcedAlignResult/list variants into relative word timestamps."""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)):
        candidate = getattr(items, "items", None)
        if candidate is not None and not callable(candidate):
            items = candidate
        else:
            try:
                items = list(items)
            except TypeError:
                items = [items]
    words = []
    for item in items:
        def field(*names):
            for name in names:
                if isinstance(item, dict) and name in item:
                    return item[name]
                value = getattr(item, name, None)
                if value is not None:
                    return value
            return None

        word = field("text", "word", "char")
        start = field("start_time", "start")
        end = field("end_time", "end")
        if word is None or start is None or end is None:
            continue
        words.append({"word": str(word), "start": float(start), "end": float(end)})
    return words


def serialize_words(words: list[dict], window_start: float) -> list[dict]:
    return [{"word": w["word"], "start": round(float(w["start"]), 3),
             "end": round(float(w["end"]), 3),
             "absolute_start": round(window_start + float(w["start"]), 3),
             "absolute_end": round(window_start + float(w["end"]), 3)}
            for w in words]


def locate_terms(words: list[dict], window_start: float) -> dict:
    located = {}
    for term in ("That's", "Probably"):
        match = next((w for w in words if normalize_text(w["word"]) == normalize_text(term)), None)
        located[term] = round(window_start + float(match["start"]), 3) if match else None
    return located


def compare_texts(asr_text: str, combined_text: str) -> dict:
    same = normalize_text(asr_text) == normalize_text(combined_text)
    return {"normalized_equal": same,
            "attribution_note": ("same normalized transcript; timing differences can be compared"
                                 if same else "transcripts differ; timing differences cannot be attributed to alignment alone")}


def _as_batch(value) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], (int, float)):
        return [value]
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _audio_summary(audio) -> dict:
    try:
        samples, sample_rate = audio
        sample_count = len(samples)
        raw = samples.tobytes() if hasattr(samples, "tobytes") else bytes(samples)
        return {"sample_rate": int(sample_rate), "sample_count": sample_count,
                "duration": round(sample_count / float(sample_rate), 3),
                "sha256": hashlib.sha256(raw).hexdigest(), "available": True}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _timestamp_summary(values) -> dict:
    try:
        if hasattr(values, "tolist"):
            values = values.tolist()
        numbers = [float(value) for value in values]
        return {"count": len(numbers), "first": numbers[:8], "last": numbers[-8:],
                "available": True}
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _field_summary(value, names: tuple[str, ...]) -> dict:
    for name in names:
        if isinstance(value, dict) and name in value:
            return _timestamp_summary(value[name])
        candidate = getattr(value, name, None)
        if candidate is not None:
            return _timestamp_summary(candidate)
    return {"available": False, "reason": "field unavailable"}


def _result_summary(result) -> dict:
    items = getattr(result, "items", None)
    if items is None:
        return {"available": False, "reason": "alignment items unavailable"}
    words = extract_words(items)
    return {"available": True, "word_count": len(words),
            "key_terms": locate_terms(words, 0.0)}


def alignment_input_match(direct_text: str, direct_language: str, direct_audio: dict,
                          traced: list[dict]) -> dict:
    if len(traced) != 1:
        return {"comparable": False, "reason": "combined alignment input is not a single item"}
    item = traced[0]
    audio = item.get("audio", {})
    checks = {
        "text_exact": item.get("text") == direct_text,
        "language_exact": item.get("language") == direct_language,
        "audio_exact": (audio.get("available") is True and direct_audio.get("available") is True
                        and audio.get("sha256") == direct_audio.get("sha256")
                        and audio.get("sample_count") == direct_audio.get("sample_count")
                        and audio.get("sample_rate") == direct_audio.get("sample_rate")),
    }
    fields_available = all(value is not None for value in (item.get("text"), item.get("language")))
    return {"comparable": fields_available and audio.get("available") is True
            and direct_audio.get("available") is True,
            "checks": checks, "all_equal": all(checks.values())}


@contextmanager
def trace_combined_alignment(model, window_start: float):
    """Temporarily capture actual library-internal align inputs and timestamp fixes."""
    aligner = getattr(model, "forced_aligner", None)
    if aligner is None or not callable(getattr(aligner, "align", None)):
        yield {"available": False, "reason": "forced_aligner.align unavailable", "calls": []}
        return

    calls = []
    timestamp_fixes = []
    original_align = aligner.align
    original_offset = getattr(model, "_offset_align_result", None)
    original_fix = getattr(aligner, "fix_timestamp", None)
    state = {"pending": []}

    def traced_align(*args, **kwargs):
        audio = kwargs.get("audio", args[0] if args else None)
        text = kwargs.get("text", args[1] if len(args) > 1 else None)
        language = kwargs.get("language", args[2] if len(args) > 2 else None)
        audio_batch, text_batch, language_batch = map(_as_batch, (audio, text, language))
        count = max(len(audio_batch), len(text_batch), len(language_batch))
        batch_calls = []
        for index in range(count):
            entry = {
                "batch_index": index,
                "text": text_batch[index] if index < len(text_batch) else None,
                "normalized_text": normalize_text(str(text_batch[index])) if index < len(text_batch) else None,
                "language": language_batch[index] if index < len(language_batch) else None,
                "audio": _audio_summary(audio_batch[index]) if index < len(audio_batch)
                else {"available": False, "reason": "audio item unavailable"},
                "offset_seconds": None,
                "offset_available": False,
                "absolute_start": None,
                "absolute_end": None,
                "result": {"available": False, "reason": "align result pending"},
            }
            calls.append(entry)
            batch_calls.append(entry)
        state["pending"].extend(batch_calls)
        results = original_align(*args, **kwargs)
        result_batch = _as_batch(results)
        for index, entry in enumerate(batch_calls):
            entry["result"] = (_result_summary(result_batch[index]) if index < len(result_batch)
                               else {"available": False, "reason": "result item unavailable"})
        return results

    def traced_offset(result, offset_sec):
        pending = state["pending"]
        if pending:
            entry = pending.pop(0)
            try:
                entry["offset_seconds"] = float(offset_sec)
                entry["offset_available"] = True
                entry["absolute_start"] = round(window_start + float(offset_sec), 3)
                duration = entry["audio"].get("duration")
                if duration is not None:
                    entry["absolute_end"] = round(window_start + float(offset_sec) + duration, 3)
            except (TypeError, ValueError):
                entry["offset_seconds"] = None
                entry["offset_available"] = False
        if original_offset is None:
            return result
        return original_offset(result, offset_sec)

    def traced_fix(values):
        before = _timestamp_summary(values)
        result = original_fix(values)
        after = _timestamp_summary(result)
        timestamp_fixes.append({"before": before, "after": after})
        return result

    aligner.align = traced_align
    if original_offset is not None:
        model._offset_align_result = traced_offset
    if original_fix is not None:
        aligner.fix_timestamp = traced_fix
    trace = {"available": True, "calls": calls, "timestamp_fixes": timestamp_fixes}
    try:
        yield trace
    finally:
        aligner.align = original_align
        if original_offset is not None:
            model._offset_align_result = original_offset
        if original_fix is not None:
            aligner.fix_timestamp = original_fix


def load_runner():
    spec = importlib.util.spec_from_file_location("qwen3_asr_runner_fr44", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runner: {RUNNER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_model(model_dir: str, aligner_dir: str, device: str, dtype_name: str):
    import torch
    from qwen_asr import Qwen3ASRModel

    dtype = getattr(torch, dtype_name)
    return Qwen3ASRModel.from_pretrained(
        model_dir, dtype=dtype, device_map=device,
        max_inference_batch_size=8, max_new_tokens=1024,
        forced_aligner=aligner_dir,
        forced_aligner_kwargs={"dtype": dtype, "device_map": device},
    )


def _path_result(call, window_start: float) -> dict:
    started = time.time()
    try:
        result = call()
        text = str(getattr(result, "text", "") or "")
        stamps = getattr(result, "time_stamps", None)
        if stamps is None and getattr(result, "items", None) is not None:
            stamps = result
        words = extract_words(stamps)
        if not text and words:
            text = " ".join(word["word"] for word in words)
        language = str(getattr(result, "language", "") or "")
        return {"ok": True, "seconds": round(time.time() - started, 1),
                "text": text, "language": language,
                "words": serialize_words(words, window_start),
                "key_terms": locate_terms(words, window_start)}
    except Exception as exc:
        return {"ok": False, "seconds": round(time.time() - started, 1),
                "error": f"{type(exc).__name__}: {exc}", "text": "",
                "language": "", "words": [],
                "key_terms": {"That's": None, "Probably": None}}


def run_window(runner, model, audio_path: str, name: str) -> dict:
    start, length = WINDOWS[name]
    audio = runner.decode_window(audio_path, start, length)
    audio_input = (audio, SAMPLE_RATE)

    pure = _path_result(
        lambda: model.transcribe(audio=audio_input, return_time_stamps=False)[0], start)

    if pure["ok"] and pure["text"].strip():
        direct = _path_result(
            lambda: model.forced_aligner.align(
                audio=audio_input, text=pure["text"],
                language=pure["language"] or "English")[0], start)
    else:
        direct = {"ok": False, "seconds": 0.0,
                  "error": "skipped: pure ASR returned no text", "text": "",
                  "language": "", "words": [],
                  "key_terms": {"That's": None, "Probably": None}}

    with trace_combined_alignment(model, start) as alignment_trace:
        combined = _path_result(
            lambda: model.transcribe(audio=audio_input, return_time_stamps=True)[0], start)
    if direct["ok"]:
        direct_audio = _audio_summary(audio_input)
        direct_text = pure["text"]
        direct_language = pure["language"] or "English"
        alignment_trace["direct_input_comparison"] = alignment_input_match(
            direct_text, direct_language, direct_audio, alignment_trace.get("calls", []))
    else:
        alignment_trace["direct_input_comparison"] = {
            "comparable": False, "reason": "direct alignment unavailable"}
    comparison = compare_texts(pure.get("text", ""), combined.get("text", ""))
    return {"window": {"name": name, "absolute_start": start,
                        "absolute_end": start + length, "duration": length},
            "pure_asr": pure, "direct_forced_aligner": direct,
            "library_combined": combined,
            "library_alignment_trace": alignment_trace,
            "text_comparison": comparison}


def main() -> int:
    parser = argparse.ArgumentParser(description="FR-44 ASR/ForcedAligner layer diagnostic")
    parser.add_argument("--audio", default=str(DEFAULT_AUDIO))
    parser.add_argument("--windows", default="production,diagnostic")
    parser.add_argument("--model", default="D:/models/Qwen3-ASR-1.7B")
    parser.add_argument("--aligner", default="D:/models/Qwen3-ForcedAligner-0.6B")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "asr_layers" / "result.json"))
    args = parser.parse_args()

    names = [item.strip() for item in args.windows.split(",") if item.strip()]
    unknown = [name for name in names if name not in WINDOWS]
    if unknown:
        print(f"未知窗口 {unknown}；可用窗口：{list(WINDOWS)}", file=sys.stderr)
        return 2
    if not Path(args.audio).is_file():
        print(f"音频文件不存在：{args.audio}", file=sys.stderr)
        return 2

    runner = load_runner()
    print(f"[FR-44] loading model on {args.device} ...", file=sys.stderr, flush=True)
    model = load_model(args.model, args.aligner, args.device, args.dtype)
    rows = []
    for name in names:
        print(f"[FR-44] running window {name} ...", file=sys.stderr, flush=True)
        rows.append(run_window(runner, model, args.audio, name))

    report = {"experiment": "FR-44", "paths": ["pure_asr", "direct_forced_aligner", "library_combined"],
              "rows": rows}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"[FR-44] result saved: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
