"""Cross-entry decision: how dubbing task-table list cells are written/read.

``output/audio/tts_tasks.xlsx`` stores Python lists as ``str(list)`` in a
single cell (``lines``, ``new_sub_times``). numpy >= 2 changed scalar repr
from ``1.2`` to ``np.float64(1.2)``. Step 10 picks up numpy scalars from the
DataFrame (``gap`` / ``tolerance``) and leaks them into the timeline via
``cur_time += gap / speed_factor``, so the persisted cell becomes
``[[np.float64(90.693), ...]]`` and step 11's bare ``eval()`` dies with
``NameError: name 'np' is not defined``.

This is the single home for both sides of that contract:

* :func:`plain_time_pairs` — normalise time pairs to builtin floats before
  writing, so the cell stays a plain numeric literal;
* :func:`parse_excel_list` — safely read both plain and numpy-2 repr cells,
  so already-generated task tables remain usable without re-running step 10;
* :func:`wav_duration` — read a PCM wav header instead of spawning ffmpeg
  thousands of times (step 10 probes every sub-line several times).

Deliberately stdlib-only: it must be importable without pydub / torch /
soundfile, both in the pipeline and in unit tests.
"""

from __future__ import annotations

import ast
import re

# ``np.float64(1.2)`` / ``np.int64(3)`` — the wrapper is the only problem; the
# inner value is a plain numeric literal, so stripping it is enough for
# ast.literal_eval (no numpy import, no arbitrary code execution).
_NUMPY_SCALAR = re.compile(
    r"np\.(?:float(?:16|32|64|128)|int(?:8|16|32|64)|bool_)\(([^()]*)\)"
)


def plain_time_pairs(pairs):
    """Builtin-float time pairs, so ``str(cell)`` stays a plain literal."""
    return [[float(start), float(end)] for start, end in pairs]


def parse_excel_list(value):
    """Parse an Excel cell holding ``str(list)`` without executing code.

    Non-string values (already a list, or NaN) pass through unchanged,
    matching the old ``eval(v) if isinstance(v, str) else v`` behaviour.
    """
    if not isinstance(value, str):
        return value
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        cleaned = _NUMPY_SCALAR.sub(r"(\1)", value)
        if cleaned == value:
            raise
        return ast.literal_eval(cleaned)


def wav_duration(path: str) -> float:
    """Duration of a PCM wav from its header — no subprocess.

    Step 10 probes ~4 wavs per sub-line (raw TTS output, speed-adjusted
    output, input for retry checks...). ``get_audio_duration`` spawns an
    ffmpeg process just to parse a ``Duration:`` line, which on Windows costs
    far more than reading a 44-byte header. Non-wav / unusual headers fall
    back to that probe so other backends keep working unchanged.
    """
    import wave
    try:
        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            return float(w.getnframes()) / float(rate) if rate else 0.0
    except (wave.Error, OSError, EOFError, ValueError):
        from core.asr_backend.audio_preprocess import get_audio_duration
        return float(get_audio_duration(path))
