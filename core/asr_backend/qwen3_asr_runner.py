#!/usr/bin/env python3
"""Qwen3-ASR runner — executed by a SEPARATE conda environment (FR-35).

Must NOT import any VideoLingo module: the host env is pinned to
transformers 4.52.1 (IndexTTS), while qwen-asr requires 4.57.6.

stdout: exactly one JSON object on the last line ({"segments": [...]}).
All diagnostics go to stderr. Window specs and time arithmetic are decided
by core/asr_backend/qwen3_asr.py — this file only executes them.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SAMPLE_RATE = 16000


def _force_utf8() -> None:
    """Windows Chinese locale encodes stdio as GBK; the host decodes UTF-8.

    Without this, Chinese diagnostics become mojibake and the JSON transcript
    on stdout is corrupted. Safe to skip when the stream is not reconfigurable
    (e.g. pytest capture).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (AttributeError, OSError, TypeError, ValueError):
            pass


_force_utf8()


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def decode_window(audio_file: str, offset: float, length: float):
    """Decode ONLY [offset, offset+length) to a 16k mono float32 ndarray."""
    import numpy as np

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{offset:.3f}", "-i", audio_file,
        "-t", f"{length:.3f}", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]
    try:
        raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or b"").decode("utf-8", "ignore")[-400:]
        raise RuntimeError(
            f"ffmpeg 解码失败 [{offset:.1f}s +{length:.1f}s]: {err}"
        ) from exc
    return np.frombuffer(raw, np.int16).flatten().astype(np.float32) / 32768.0


def _field(item, *names):
    """ForcedAligner items may be dataclass-like objects or dicts."""
    for name in names:
        if isinstance(item, dict) and name in item:
            return item[name]
        value = getattr(item, name, None)
        if value is not None:
            return value
    return None


def parse_stamps(result) -> list[dict]:
    """One ASR result -> [{word, start, end}] relative to the window audio.

    qwen-asr 0.0.6 returns a ForcedAlignResult dataclass whose `.items` holds
    ForcedAlignItem(text, start_time, end_time) in SECONDS (align() already
    divided milliseconds by 1000). Older / mocked shapes — list of dicts,
    list of objects, nested lists, plain iterables — stay supported.
    """
    stamps = _field(result, "time_stamps")
    if stamps is None:
        raise RuntimeError(
            "ForcedAligner 未返回 time_stamps：请确认 qwen3_asr.aligner 已配置、"
            "权重已下载，且该语言在 ForcedAligner 支持的 11 语种内"
        )

    items = None
    if not isinstance(stamps, dict):
        candidate = getattr(stamps, "items", None)
        # dict.items is a bound method — never mistake it for the item list.
        if candidate is not None and not callable(candidate):
            items = candidate
    if items is None:
        if isinstance(stamps, (list, tuple)):
            items = stamps[0] if stamps and isinstance(stamps[0], (list, tuple)) else stamps
        elif isinstance(stamps, dict):
            items = [stamps]
        else:
            try:
                items = list(stamps)  # __iter__ (ForcedAlignResult implements it)
            except TypeError:
                items = [stamps]

    words: list[dict] = []
    for item in items:
        text = _field(item, "text", "word", "char")
        start = _field(item, "start_time", "start")
        end = _field(item, "end_time", "end")
        if text is None or start is None or end is None:
            continue
        words.append({"word": str(text), "start": float(start), "end": float(end)})
    if not words:
        count = len(items) if hasattr(items, "__len__") else "?"
        raise RuntimeError(
            "ForcedAligner 时间戳为空或格式不可识别（需 text/start_time/end_time）；"
            f"time_stamps 类型={type(stamps).__name__}，"
            f"items 类型={type(items).__name__}，条目数={count}"
        )
    return words


# --- Word-level stamp normalization (FR-39) ---------------------------------
# ForcedAligner leaves ~5% of function words with start == end (no acoustic
# segment), and occasionally emits a zero-width hallucinated duplicate of a
# nearby long token. Nothing here may touch a word that WAS aligned.
MIN_INTERP_GAP = 0.02
HALLUCINATION_CHARS = 30
URL_WORD_CHARS = 20
# Deliberately narrow: org (organi*zation*), net (inter*net*) and even com
# (ac*com*modation) occur inside ordinary long English words. Only a token
# that appears EXACTLY ONCE, with a domain-sized prefix and a short path,
# is treated as a run-together URL.
URL_TOKENS = ("www", "com")

# --- Uniform-stretch stamp guard (FR-41) -----------------------------------
# When a long stretch has no usable acoustic anchor (music / applause / ad
# voice-over), the forced aligner sometimes "spreads" a run of words evenly
# across it. In the 8:02 case four words were each exactly 1.52 s long with
# 3.04 s between starts, inflating the sentence window to 12.32 s even though
# the real speech was only the last ~3 s. We ONLY shrink their durations:
# starts, order, text and word count are never touched, and no word is ever
# relocated by guessing which energy peak is "the real speech" (the ad segment
# measured rms 3831 vs 4084 for the real line — energy cannot separate them).
MIN_STRETCH_WORDS = 3
MIN_STRETCH_WORD_DUR = 1.0
MIN_STRETCH_SPAN = 5.0
STRETCH_INTERVAL_TOL = 0.15
STRETCH_SEC_PER_CHAR = 0.08
STRETCH_BASE_DUR = 0.15


