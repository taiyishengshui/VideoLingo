import os, json, subprocess
from core.utils import *
from core.asr_backend.demucs_vl import demucs_audio
from core.asr_backend.audio_preprocess import process_transcription, convert_video_to_audio, prepare_audio_for_asr, split_audio, save_results, normalize_audio_volume, get_audio_duration
from core.utils.media_files import find_media_file
from core.utils.models import *

# Checkpoint directory: per-segment transcription results, so an interrupted run
# can resume exactly where it stopped instead of restarting from segment 1.
_CKPT_DIR = "output/log/asr_checkpoint"
_SEGMENTS_FILE = os.path.join(_CKPT_DIR, "segments.json")


def _audio_fingerprint(audio_file: str) -> dict:
    # gap_fill 进指纹：补转开关/阈值一变，旧的转写结果就该作废重转 ——
    # 否则改了补转配置却直接复用上一轮的 checkpoint，补转永远不生效。
    try:
        from core.asr_backend.vad_gap_fill import gap_fill_settings
        gap_fill = gap_fill_settings()
    except Exception:
        gap_fill = {"enabled": False}
    from core.services.asr_service import decode_settings
    return {
        "file": os.path.basename(audio_file),
        "size": os.path.getsize(audio_file),
        "duration": round(get_audio_duration(audio_file), 3),
        "gap_fill": gap_fill,
        # Decode-policy changes must not replay earlier prompted/hallucinated ASR.
        "decode": decode_settings(),
    }


def _load_or_save_segments(audio_file: str) -> list:
    """Reuse the persisted split list across runs.

    Segment boundaries come from silence detection; re-splitting could pick
    slightly different cut points on some runs, which would misalign the
    per-segment checkpoint files. Persisting the list keeps resume runs
    bitwise-consistent with the first run. If the audio file changed (new
    fingerprint), wipe the whole checkpoint and start fresh.
    """
    os.makedirs(_CKPT_DIR, exist_ok=True)
    fingerprint = _audio_fingerprint(audio_file)
    segments = split_audio(audio_file)
    segments = [[round(float(s), 3), round(float(e), 3)] for s, e in segments]

    if os.path.exists(_SEGMENTS_FILE):
        try:
            with open(_SEGMENTS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except (json.JSONDecodeError, OSError):
            saved = None
        if saved and saved.get("fingerprint") == fingerprint and \
           [[round(s, 3), round(e, 3)] for s, e in saved.get("segments", [])] == segments:
            rprint(f"[cyan]♻️ Reusing saved segment list ({len(segments)} segments).[/cyan]")
            return [tuple(seg) for seg in segments]
        # fingerprint or boundary mismatch → stale checkpoint, start over
        rprint("[yellow]⚠️ Checkpoint is stale (audio changed), clearing it.[/yellow]")
        _clear_checkpoint()

    with open(_SEGMENTS_FILE, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fingerprint, "segments": segments}, f, ensure_ascii=False, indent=1)
    return [tuple(seg) for seg in segments]


def _seg_file(index: int) -> str:
    return os.path.join(_CKPT_DIR, f"seg_{index:04d}.json")


def _load_done_results(segments: list):
    """Load per-segment results already on disk, in order, until the first
    gap. Corrupt/partial files are treated as missing (re-transcribed);
    writes are atomic (tmp + rename) so a file either parses or is absent."""
    all_results = []
    for i in range(len(segments)):
        path = _seg_file(i)
        if not os.path.exists(path):
            return all_results, i  # results are strictly ordered and gapless
        try:
            with open(path, "r", encoding="utf-8") as f:
                result = json.load(f)
            if 'segments' not in result:
                raise ValueError("missing 'segments' key")
            all_results.append(result)
        except (json.JSONDecodeError, ValueError, OSError):
            rprint(f"[yellow]⚠️ Corrupt checkpoint segment {i + 1}, will re-transcribe it.[/yellow]")
            return all_results, i
    return all_results, len(segments)


def _save_seg_result(index: int, result):
    """Atomic per-segment save: write to .tmp then rename, so a crash can
    never leave a half-written checkpoint file behind."""
    os.makedirs(_CKPT_DIR, exist_ok=True)
    tmp = _seg_file(index) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    os.replace(tmp, _seg_file(index))


