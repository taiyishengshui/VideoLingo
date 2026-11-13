import os
import gc
import subprocess
import torch
from rich.console import Console
from core.utils.log_hooks import rprint
from demucs.pretrained import get_model
from demucs.audio import prevent_clip
from torch.cuda import is_available as is_cuda_available
from typing import Optional
from demucs.api import Separator
from demucs.apply import BagOfModels
from core.utils.models import *


class PreloadedSeparator(Separator):
    def __init__(self, model: BagOfModels, shifts: int = 1, overlap: float = 0.25,
                 split: bool = True, segment: Optional[int] = None, jobs: int = 0):
        self._model, self._audio_channels, self._samplerate = model, model.audio_channels, model.samplerate
        device = "cuda" if is_cuda_available() else "mps" if torch.backends.mps.is_available() else "cpu"
        self.update_parameter(device=device, shifts=shifts, overlap=overlap, split=split,
                            segment=segment, jobs=jobs, progress=True, callback=None, callback_arg=None)


def _to_pcm_frames(wav: torch.Tensor):
    """(C, T) tensor -> contiguous (T, C) int16 PCM, NaN/Inf sanitized."""
    wav = prevent_clip(wav.detach().cpu(), mode="rescale")
    wav = torch.nan_to_num(wav.float(), nan=0.0, posinf=1.0, neginf=-1.0)
    pcm = (wav.clamp(-1.0, 1.0) * 32767.0).to(torch.int16)
    return pcm.transpose(0, 1).contiguous().numpy()


def _encode_ffmpeg(frames, path: str, samplerate: int, channels: int, bitrate: int) -> None:
    """Preferred path: stream PCM into ffmpeg/libmp3lame (flat memory)."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "s16le", "-ar", str(samplerate), "-ac", str(channels),
        "-i", "pipe:0",
        "-codec:a", "libmp3lame", "-b:a", f"{bitrate}k",
        str(path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    chunk_frames = samplerate * 60   # 60 s of PCM per write
    stderr = ""
    try:
        for start in range(0, frames.shape[0], chunk_frames):
            proc.stdin.write(frames[start:start + chunk_frames].tobytes())
        proc.stdin.close()
        stderr = proc.stderr.read().decode("utf-8", errors="ignore")
        rc = proc.wait()
    except BaseException as e:
        try:
            stderr = proc.stderr.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        proc.kill()
        raise RuntimeError(
            f"ffmpeg mp3 编码中断: {type(e).__name__}: {e}; ffmpeg: {stderr.strip()}")
    if rc != 0:
        raise RuntimeError(f"ffmpeg mp3 编码失败 (rc={rc}): {stderr.strip()}")


def _encode_lameenc_chunked(frames, path: str, samplerate: int,
                            channels: int, bitrate: int) -> None:
    """Fallback: demucs' original lameenc, but fed 60 s at a time.

    demucs.audio.save_audio() calls encode() ONCE over the whole recording.
    For ~74 min that is ~750 MiB of PCM plus a huge LAME output buffer; under
    GPU/RAM pressure it raises ``RuntimeError: LAME encoding failed``.
    Chunking keeps each encode() small while producing the same mp3 format.
    """
    import lameenc
    enc = lameenc.Encoder()
    enc.set_bit_rate(bitrate)
    enc.set_in_sample_rate(samplerate)
    enc.set_channels(channels)
    enc.set_quality(2)
    enc.silence()
    chunk_frames = samplerate * 60
    with open(path, "wb") as f:
        for start in range(0, frames.shape[0], chunk_frames):
            f.write(enc.encode(frames[start:start + chunk_frames].tobytes()))
        f.write(enc.flush())


def _save_mp3_stream(wav: torch.Tensor, path: str, samplerate: int, bitrate: int = 128) -> None:
    """Save a (C, T) tensor as mp3 without the fragile single-shot lameenc call.

    Output format stays exactly what the rest of VideoLingo expects
    (vocal.mp3 / background.mp3), and _2_asr.py's format="mp3" is unaffected.
    """
    frames = _to_pcm_frames(wav)
    channels = int(frames.shape[1])
    try:
        _encode_ffmpeg(frames, path, samplerate, channels, bitrate)
    except Exception as e:
        rprint(f"[yellow]⚠️ ffmpeg 编码失败，回退分块 lameenc: {e}[/yellow]")
        _encode_lameenc_chunked(frames, path, samplerate, channels, bitrate)


def demucs_audio():
    if os.path.exists(_VOCAL_AUDIO_FILE) and os.path.exists(_BACKGROUND_AUDIO_FILE):
        rprint(f"[yellow]⚠️ {_VOCAL_AUDIO_FILE} and {_BACKGROUND_AUDIO_FILE} already exist, skip Demucs processing.[/yellow]")
        return

    console = Console()
    os.makedirs(_AUDIO_DIR, exist_ok=True)

    console.print("🤖 Loading <htdemucs> model...")
    model = get_model('htdemucs')
    separator = PreloadedSeparator(model=model, shifts=1, overlap=0.25)

    console.print("🎵 Separating audio...")
    _, outputs = separator.separate_audio_file(_RAW_AUDIO_FILE)

    console.print("🎤 Saving vocals track...")
    _save_mp3_stream(outputs['vocals'].cpu(), _VOCAL_AUDIO_FILE,
                     samplerate=model.samplerate, bitrate=128)

    console.print("🎹 Saving background music...")
    background = sum(audio for source, audio in outputs.items() if source != 'vocals')
    _save_mp3_stream(background.cpu(), _BACKGROUND_AUDIO_FILE,
                     samplerate=model.samplerate, bitrate=128)

    # Clean up memory
    del outputs, background, model, separator
    gc.collect()

    console.print("[green]✨ Audio separation completed![/green]")


if __name__ == "__main__":
    demucs_audio()