def detect_uniform_stretch(words: list[dict]) -> list[tuple[int, int]]:
    """Index ranges of evenly-spread inflated runs (read-only)."""
    ranges: list[tuple[int, int]] = []
    i, n = 0, len(words)
    while i < n:
        if words[i]["end"] - words[i]["start"] < MIN_STRETCH_WORD_DUR:
            i += 1
            continue
        j = i + 1
        while (j < n and
               words[j]["end"] - words[j]["start"] >= MIN_STRETCH_WORD_DUR):
            j += 1
        run = words[i:j]
        if (len(run) >= MIN_STRETCH_WORDS
                and run[-1]["end"] - run[0]["start"] >= MIN_STRETCH_SPAN):
            deltas = [run[k + 1]["start"] - run[k]["start"]
                      for k in range(len(run) - 1)]
            mean = sum(deltas) / len(deltas)
            if mean > 0 and (max(deltas) - min(deltas)) / mean <= STRETCH_INTERVAL_TOL:
                ranges.append((i, j))
        i = j
    return ranges


def _shrink_uniform_runs(words: list[dict],
                         ranges: list[tuple[int, int]]) -> tuple[int, float]:
    """Cap each inflated word by its character count; never move a start."""
    changed, saved = 0, 0.0
    for i, j in ranges:
        for k in range(i, j):
            w = words[k]
            dur = w["end"] - w["start"]
            cap = STRETCH_BASE_DUR + STRETCH_SEC_PER_CHAR * len(str(w["word"]))
            if dur <= cap + 1e-6:
                continue
            next_start = (words[k + 1]["start"] if k + 1 < len(words)
                          else w["end"])
            new_end = min(w["start"] + cap, next_start)
            if new_end <= w["start"]:
                continue
            saved += dur - (new_end - w["start"])
            w["end"] = round(new_end, 3)
            changed += 1
    return changed, round(saved, 3)


def _split_url_word(text: str):
    """`thrivecosmeticscomjillian` -> ['thrivecosmetics.com', 'jillian'].

    Only pure-ASCII alphanumeric strings longer than URL_WORD_CHARS with a
    single domain token in the middle. Chinese (character-level stamps),
    punctuation and ordinary long words are left alone.
    """
    if (len(text) <= URL_WORD_CHARS or not text.isascii()
            or not text.isalnum()):
        return None
    low = text.lower()
    for token in URL_TOKENS:
        idx = low.find(token)
        if low.count(token) != 1 or idx <= 3:
            continue
        tail = text[idx + len(token):]
        if 3 <= len(tail) <= 12:
            return [f"{text[:idx]}.{text[idx:idx + len(token)]}", tail]
    return None


