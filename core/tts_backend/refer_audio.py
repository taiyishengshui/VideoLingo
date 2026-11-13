"""Reference audio extraction — shared tool for TTS backends.

Extracted from core/_9_refer_audio.py (2026-09-08). Both step 9 (pipeline
orchestration) and several TTS backends (GPT-SoVITS, CosyVoice2 — they need
speaker reference clips on demand) use this. Backends must not import a
numbered step module, so the implementation lives here in tts_backend.
"""
import os

import pandas as pd
import soundfile as sf
from core.utils.log_hooks import rprint
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from core.asr_backend.demucs_vl import demucs_audio
from core.utils.models import _AUDIO_SEGS_DIR, _AUDIO_REFERS_DIR, _8_1_AUDIO_TASK, _VOCAL_AUDIO_FILE


def time_to_samples(time_str, sr):
    """Unified time conversion function"""
    h, m, s = time_str.split(':')
    s, ms = s.split(',') if ',' in s else (s, '0')
    seconds = int(h) * 3600 + int(m) * 60 + float(s) + float(ms) / 1000
    return int(seconds * sr)


def extract_audio(audio_data, sr, start_time, end_time, out_file):
    """Simplified audio extraction function"""
    start = time_to_samples(start_time, sr)
    end = time_to_samples(end_time, sr)
    sf.write(out_file, audio_data[start:end], sr)


def _fixed_voice_only() -> bool:
    """True only when the active backend uses a fixed speaker wav."""
    try:
        from core.utils.config_utils import load_key
        if load_key("tts_method") != "index_tts":
            return False
        return int(load_key("index_tts.refer_mode")) == 1
    except (KeyError, ValueError, TypeError):
        return False


def _refers_complete(df) -> bool:
    """Every subtitle line must have its own (non-empty) reference clip.

    A plain ``os.listdir()`` check lets a half-finished or force-rerun
    directory look "done", which makes per-line cloning silently degrade to
    line 1 for hundreds of lines.
    """
    if not os.path.isdir(_AUDIO_REFERS_DIR):
        return False
    for n in df["number"]:
        path = os.path.join(_AUDIO_REFERS_DIR, f"{n}.wav")
        if not os.path.isfile(path) or os.path.getsize(path) < 1024:
            return False
    return True


def extract_refer_audio_main():
    # refer_mode=1 is the ONLY case with no original vocals: it uses a preset
    # wav, so Demucs (~2.5 min GPU) would produce slices nobody reads.
    # Create the marker dir so the pipeline registers step 9 as done.
    # Any other mode (2/3, or any non-index_tts backend) runs the full path.
    if _fixed_voice_only():
        os.makedirs(_AUDIO_REFERS_DIR, exist_ok=True)
        rprint(Panel("index-tts refer_mode=1 → 使用固定音色文件，不提取原声。"
                     " 想用原视频人声请把 config.yaml 的 index_tts.refer_mode 改成 2 或 3，并重新运行本步骤",
                     title="Info", border_style="blue"))
        return

    demucs_audio() #!!! in case demucs not run
    # Guard on OUR OWN output (refers/), not segs/ — segs belongs to
    # gen_audio; guarding on it made a refer_audio rerun silently skip
    # whenever dub audio existed, and left stale refers after a
    # gen_audio rerun re-cut the task table.
    df = pd.read_excel(_8_1_AUDIO_TASK)
    if _refers_complete(df):
        rprint(Panel(f"原声参考音频已齐全（{len(df)} 条），跳过提取", title="Info", border_style="blue"))
        return

    # Create output directory
    os.makedirs(_AUDIO_REFERS_DIR, exist_ok=True)

    # Read task file and audio data
    # float32 halves the peak RAM: a 74-min stereo mp3 is ~3.1 GB as float64.
    # soundfile writes .wav as PCM_16, so the conversion is lossless enough
    # for a speaker prompt.
    data, sr = sf.read(_VOCAL_AUDIO_FILE, dtype="float32")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
    ) as progress:
        task = progress.add_task("从原视频人声切出逐句参考音频...", total=len(df))

        for _, row in df.iterrows():
            out_file = os.path.join(_AUDIO_REFERS_DIR, f"{row['number']}.wav")
            extract_audio(data, sr, row['start_time'], row['end_time'], out_file)
            progress.update(task, advance=1)

    rprint(Panel(f"✅ 已【从原视频人声】切出 {len(df)} 条参考音频 → {_AUDIO_REFERS_DIR}\n"
                 f"refer_mode=2 用 1.wav（全片统一音色）；refer_mode=3 逐句使用对应片段。"
                 f" 原声模式不会使用 spk_audio_prompt 预置音色。",
                 title="Success", border_style="green"))


if __name__ == "__main__":
    extract_refer_audio_main()
