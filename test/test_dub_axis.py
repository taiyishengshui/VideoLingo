"""FR-41 dubbing-axis guard — pure logic, no ffmpeg / GPU / real audio.

The guard only moves a chunk's LAST row when its source window is much longer
than its dubbing and real speech is still audible in the window tail. Every
other row must keep the legacy accumulated timeline byte-for-byte.
"""
from __future__ import annotations

import array
import math

import pytest

from core.services import dub_axis_service as axis


# --- should_shift: the three guards ----------------------------------------

def test_should_shift_matches_the_real_8min_case():
    # src window 12.32 s, dubbing 4.946 s, head rms 3831, tail rms 4084
    assert axis.should_shift(src_dur=12.32, dub_dur=4.946,
                             front_rms=3831.0, back_rms=4084.0) is True


def test_should_shift_rejects_non_last_row_and_disabled_switch():
    kw = dict(src_dur=12.32, dub_dur=4.946, front_rms=3831.0, back_rms=4084.0)
    assert axis.should_shift(is_chunk_last=False, **kw) is False
    assert axis.should_shift(enabled=False, **kw) is False


def test_should_shift_rejects_small_window_surplus():
    assert axis.should_shift(src_dur=6.0, dub_dur=4.5,
                             front_rms=4000.0, back_rms=4200.0) is False
    assert axis.should_shift(src_dur=7.0, dub_dur=4.946,  # surplus 2.054
                             front_rms=4000.0, back_rms=4200.0) is True


def test_should_shift_rejects_ordinary_line_with_quiet_tail():
    # Normal sentence: real speech at the head, silence at the tail. Moving
    # the dubbing later here would make it worse, not better.
    assert axis.should_shift(src_dur=9.0, dub_dur=3.0,
                             front_rms=4200.0, back_rms=120.0) is False


def test_should_shift_rejects_tail_below_voice_floor():
    assert axis.should_shift(src_dur=9.0, dub_dur=3.0,
                             front_rms=50.0, back_rms=120.0) is False


# --- shift_sub_times: translate, never reshape ------------------------------

def test_shift_sub_times_translates_all_pairs_and_returns_builtin_floats():
    pairs = [[482.269, 485.0], [485.0, 487.214]]
    out = axis.shift_sub_times(pairs, 3.827)
    assert out == [[486.096, 488.827], [488.827, 491.041]]
    assert all(type(v) is float for row in out for v in row)
    assert [b - a for a, b in out] == [b - a for a, b in pairs]
    assert pairs == [[482.269, 485.0], [485.0, 487.214]]  # input untouched


def test_shift_sub_times_zero_delta_is_a_no_op():
    pairs = [[1.0, 2.0]]
    assert axis.shift_sub_times(pairs, 0.0) == [[1.0, 2.0]]


# --- window_edge_rms: injectable I/O, correct windows -----------------------

def test_window_edge_rms_queries_head_and_tail_thirds():
    calls = []

    def fake_rms(path, start, duration):
        calls.append((path, round(start, 4), round(duration, 4)))
        return 100.0 if len(calls) == 1 else 200.0

    front, back = axis.window_edge_rms("vocal.mp3", 478.722, 491.042, fake_rms)
    assert (front, back) == (100.0, 200.0)
    assert len(calls) == 2
    edge = (491.042 - 478.722) / 3
    assert calls[0][1] == pytest.approx(478.722, abs=1e-3)
    assert calls[0][2] == pytest.approx(edge, abs=1e-3)
    assert calls[1][1] == pytest.approx(491.042 - edge, abs=1e-3)
    assert calls[1][2] == pytest.approx(edge, abs=1e-3)


def test_window_edge_rms_handles_degenerate_window():
    assert axis.window_edge_rms("v.mp3", 10.0, 10.0,
                                lambda p, s, d: 0.0) == (0.0, 0.0)


def test_decode_rms_computes_from_pcm_bytes(monkeypatch):
    samples = array.array("h", [1000, -1000, 2000, -2000])

    class _Proc:
        stdout = samples.tobytes()
        stderr = b""

    monkeypatch.setattr(axis.subprocess, "run",
                        lambda *a, **k: _Proc())
    got = axis.decode_rms("v.mp3", 5.0, 0.5)
    assert got == pytest.approx(math.sqrt((1000**2 * 2 + 2000**2 * 2) / 4))
    assert isinstance(got, float)


def test_decode_rms_missing_ffmpeg_is_not_fatal(monkeypatch):
    def boom(*a, **k):
        raise OSError("ffmpeg not found")

    monkeypatch.setattr(axis.subprocess, "run", boom)
    assert axis.decode_rms("v.mp3", 0.0, 1.0) == 0.0
    assert axis.decode_rms("v.mp3", 0.0, 0.0) == 0.0


# --- axis_delta: short-circuit before any I/O -------------------------------

def test_axis_delta_short_circuits_without_decoding_audio():
    def must_not_run(*a, **k):
        raise AssertionError("no ffmpeg for a normal row")

    assert axis.axis_delta(src_start=100.0, src_end=105.0, dub_dur=4.0,
                           cur_time=104.0, chunk_end_time=105.0,
                           audio_file="v.mp3", rms_fn=must_not_run) == 0.0


def test_axis_delta_returns_exact_right_alignment():
    def fake_rms(path, start, duration):
        return 3831.0 if start < 486.0 else 4084.0

    delta = axis.axis_delta(src_start=478.722, src_end=491.042, dub_dur=4.946,
                            cur_time=487.215, chunk_end_time=491.042,
                            audio_file="v.mp3", rms_fn=fake_rms)
    assert delta == pytest.approx(3.827, abs=1e-3)
    shifted = axis.shift_sub_times([[482.269, 487.215]], delta)
    assert shifted[0][1] == pytest.approx(491.042, abs=1e-3)
    assert shifted[0][0] == pytest.approx(486.096, abs=1e-3)


def test_axis_delta_no_shift_when_tail_is_quiet():
    delta = axis.axis_delta(src_start=478.722, src_end=491.042, dub_dur=4.946,
                            cur_time=487.215, chunk_end_time=491.042,
                            audio_file="v.mp3",
                            rms_fn=lambda p, s, d: 4000.0 if s < 486 else 100.0)
    assert delta == 0.0


def test_axis_delta_disabled_switch_and_no_negative_delta():
    loud = lambda p, s, d: 4000.0  # noqa: E731
    assert axis.axis_delta(src_start=0.0, src_end=12.0, dub_dur=4.0,
                           cur_time=4.0, chunk_end_time=12.0,
                           audio_file="v.mp3", enabled=False,
                           rms_fn=loud) == 0.0
    # A chunk that already overran its end must not be moved backwards.
    assert axis.axis_delta(src_start=0.0, src_end=12.0, dub_dur=4.0,
                           cur_time=13.0, chunk_end_time=12.0,
                           audio_file="v.mp3", rms_fn=loud) == 0.0
