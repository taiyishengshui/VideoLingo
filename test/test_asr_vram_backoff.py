"""VRAM backoff helpers for the local WhisperX ASR backend.

Covers the two knobs that decide whether a segment survives on a crowded GPU:

  * `_vad_batch_size_for` — maps free VRAM to a pyannote VAD batch size;
  * `_apply_vad_batch_size` / `_vad_to_cpu` — reach into the already-built
    whisperx VAD pipeline and retune it (whisperx hardcodes batch_size=32 and
    exposes no way to change it, so this is the only handle we have).

The VAD is what actually OOM'd in production: it raises `MemoryError` (not
`RuntimeError`) and ignores the whisper `batch_size` argument entirely.

Run from the repo root:  python -m pytest test/test_asr_vram_backoff.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from core.asr_backend import whisperX_local as wx
except Exception as e:  # torch/whisperx/pyannote not installed here
    pytest.skip(f"whisperx stack not importable: {e}", allow_module_level=True)


class _Inference:
    def __init__(self, batch_size=32):
        self.batch_size = batch_size
        self.pre_aggregation_hook = lambda x: x


class _Pipeline:
    def __init__(self):
        self._segmentation = _Inference()


class _Vad:
    def __init__(self):
        self.vad_pipeline = _Pipeline()


class _Model:
    def __init__(self):
        self.vad_model = _Vad()


@pytest.fixture(autouse=True)
def _isolated_run_state(monkeypatch):
    """Keep the process-wide ratchet from leaking between tests."""
    monkeypatch.setattr(
        wx, "_RUN_STATE",
        {"last_good_batch": None, "cpu_fallback": False,
         "vad_batch": None, "vad_on_cpu": False},
    )


def test_vad_batch_size_scales_with_free_vram():
    assert wx._vad_batch_size_for(8.0) == 32
    assert wx._vad_batch_size_for(4.0) == 32
    assert wx._vad_batch_size_for(3.0) == 16
    assert wx._vad_batch_size_for(2.5) == 16
    assert wx._vad_batch_size_for(1.5) == 8
    assert wx._vad_batch_size_for(0.8) == 4
    assert wx._vad_batch_size_for(0.2) == 2


def test_vad_batch_size_is_monotonic():
    sizes = [wx._vad_batch_size_for(g / 10) for g in range(1, 60)]
    assert sizes == sorted(sizes, reverse=True)


def test_apply_vad_batch_size_rewrites_inference():
    model = _Model()
    assert wx._apply_vad_batch_size(model, 8) is True
    assert model.vad_model.vad_pipeline._segmentation.batch_size == 8
    assert wx._RUN_STATE["vad_batch"] == 8


def test_apply_vad_batch_size_never_raises_on_unexpected_layout():
    class _Bare:
        pass

    assert wx._apply_vad_batch_size(_Bare(), 8) is False
    assert wx._apply_vad_batch_size(None, 8) is False


def test_vad_to_cpu_false_when_vad_missing():
    class _Bare:
        pass

    assert wx._vad_to_cpu(_Bare()) is False
    assert wx._vad_to_cpu(None) is False
