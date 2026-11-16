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
    """One ASR result -> [{word, start, end}] relative to the window audio."""
    stamps = _field(result, "time_stamps")
    if stamps is None:
        raise RuntimeError(
            "ForcedAligner 未返回 time_stamps：请确认 qwen3_asr.aligner 已配置、"
            "权重已下载，且该语言在 ForcedAligner 支持的 11 语种内"
        )
    if isinstance(stamps, (list, tuple)) and stamps and isinstance(stamps[0], (list, tuple)):
        stamps = stamps[0]
    if not isinstance(stamps, (list, tuple)):
        stamps = [stamps]

    words: list[dict] = []
    for item in stamps:
        text = _field(item, "text", "word", "char")
        start = _field(item, "start_time", "start")
        end = _field(item, "end_time", "end")
        if text is None or start is None or end is None:
            continue
        words.append({"word": str(text), "start": float(start), "end": float(end)})
    if not words:
        raise RuntimeError(
            "ForcedAligner 时间戳为空或格式不可识别（需 text/start_time/end_time）"
        )
    return words


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
