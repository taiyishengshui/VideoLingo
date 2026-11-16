"""Unit tests for the batched ffmpeg merge graph builder (v1.31).

These are pure-string checks: no ffmpeg subprocess, no pydub decode, so they
run in the lightweight test env. The point is to guarantee that silence gaps
(needed for subtitle alignment) are inserted BEFORE each non-leading segment
and that the per-batch command stays short enough for Windows.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core._11_merge_audio import build_batch_filtergraph, BATCH_SIZE


def _segs(*triples):
    # triples: (file, start, end)
    return [(f, float(s), float(e)) for (f, s, e) in triples]


def test_no_gap_no_anullsrc():
    # back-to-back segments → no silence, no anullsrc
    segs = _segs(("a0.wav", 0.0, 2.0), ("a1.wav", 2.0, 4.0))
    g, pe, has_gap = build_batch_filtergraph(segs, 0.0)
    assert has_gap is False
    assert "anullsrc" not in g
    assert "[SIL0]" not in g
    assert "concat=n=2:v=0:a=1[out]" in g


def test_positive_gaps_emit_silence_before_each_clip():
    # leading gap (start0=1.0) + between-gap → 2 clips + 2 silences (silence-first)
    segs = _segs(("a0.wav", 1.0, 2.0), ("a1.wav", 3.0, 4.0))
    g, pe, has_gap = build_batch_filtergraph(segs, 0.0)
    assert has_gap is True
    # anullsrc input is normalized to [SIL0] inside the graph; the literal
    # "anullsrc" only appears in the ffmpeg command, not the graph string.
    assert "[SIL0]" in g
    assert "concat=n=4:v=0:a=1[out]" in g
    assert "[sil0][clip0]" in g
    assert "[sil1][clip1]" in g


def test_zero_leading_and_zero_gap_silence_skipped():
    # start0 == 0 → no leading silence; gap 2.0->2.0 == 0 → skipped.
    # only the 4.0->5.0 gap for a2 emits one silence.
    segs = _segs(("a0.wav", 0.0, 2.0), ("a1.wav", 2.0, 4.0), ("a2.wav", 5.0, 6.0))
    g, pe, has_gap = build_batch_filtergraph(segs, 0.0)
    assert has_gap is True
    assert "concat=n=4:v=0:a=1[out]" in g
    assert "[sil0][clip0]" not in g
    assert "[sil1][clip1]" not in g
    assert "[sil2][clip2]" in g


def test_chain_order_silence_first():
    segs = _segs(("a0.wav", 1.0, 2.0), ("a1.wav", 3.0, 4.0))
    g, pe, has_gap = build_batch_filtergraph(segs, 0.0)
    # silence precedes its own clip (correct for timeline alignment)
    assert "[sil0][clip0][sil1][clip1]concat=n=4" in g


def test_prev_end_carries_across_batches():
    # batch1 starts where batch0 left off; gap is the inter-batch lead-in
    segs0 = _segs(("a0.wav", 0.0, 2.0), ("a1.wav", 2.0, 4.0))
    g0, pe0, _ = build_batch_filtergraph(segs0, 0.0)
    assert pe0 == 4.0
    segs1 = _segs(("a2.wav", 6.0, 8.0))  # 2s gap from prev_end=4.0
    g1, pe1, has_gap = build_batch_filtergraph(segs1, pe0)
    assert has_gap is True
    assert "[sil0][clip0]concat=n=2" in g1
    assert pe1 == 8.0


def test_batch_size_constant_reasonable():
    # Keep each ffmpeg command well under Windows' ~32767 char limit.
    assert BATCH_SIZE <= 200
    assert BATCH_SIZE >= 10