def normalize_words(words: list[dict]):
    """Single decision for Qwen3-ASR word-stamp cleanup (FR-39).

    Returns (normalized_words, stats). Only abnormal words are touched:
      1. zero-width + >30 chars  -> hallucinated duplicate, drop
      2. zero-width run with a real gap -> linear interpolation by char count
         inside [prev.end, next.start)
      3. gap < MIN_INTERP_GAP   -> keep zero-width (no room; NEVER shift a
         real neighbour timestamp to make room)
      4. long ASCII URL-like token -> split into two words, proportional time
      5. evenly-spread inflated run -> cap each duration by char count; starts,
         order and word count never move (FR-41)
    """
    stats = {"dropped": 0, "interpolated": 0, "still_zero": 0, "split": 0,
             "stretch_groups": 0, "stretched": 0, "stretch_seconds": 0.0}

    # 1. Drop zero-width hallucinated duplicates.
    kept: list[dict] = []
    for w in words:
        if w["start"] >= w["end"] and len(str(w["word"])) > HALLUCINATION_CHARS:
            stats["dropped"] += 1
            continue
        kept.append(dict(w))

    # 2. Split long URL-like tokens (aligned words only).
    expanded: list[dict] = []
    for w in kept:
        parts = _split_url_word(str(w["word"])) if w["start"] < w["end"] else None
        if not parts:
            expanded.append(w)
            continue
        total = sum(len(p) for p in parts)
        cursor = float(w["start"])
        dur = float(w["end"]) - cursor
        for i, part in enumerate(parts):
            seg_start = cursor
            cursor += dur * len(part) / total
            seg_end = float(w["end"]) if i == len(parts) - 1 else cursor
            expanded.append({"word": part,
                             "start": round(seg_start, 3),
                             "end": round(seg_end, 3)})
        stats["split"] += 1

    # 3. Interpolate zero-width runs only where a real gap exists.
    i, n = 0, len(expanded)
    while i < n:
        if expanded[i]["start"] < expanded[i]["end"]:
            i += 1
            continue
        j = i
        while j < n and expanded[j]["start"] >= expanded[j]["end"]:
            j += 1
        left = expanded[i - 1]["end"] if i > 0 else expanded[i]["start"]
        right = expanded[j]["start"] if j < n else expanded[i]["start"]
        gap = right - left
        if gap >= MIN_INTERP_GAP:
            weights = [max(1, len(str(x["word"]))) for x in expanded[i:j]]
            total = sum(weights)
            cursor = float(left)
            for k, x in enumerate(expanded[i:j]):
                x["start"] = round(cursor, 3)
                cursor += gap * weights[k] / total
                x["end"] = (round(float(right), 3)
                            if k == len(weights) - 1 else round(cursor, 3))
                stats["interpolated"] += 1
        else:
            stats["still_zero"] += j - i
        i = j

    # 4. Evenly-spread inflated runs (FR-41): durations shrink, starts stay.
    ranges = detect_uniform_stretch(expanded)
    stretched, stretch_seconds = _shrink_uniform_runs(expanded, ranges)
    stats["stretch_groups"] = len(ranges)
    stats["stretched"] = stretched
    stats["stretch_seconds"] = stretch_seconds

    return expanded, stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Qwen3-ASR isolated-env runner")
    parser.add_argument("--spec-file", required=True)
    args = parser.parse_args()

    try:
        spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"ERROR: 无法读取 spec: {exc}")
        return 1

    try:
        import torch
        from qwen_asr import Qwen3ASRModel
    except ImportError as exc:
        log(f"ERROR: 独立环境缺少 qwen-asr / torch: {exc}")
        log("请在 qwen3_asr.python_exe 指向的环境执行: pip install -U qwen-asr")
        return 2

    dtype = getattr(torch, spec["dtype"], None)
    if dtype is None:
        log(f"ERROR: 未知 dtype: {spec['dtype']}")
        return 2

    log(f"[Qwen3-ASR] loading {spec['model']} + {spec['aligner']} on {spec['device']}")
    try:
        model = Qwen3ASRModel.from_pretrained(
            spec["model"],
            dtype=dtype,
            device_map=spec["device"],
            max_inference_batch_size=int(spec["max_inference_batch_size"]),
            max_new_tokens=int(spec["max_new_tokens"]),
            forced_aligner=spec["aligner"],
            forced_aligner_kwargs={"dtype": dtype, "device_map": spec["device"]},
        )
    except Exception as exc:  # noqa: BLE001 — surface a single actionable error
        log(f"ERROR: 模型加载失败: {exc}")
        log("请确认权重已下载（modelscope download --model <id>）且 CUDA / 显存可用")
        return 3

    segments: list[dict] = []
    windows = spec["windows"]
    overlap = float(spec["overlap_seconds"])
    for idx, (win_start, win_end) in enumerate(windows):
        win_start, win_end = float(win_start), float(win_end)
        length = win_end - win_start
        if length <= 0:
            continue
        # ffmpeg seeks in full-file coordinates; emitted stamps stay segment-relative.
        abs_start = float(spec["start"]) + win_start
        log(f"[Qwen3-ASR] window {idx + 1}/{len(windows)}: "
            f"{win_start:.1f}-{win_end:.1f}s")
        try:
            audio = decode_window(spec["audio_file"], abs_start, length)
            results = model.transcribe(
                audio=(audio, SAMPLE_RATE),
                language=spec.get("language"),
                return_time_stamps=True,
            )
        except Exception as exc:  # noqa: BLE001 — one actionable failure, no fallback
            log(f"ERROR: 窗口 {idx + 1} 推理失败: {exc}")
            return 4
        if not results:
            continue

        result = results[0]
        try:
            words = parse_stamps(result)
        except RuntimeError as exc:
            log(f"ERROR: {exc}")
            return 5

        words, stats = normalize_words(words)
        log(f"[Qwen3-ASR] word normalize: dropped={stats['dropped']} "
            f"interpolated={stats['interpolated']} "
            f"still_zero={stats['still_zero']} split={stats['split']} "
            f"stretch_groups={stats['stretch_groups']} "
            f"stretched={stats['stretched']} "
            f"stretch_seconds={stats['stretch_seconds']}")

        # Overlap belongs to the previous window: drop stamps inside the first
        # half of this window's overlap so boundary words are not duplicated.
        cutoff = win_start + (overlap / 2.0 if idx else 0.0)
        kept = []
        for w in words:
            t = win_start + w["start"]
            if t < cutoff - 1e-6:
                continue
            kept.append({"word": w["word"],
                         "start": round(t, 3),
                         "end": round(win_start + w["end"], 3)})
        if not kept:
            continue
        segments.append({
            "text": str(_field(result, "text") or "").strip(),
            "start": kept[0]["start"],
            "end": kept[-1]["end"],
            "words": kept,
        })

    if not segments:
        log("ERROR: 所有窗口均未产出词级时间戳")
        return 6
    print(json.dumps({"segments": segments}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
