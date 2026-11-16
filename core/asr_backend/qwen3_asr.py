"""Qwen3-ASR host-side adapter (FR-35).

Runs with the project's own Python (transformers 4.52.1 stays untouched).
The heavy model lives in a separate conda environment and is driven through
`qwen3_asr_runner.py` via subprocess. This module owns ALL host-side decisions:
config + validation, window splitting, runner invocation, timestamp shifting
and the whisperX-compatible result shape.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

_RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "qwen3_asr_runner.py")

# Qwen3-ForcedAligner-0.6B supports exactly these 11 languages.
_LANGUAGE_MAP = {
    "auto": None,
    "zh": "Chinese", "en": "English", "yue": "Cantonese",
    "fr": "French", "de": "German", "it": "Italian",
    "ja": "Japanese", "ko": "Korean", "pt": "Portuguese",
    "ru": "Russian", "es": "Spanish",
}

_DEFAULTS = {
    "python_exe": "",
    "model": "Qwen/Qwen3-ASR-1.7B",
    "aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
    "language": "auto",
    "max_window_seconds": 270,
    "overlap_seconds": 1.5,
    "max_inference_batch_size": 8,
    "max_new_tokens": 1024,
    "device": "cuda:0",
    "dtype": "bfloat16",
}

# Keys that change transcription output → must be in the checkpoint fingerprint.
_SIGNATURE_KEYS = (
    "model", "aligner", "language", "max_window_seconds", "overlap_seconds",
    "max_inference_batch_size", "max_new_tokens", "device", "dtype",
)


def _load(key: str, default):
    """Read config.yaml safely; missing keys fall back to documented defaults."""
    try:
        from core.utils.config_utils import load_key
        value = load_key(key)
    except (KeyError, ImportError, OSError, TypeError):
        return default
    return default if value is None else value


def _rprint(msg: str) -> None:
    try:
        from core.utils.log_hooks import rprint
        rprint(msg)
    except Exception:  # noqa: BLE001 — logging must never break transcription
        print(msg)


def qwen3_config() -> dict:
    """Validated config. Raises ValueError for values that would silently
    produce wrong transcripts (never fall back to another ASR backend)."""
    cfg = {k: _load("qwen3_asr." + k, v) for k, v in _DEFAULTS.items()}
    cfg["python_exe"] = str(cfg["python_exe"]).strip()

    lang = str(cfg["language"]).strip().lower()
    if lang not in _LANGUAGE_MAP:
        raise ValueError(
            f"qwen3_asr.language='{lang}' 不在 ForcedAligner 支持的 11 语种内；"
            f"可用 {', '.join(_LANGUAGE_MAP)}，其他语言请改用 whisper.runtime=local"
        )
    cfg["language"] = lang

    max_window = float(cfg["max_window_seconds"])
    overlap = float(cfg["overlap_seconds"])
    if max_window <= 0 or max_window > 300:
        raise ValueError(
            "qwen3_asr.max_window_seconds 必须在 (0, 300]："
            "ForcedAligner 单段上限 5 分钟"
        )
    if overlap < 0 or overlap >= max_window:
        raise ValueError("qwen3_asr.overlap_seconds 必须在 [0, max_window_seconds)")
    cfg["max_window_seconds"] = max_window
    cfg["overlap_seconds"] = overlap

    cfg["max_inference_batch_size"] = max(1, int(cfg["max_inference_batch_size"]))
    cfg["max_new_tokens"] = max(1, int(cfg["max_new_tokens"]))
    cfg["device"] = str(cfg["device"]).strip() or "cuda:0"
    cfg["dtype"] = str(cfg["dtype"]).strip() or "bfloat16"
    cfg["model"] = str(cfg["model"]).strip()
    cfg["aligner"] = str(cfg["aligner"]).strip()
    return cfg


def qwen3_signature() -> dict:
    """Only result-affecting keys — consumed by _2_asr._audio_fingerprint."""
    cfg = qwen3_config()
    return {k: cfg[k] for k in _SIGNATURE_KEYS}


def build_windows(start: float, end: float, max_window: float,
                  overlap: float) -> list[list[float]]:
    """Unique window-splitting decision. Returns [start, end] pairs RELATIVE
    to `start`, each <= 5 min for the forced aligner, with `overlap` seconds
    shared between neighbours so boundary words are not lost."""
    total = float(end) - float(start)
    max_window = float(max_window)
    overlap = float(overlap)
    if total <= 0:
        raise ValueError(f"无效音频区间 [{start}, {end}]")
    if max_window <= 0 or max_window > 300:
        raise ValueError("max_window_seconds 必须在 (0, 300]")
    if overlap < 0 or overlap >= max_window:
        raise ValueError("overlap_seconds 必须在 [0, max_window_seconds)")

    windows: list[list[float]] = []
    pos = 0.0
    step = max_window - overlap
    while pos < total - 1e-6:
        win_end = min(pos + max_window, total)
        windows.append([round(pos, 3), round(win_end, 3)])
        if win_end >= total - 1e-6:
            break
        pos += step
    return windows


def normalize_runner_result(payload, seg_start: float) -> dict:
    """Validate runner JSON and shift segment-relative stamps to full-file
    absolute time — same contract as whisperX_local.transcribe_audio."""
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise RuntimeError("Qwen3-ASR runner 未返回 {'segments': [...]} 结构")
    if not payload["segments"]:
        raise RuntimeError("Qwen3-ASR runner 返回空 segments（未产出任何转录）")

    out = {"segments": []}
    for seg in payload["segments"]:
        words = seg.get("words")
        if not words:
            raise RuntimeError("Qwen3-ASR runner 的 segment 缺 words（词级时间戳必须存在）")
        shifted = []
        for w in words:
            if not all(k in w for k in ("word", "start", "end")):
                raise RuntimeError("Qwen3-ASR runner 的 word 缺 word/start/end 字段")
            shifted.append({
                "word": str(w["word"]),
                "start": round(float(w["start"]) + seg_start, 3),
                "end": round(float(w["end"]) + seg_start, 3),
            })
        out["segments"].append({
            "text": str(seg.get("text") or ""),
            "start": round(float(seg.get("start", 0)) + seg_start, 3),
            "end": round(float(seg.get("end", 0)) + seg_start, 3),
            "words": shifted,
        })
    return out


def transcribe_audio_qwen3_asr(raw_audio_file: str, vocal_audio_file: str,
                               start: float, end: float) -> dict:
    """Drop-in replacement for whisperX_local.transcribe_audio.

    Never falls back to whisperX: any missing dependency/config/weight or
    runner failure raises an actionable error instead.
    """
    cfg = qwen3_config()
    python_exe = cfg["python_exe"]
    install_hint = (
        "Qwen3-ASR 需要独立 conda 环境（主环境 transformers 4.52.1 被 IndexTTS 锁死，"
        "qwen-asr 要求 4.57.6）。请在设置页填 qwen3_asr.python_exe，"
        "并在该环境执行 `pip install -U qwen-asr` + 下载 "
        "Qwen/Qwen3-ASR-1.7B 与 Qwen/Qwen3-ForcedAligner-0.6B 权重；"
        "或把 whisper.runtime 改回 local。"
    )
    if not python_exe:
        raise RuntimeError(f"qwen3_asr.python_exe 未配置。{install_hint}")
    if not os.path.isfile(python_exe):
        raise RuntimeError(f"qwen3_asr.python_exe 不存在：{python_exe}。{install_hint}")
    if not os.path.isfile(_RUNNER):
        raise RuntimeError(f"Qwen3-ASR runner 缺失：{_RUNNER}")

    windows = build_windows(start, end, cfg["max_window_seconds"],
                            cfg["overlap_seconds"])
    spec = {
        "audio_file": os.path.abspath(raw_audio_file),
        "start": float(start),
        "end": float(end),
        "windows": windows,
        "model": cfg["model"],
        "aligner": cfg["aligner"],
        "language": _LANGUAGE_MAP[cfg["language"]],
        "max_inference_batch_size": cfg["max_inference_batch_size"],
        "max_new_tokens": cfg["max_new_tokens"],
        "device": cfg["device"],
        "dtype": cfg["dtype"],
        "overlap_seconds": cfg["overlap_seconds"],
    }

    _rprint(f"[cyan]🎤 Qwen3-ASR 转录 {start:.1f}s -> {end:.1f}s · "
            f"{len(windows)} 个 ≤{cfg['max_window_seconds']:.0f}s 窗口[/cyan]")

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump(spec, fh, ensure_ascii=False)
            tmp_path = fh.name
        cmd = [python_exe, _RUNNER, "--spec-file", tmp_path]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RuntimeError(f"无法启动 Qwen3-ASR 独立环境进程：{exc}。{install_hint}") from exc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()[-800:]
        raise RuntimeError(
            f"Qwen3-ASR runner 退出码 {proc.returncode}：{err or '(无 stderr)'}。"
            f"{install_hint}"
        )

    payload = None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            break
        except ValueError:
            continue
    if payload is None:
        raise RuntimeError(
            "Qwen3-ASR runner stdout 未输出可解析的 JSON。"
            f"stdout 尾部：{(proc.stdout or '')[-300:]}"
        )
    return normalize_runner_result(payload, float(start))
