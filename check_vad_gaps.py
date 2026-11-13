"""只读检查当前视频ASR覆盖，--fill离线复核，不修改字幕/断点/配置。

python check_vad_gaps.py
python check_vad_gaps.py --fill
python check_vad_gaps.py --fill --start 00:09:30 --end 00:10:10

有声音不等于有人讲话：扫描只标疑似漏转，最终需对照原音频/复核文本。
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

AUDIO = os.path.join("output", "audio", "raw.mp3")
WORDS = os.path.join("output", "log", "cleaned_chunks.json")
SRT = os.path.join("output", "src.srt")
CHECKPOINT = os.path.join("output", "log", "asr_checkpoint")


def parse_srt(path):
    from core.services.ad_service import parse_srt as parse
    return [(s["start"], s["end"]) for s in parse(Path(path).read_text(encoding="utf-8-sig"))]


def scan():
    from core.asr_backend.vad_gap_fill import (
        find_uncovered_gaps, gap_fill_settings, prompt_echo, speech_ratio)
    cfg = gap_fill_settings()
    segments = []
    for path in sorted(Path(CHECKPOINT).glob("seg_*.json")):
        segments.extend(json.loads(path.read_text(encoding="utf-8"))["segments"])
    if not segments and os.path.isfile(WORDS):
        segments = json.loads(Path(WORDS).read_text(encoding="utf-8"))["words"]
    if not segments:
        print("未找到当前视频ASR断点/词表，请先完成转录。")
        return []
    print(f"当前ASR记录 {len(segments)} 条；阈值 {cfg['min_gap']}s（原片时间）")
    # ASR优先于SRT：字幕延长显示会遮住实际无词空档。
    found = []
    for a, b in find_uncovered_gaps(segments, cfg["min_gap"]):
        ratio = speech_ratio(AUDIO, a, b, cfg["noise"], cfg["min_silence"])
        candidate = (ratio >= cfg["min_speech_ratio"]
                     or ratio * (b - a) >= min(1.0, cfg["min_gap"]))
        print(f"  {a:9.3f} → {b:9.3f} ({b-a:5.1f}s)，有声 {ratio:.0%}，"
              f"{'疑似漏转，需复核' if candidate else '主要为静音'}")
        if candidate:
            found.append((a, b))
    hints_path = Path(CHECKPOINT) / "asr_hints.json"
    if hints_path.is_file():
        hints = json.loads(hints_path.read_text(encoding="utf-8")).get("hints", {})
        for s in segments:
            if prompt_echo(s, hints.get("initial_prompt")):
                print(f"  {s['start']:.3f} → {s['end']:.3f}：疑似提示词回声 {s['text']}")
                found.append((s["start"], s["end"]))
    print(f"疑似区间 {len(found)} 处；仅扫描无法保证检出短漏词或时间错位。")
    return sorted(set(found))


def fill(gaps):
    if not gaps:
        print("当前没有待复核区间，不加载模型。")
        return
    import numpy as np
    from core.asr_backend.whisperX_local import _find_local_hf_model
    from core.asr_backend.vad_gap_fill import looks_like_hallucination
    from core.utils import load_key
    from faster_whisper import WhisperModel
    import torch

    name = str(load_key("whisper.model"))
    repo = name if "/" in name else f"Systran/faster-whisper-{name}"
    local = Path(name) if Path(name).is_dir() else _find_local_hf_model(repo)
    if local is None:
        local = Path(str(load_key("model_dir"))) / name
    if not Path(local).is_dir():
        raise FileNotFoundError("未找到已缓存模型；只读复核不会自动下载模型。")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = WhisperModel(str(local), device=device,
                         compute_type="float16" if device == "cuda" else "int8",
                         local_files_only=True)
    language = str(load_key("whisper.language"))
    language = None if "auto" in language else language
    for a, b in gaps:
        args = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(a), "-i", AUDIO,
                "-t", str(b-a), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"]
        raw = subprocess.run(args, capture_output=True, check=True).stdout
        samples = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
        found, _ = model.transcribe(samples, language=language, beam_size=5,
                                    vad_filter=False, initial_prompt=None, hotwords=None,
                                    condition_on_previous_text=False)
        print(f"\n复核 {a:.3f} → {b:.3f}（只读，不回写产物）")
        for s in found:
            if not looks_like_hallucination(s.text):
                print(f"  [{a+s.start:.3f} → {a+s.end:.3f}] {s.text.strip()}")


def seconds(value):
    try:
        parts = list(map(float, value.split(":")))
        if len(parts) > 3 or any(p < 0 for p in parts):
            raise ValueError
        total = 0.0
        for part in parts:
            total = total * 60 + part
        return total
    except ValueError as e:
        raise argparse.ArgumentTypeError("请输入秒数或HH:MM:SS") from e


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fill", action="store_true")
    parser.add_argument("--start", type=seconds)
    parser.add_argument("--end", type=seconds)
    args = parser.parse_args()
    if (args.start is None) != (args.end is None):
        parser.error("--start和--end必须同时提供")
    if args.start is not None and args.end <= args.start:
        parser.error("--end必须大于--start")
    ranges = [(args.start, args.end)] if args.start is not None else scan()
    if args.fill:
        fill(ranges)
