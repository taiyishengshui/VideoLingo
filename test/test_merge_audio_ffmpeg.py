"""Integration test for the batched ffmpeg merge path.

Requires a real `ffmpeg` binary on PATH (skipped otherwise). Builds tiny
WAV segments, runs `merge_audio_segments_ffmpeg`, and asserts a valid dub file
is produced. Catches the regressions we hit on Windows:
  - filter_complex named output needs `-map "[out]"` (else
    "concat:out:a0 has output 0 (out) unconnected")
  - the final stitch must not rely on the concat-demuxer list file
    (fragile path/quoting handling on gyan.dev ffmpeg)
  - FR-40: silence gaps must be ACTUALLY silent. A graph-string assertion
    cannot detect this, and silent test WAVs hide it entirely, so the FR-40
    case uses NON-silent tone WAVs and measures PCM RMS inside every gap.
"""
import array
import math
import os
import shutil
import subprocess
import wave

import pytest

import core._11_merge_audio as mod
from core._11_merge_audio import merge_audio_segments_ffmpeg

ffmpeg = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(ffmpeg is None, reason="ffmpeg not on PATH")


def _make_silent_wav(path: str, seconds: float, sr: int = 16000) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=mono", "-t", str(seconds), path],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _make_tone_wav(path: str, seconds: float, freq: int = 440, sr: int = 16000) -> None:
    """A NON-silent clip — required to expose audio leaking into a gap."""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i",
         f"sine=frequency={freq}:sample_rate={sr}:duration={seconds}",
         "-ac", "1", "-ar", str(sr), path],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _rms(path: str, lo: float, hi: float) -> float:
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        data = array.array("h")
        data.frombytes(w.readframes(w.getnframes()))
    seg = data[int(lo * rate):int(hi * rate)]
    return math.sqrt(sum(v * v for v in seg) / len(seg)) if seg else 0.0


def test_merge_audio_segments_ffmpeg_real(tmp_path, monkeypatch):
    # 5 segments with silence gaps between some of them
    specs = [(0.0, 1.0), (1.3, 2.0), (2.0, 3.0), (3.5, 4.0), (4.0, 5.0)]
    segs = []
    for i, (s, e) in enumerate(specs):
        p = os.path.join(str(tmp_path), f"seg_{i}.wav")
        _make_silent_wav(p, e - s)
        segs.append((p, s, e))

    out = os.path.join(str(tmp_path), "dub.mp3")
    monkeypatch.setattr(mod, "DUB_VOCAL_FILE", out)

    ok = merge_audio_segments_ffmpeg(segs)
    assert ok is True
    assert os.path.exists(out)
    # 5s of 16k/mono @64k mp3 should be well above a few KB
    assert os.path.getsize(out) > 1024


def test_merge_audio_segments_ffmpeg_missing_first(tmp_path, monkeypatch):
    # First segment missing -> should bail out (return False), not crash
    specs = [(0.0, 1.0), (1.0, 2.0)]
    segs = []
    for i, (s, e) in enumerate(specs):
        p = os.path.join(str(tmp_path), f"seg_{i}.wav")
        if i != 0:
            _make_silent_wav(p, e - s)
        segs.append((p, s, e))

    out = os.path.join(str(tmp_path), "dub.mp3")
    monkeypatch.setattr(mod, "DUB_VOCAL_FILE", out)

    assert merge_audio_segments_ffmpeg(segs) is False


def test_silence_branches_are_really_silent(tmp_path):
    """FR-40 regression: with non-silent clips, every gap must be true silence.

    The old graph let several `atrim` filters share one `[SIL0]` label; ffmpeg
    kept only the first branch silent and fed the rest the first clip's audio,
    so gaps replayed the opening line ("男人在亲热一晚后" / "男人").
    """
    specs = [(0.0, 1.0), (1.8, 2.8), (4.8, 5.8), (6.2, 7.2)]
    segs = []
    for i, (s, e) in enumerate(specs):
        p = os.path.join(str(tmp_path), f"tone_{i}.wav")
        _make_tone_wav(p, e - s)
        segs.append((p, s, e))

    graph, pe, has_gap = mod.build_batch_filtergraph(segs, 0.0)
    assert has_gap is True
    assert pe == 7.2
    # 3 gaps of different lengths → 3 exclusive silence branches
    assert "asplit=3[S0][S1][S2]" in graph

    out = os.path.join(str(tmp_path), "batch.wav")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for p, _, _ in segs:
        cmd += ["-i", p]
    cmd += ["-f", "lavfi", "-i", f"anullsrc=r={mod.SAMPLE_RATE}:cl=mono",
            "-filter_complex", graph, "-map", "[out]",
            "-ar", str(mod.SAMPLE_RATE), "-ac", "1", out]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    for lo, hi in [(0.0, 1.0), (1.8, 2.8), (4.8, 5.8), (6.2, 7.2)]:
        assert _rms(out, lo, hi) > 1000, f"clip {lo}-{hi}s should carry audio"
    for lo, hi in [(1.05, 1.75), (2.85, 4.75), (5.85, 6.15)]:
        assert _rms(out, lo, hi) < 50, f"gap {lo}-{hi}s must be silent (FR-40)"
