"""Integration test for the batched ffmpeg merge path.

Requires a real `ffmpeg` binary on PATH (skipped otherwise). Builds tiny
silent WAV segments, runs `merge_audio_segments_ffmpeg`, and asserts a valid
dub file is produced. Catches the two regressions we hit on Windows:
  - filter_complex named output needs `-map "[out]"` (else
    "concat:out:a0 has output 0 (out) unconnected")
  - the final stitch must not rely on the concat-demuxer list file
    (fragile path/quoting handling on gyan.dev ffmpeg).
"""
import os
import shutil
import subprocess

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
