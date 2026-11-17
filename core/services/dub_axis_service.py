"""FR-41 — single decision for dubbing-axis placement.

Why this exists
---------------
``core/_10_gen_audio.py: merge_chunks()`` lays a chunk's dubbing out from the
first row's ``start_time``, accumulating each row's wav duration. The
``chunk_end_time`` bound only caps the END of the chunk; nothing requires the
dubbing to reach it. A row whose source window is much longer than its short
translated line therefore leaves a silent tail — and that tail can land
exactly on the real English speech (the 8:02 case: dubbing 482.269-487.214,
real speech 487.8-491.0, source window 478.722-491.042).

This module decides, for a chunk's LAST row only, whether to right-align that
row's dubbing to ``chunk_end_time`` (moving the idle silence to the FRONT of
the window, where the 11th step already inserts real silence via its
``asplit=K`` branches). Every other row keeps the legacy accumulated axis
byte-for-byte.

Pure logic (:func:`should_shift`, :func:`shift_sub_times`) is separated from
I/O (:func:`decode_rms`, :func:`window_edge_rms`) so unit tests need neither
ffmpeg nor a real audio file. Stdlib-only, importable without pydub/torch.
"""
from __future__ import annotations

import array
import math
import subprocess
from typing import Callable

SAMPLE_RATE = 8000
MIN_WINDOW_SURPLUS = 2.0       # source window - dubbing duration, seconds
BACK_VOICE_RATIO = 0.8         # tail rms must stay comparable to head rms
VOICE_RMS_FLOOR = 300.0        # tail must actually contain speech
EDGE_FRACTION = 1.0 / 3.0      # compare first / last third of the window


def should_shift(*, src_dur: float, dub_dur: float,
                 front_rms: float, back_rms: float,
                 is_chunk_last: bool = True, enabled: bool = True) -> bool:
    """Pure decision: only extreme rows with real speech in the window tail.

    Three guards, in order:
      1. only a chunk's last row, and only when the switch is on;
      2. the source window must be >2 s longer than its dubbing;
      3. the tail must be audible and not much quieter than the head.

    Guard 3 is what rejects ordinary lines whose real speech is at the FRONT:
    their tail is quiet, so right-aligning would make the dubbing later, not
    better. It deliberately does NOT try to pick "the real speech" by energy
    magnitude — the ad segment in the 8:02 case measured rms 3831 vs 4084 for
    the real line, so energy alone cannot separate them.
    """
    if not enabled or not is_chunk_last:
        return False
    if src_dur - dub_dur <= MIN_WINDOW_SURPLUS:
        return False
    if back_rms < VOICE_RMS_FLOOR:
        return False
    return back_rms >= front_rms * BACK_VOICE_RATIO


def shift_sub_times(pairs, delta: float):
    """Translate every sub-line of one row; return builtin-float pairs.

    Callers must feed the result through ``plain_time_pairs`` so the Excel
    cell stays a plain numeric literal (v1.27 numpy-scalar contract).
    """
    d = float(delta)
    return [[float(a) + d, float(b) + d] for a, b in pairs]


def decode_rms(audio_file: str, start: float, duration: float,
               sample_rate: int = SAMPLE_RATE) -> float:
    """Decode ONE small window with ffmpeg — never hold the whole track.

    16 GB RAM / 6C12T box: decoding only the two edge thirds of a candidate
    row keeps memory flat. A missing file / missing ffmpeg is NOT a fatal
    error: it simply means no shift, i.e. the legacy axis is preserved.
    """
    if duration <= 0:
        return 0.0
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}",
        "-i", audio_file, "-t", f"{duration:.3f}",
        "-f", "s16le", "-ac", "1", "-ar", str(sample_rate), "-",
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False)
    except OSError:
        return 0.0
    raw = proc.stdout[:len(proc.stdout) // 2 * 2]
    if not raw:
        return 0.0
    samples = array.array("h")
    samples.frombytes(raw)
    if not samples:
        return 0.0
    return math.sqrt(sum(v * v for v in samples) / len(samples))


def window_edge_rms(audio_file: str, src_start: float, src_end: float,
                    rms_fn: Callable[[str, float, float], float] | None = None):
    """Head/tail rms of one source window. ``rms_fn`` is injectable in tests."""
    span = max(0.0, float(src_end) - float(src_start))
    edge = max(0.1, span * EDGE_FRACTION)
    fn = rms_fn or decode_rms
    front = fn(audio_file, float(src_start), edge)
    back = fn(audio_file, max(float(src_start), float(src_end) - edge), edge)
    return float(front), float(back)


def axis_delta(*, src_start: float, src_end: float, dub_dur: float,
               cur_time: float, chunk_end_time: float, audio_file: str,
               enabled: bool = True,
               rms_fn: Callable[[str, float, float], float] | None = None):
    """How far to move a chunk's last row, or 0.0 for the legacy axis.

    Short-circuits on the duration guard BEFORE spawning ffmpeg, so normal
    rows cost nothing. Never returns a negative delta: a chunk that already
    overran its end is handled by the existing trim/exception path.
    """
    if not enabled:
        return 0.0
    src_dur = max(0.0, float(src_end) - float(src_start))
    if src_dur - float(dub_dur) <= MIN_WINDOW_SURPLUS:
        return 0.0
    front, back = window_edge_rms(audio_file, src_start, src_end, rms_fn)
    if not should_shift(src_dur=src_dur, dub_dur=float(dub_dur),
                        front_rms=front, back_rms=back):
        return 0.0
    return max(0.0, float(chunk_end_time) - float(cur_time))