def _clear_checkpoint():
    import shutil
    if os.path.exists(_CKPT_DIR):
        shutil.rmtree(_CKPT_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Adaptive domain hints: transcribe a short opening sample, then ask the LLM
# to summarize the domain + key terms. Fresh per audio file (fingerprinted),
# cached by ask_gpt's own prompt cache — resume runs hit the LLM cache, so
# a re-run of the same audio costs zero extra API calls.
# ---------------------------------------------------------------------------
_HINTS_FILE = os.path.join(_CKPT_DIR, "asr_hints.json")
_HINT_SAMPLE_SECONDS = 180


def _validate_hints(resp):
    if not isinstance(resp, dict):
        return {'status': 'error', 'message': 'hints must be a JSON object'}
    for key in ("initial_prompt", "hotwords"):
        val = resp.get(key)
        if val is not None and not isinstance(val, str):
            return {'status': 'error', 'message': f'{key} must be a string'}
    return {'status': 'success', 'message': ''}


def _first_speech_offset(audio_file: str, scan_limit: float = 600.0) -> float:
    """Return the offset (seconds) of the first non-silent audio, via ffmpeg
    silencedetect. Audio often begins with lead-in silence; probing from there
    (instead of from 0) gives the LLM real speech within its sample window.
    Failures degrade to 0.0 — probing from 0 is always safe."""
    try:
        cmd = [
            "ffmpeg", "-nostdin", "-t", f"{scan_limit:.0f}",
            "-i", audio_file,
            "-af", "silencedetect=noise=-35dB:d=1",
            "-f", "null", "-",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        # Leading silence appears as a silence_start at/into 0 followed by
        # silence_end at the actual first speech moment.
        import re as _re
        m = _re.search(r"silence_end:\s*([\d.]+)", result.stderr)
        if m and result.stderr.find("silence_start:") >= 0:
            offset = float(m.group(1))
            # Only treat it as LEADING silence if the silence truly starts ~0;
            # a lone silence_end without leading silence means speech from 0.
            lead = _re.search(r"silence_start:\s*([\d.]+)", result.stderr)
            begins_silent = lead is None or float(lead.group(1)) < 1.0
            if begins_silent and 1.0 <= offset < scan_limit:
                return offset
        return 0.0
    except Exception:
        return 0.0


def _detect_asr_hints(segments, fingerprint: dict):
    """Probe the opening of the audio with the ASR model itself, then let the
    LLM extract domain context. Wrapped in try/except: any failure degrades
    to no hints (transcription still works, just without domain steering)."""
    try:
        from core.asr_backend.whisperX_local import transcribe_audio as _probe_ts
        # Start the probe at the first actual speech (skip lead-in silence),
        # then take a fixed window. Short files are inherently safe: the probe
        # window is capped by the audio's own duration.
        speech_start = _first_speech_offset(_RAW_AUDIO_FILE)
        probe_end = min(speech_start + _HINT_SAMPLE_SECONDS,
                        segments[-1][1] if segments else _HINT_SAMPLE_SECONDS)
        probe_start = min(speech_start, max(0.0, probe_end - 10.0))
        rprint(f"[cyan]🔍 Probing audio domain from {probe_start:.1f}s to "
               f"{probe_end:.1f}s (adaptive hotwords)...[/cyan]")
        sample = _probe_ts(_RAW_AUDIO_FILE, _RAW_AUDIO_FILE, probe_start, probe_end)

        sample_text = " ".join(
            seg.get("text", "") for seg in sample.get("segments", [])
        ).strip()[:4000]
        if not sample_text:
            rprint("[yellow]⚠️ Probe produced no text; skipping domain hints.[/yellow]")
            return None

        prompt = (
            "You are helping an ASR (speech recognition) system improve accuracy "
            "on this audio. Below is a transcript of the FIRST 3 MINUTES of a "
            "longer audio file.\n\n"
            "Transcript:\n" + sample_text + "\n\n"
            "Based on this, return a JSON object with exactly these keys:\n"
            '- "domain": one short sentence describing what this audio is about '
            "(topic, speakers, setting if identifiable)\n"
            '- "initial_prompt": an English sentence (max ~30 words) that describes '
            "the audio content, style and setting, to prime a speech recognition "
            "model. Do NOT include any transcript text in it.\n"
            '- "hotwords": a comma-separated list (single string, max 20 items) of '
            "domain terms, names and jargon likely to appear in this audio, to boost "
            "their recognition probability. Transcribe names as most likely spelled.\n\n"
            "Respond with the JSON object only."
        )
        hints = ask_gpt(prompt, resp_type="json", valid_def=_validate_hints,
                       log_title="asr_domain_hints")
        # persist next to the checkpoint so a resumed run reuses them and the
        # transcription stays consistent across the resume boundary
        os.makedirs(_CKPT_DIR, exist_ok=True)
        with open(_HINTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": fingerprint, "hints": hints}, f, ensure_ascii=False, indent=1)
        rprint(f"[green]✓ Domain hints: {hints.get('domain', '')[:80]}[/green]")
        rprint(f"[green]✓ Hotwords: {str(hints.get('hotwords', ''))[:120]}[/green]")
        return hints
    except Exception as e:
        rprint(f"[yellow]⚠️ Domain hint detection failed ({e}); "
               f"transcribing without hints.[/yellow]")
        return None


def _load_asr_hints(fingerprint: dict):
    """Reuse hints saved by a previous run of the same audio (fingerprint must
    match — a new audio gets fresh hints via _detect_asr_hints)."""
    if not os.path.exists(_HINTS_FILE):
        return None
    try:
        with open(_HINTS_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("fingerprint") == fingerprint:
            hints = saved.get("hints")
            rprint("[cyan]♻️ Reusing domain hints from previous run.[/cyan]")
            return hints
    except (json.JSONDecodeError, OSError):
        pass
    return None


@check_file_exists(_2_CLEANED_CHUNKS)
def transcribe():
    # 1. prepare audio
    media_file, media_type = find_media_file()
    if media_type == "video":
        convert_video_to_audio(media_file)
    else:
        prepare_audio_for_asr(media_file)

    # 2. Demucs vocal separation:
    if load_key("demucs"):
        demucs_audio()
        vocal_audio = normalize_audio_volume(_VOCAL_AUDIO_FILE, _VOCAL_AUDIO_FILE, format="mp3")
    else:
        vocal_audio = _RAW_AUDIO_FILE

    # 3. Load (or resume) the segment split
    segments = _load_or_save_segments(_RAW_AUDIO_FILE)

    # 3.5 Adaptive domain hints: local runtime only (the probe uses the local
    # whisper model; cloud/elevenlabs runtimes skip hints entirely).
    runtime = load_key("whisper.runtime")
    asr_hints = None
    if runtime == "local":
        fingerprint = _audio_fingerprint(_RAW_AUDIO_FILE)
        asr_hints = _load_asr_hints(fingerprint)
        if asr_hints is None:
            asr_hints = _detect_asr_hints(segments, fingerprint)

    # 4. Find how much is already transcribed
    all_results, start_index = _load_done_results(segments)
    if start_index > 0:
        rprint(f"[cyan]♻️ Resuming: segments 1-{start_index} already done, "
               f"continuing from segment {start_index + 1}/{len(segments)}.[/cyan]")
    else:
        rprint(f"[cyan]🎤 Transcribing {len(segments)} segments from scratch.[/cyan]")

    # 5. Transcribe audio by clips
    if runtime == "local":
        from core.asr_backend.whisperX_local import transcribe_audio as ts
        rprint("[cyan]🎤 Transcribing audio with local model...[/cyan]")
    elif runtime == "cloud":
        from core.asr_backend.whisperX_302 import transcribe_audio_302 as ts
        rprint("[cyan]🎤 Transcribing audio with 302 API...[/cyan]")
    elif runtime == "elevenlabs":
        from core.asr_backend.elevenlabs_asr import transcribe_audio_elevenlabs as ts
        rprint("[cyan]🎤 Transcribing audio with ElevenLabs API...[/cyan]")

    for i in range(start_index, len(segments)):
        check_cancel()
        start, end = segments[i]
        rprint(f"[cyan]🎵 Segment {i + 1}/{len(segments)}: "
               f"{start:.1f}s -> {end:.1f}s[/cyan]")
        # asr_hints is local-runtime only; cloud/elevenlabs ts() don't accept it
        if runtime == "local":
            result = ts(_RAW_AUDIO_FILE, vocal_audio, start, end, asr_hints=asr_hints)
        else:
            result = ts(_RAW_AUDIO_FILE, vocal_audio, start, end)
        _save_seg_result(i, result)
        all_results.append(result)

    # 6. Combine results
    combined_result = {'segments': []}
    for result in all_results:
        combined_result['segments'].extend(result['segments'])

    # 7. Process df and save — the xlsx is written once, fully complete.
    df = process_transcription(combined_result)
    save_results(df)

    # 8. Per-segment transcription log is KEPT at the checkpoint dir — it doubles
    # as a backup: if the xlsx is ever lost, re-running this step rebuilds it
    # from the checkpoint in seconds (no re-transcription). To force a FULL
    # re-transcription of the same audio, delete the checkpoint dir by hand
    # (or replace the audio file — the fingerprint mismatch clears it).
    rprint(f"[green]✅ Transcription complete. Per-segment log kept at {_CKPT_DIR}[/green]")

if __name__ == "__main__":
    transcribe()
