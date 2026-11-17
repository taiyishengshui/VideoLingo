"""Unit tests for the batched ffmpeg merge graph builder (v1.31 / FR-40).

These are pure-string checks: no ffmpeg subprocess, no pydub decode, so they
run in the lightweight test env. The point is to guarantee that silence gaps
(needed for subtitle alignment) are inserted BEFORE each non-leading segment,
that each gap owns an EXCLUSIVE silence branch (FR-40), and that the per-batch
command stays short enough for Windows.
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
    assert "asplit" not in g
    assert "[S0]" not in g
    assert "[SIL0]" not in g
    assert "concat=n=2:v=0:a=1[out]" in g


def test_positive_gaps_emit_silence_before_each_clip():
    # leading gap (start0=1.0) + between-gap → 2 clips + 2 silences (silence-first)
    segs = _segs(("a0.wav", 1.0, 2.0), ("a1.wav", 3.0, 4.0))
    g, pe, has_gap = build_batch_filtergraph(segs, 0.0)
    assert has_gap is True
    # FR-40: two gaps must consume two EXCLUSIVE branches, not one shared [SIL0].
    assert "[SIL0]" not in g
    assert "asplit=2[S0][S1]" in g
    assert g.count("[S0]atrim=") == 1
    assert g.count("[S1]atrim=") == 1
    assert "[S0]atrim=0:1.000" in g
    assert "[S1]atrim=0:1.000" in g
    assert "concat=n=4:v=0:a=1[out]" in g
    assert "[sil0][clip0]" in g
    assert "[sil1][clip1]" in g


def test_zero_leading_and_zero_gap_silence_skipped():
    # start0 == 0 → no leading silence; gap 2.0->2.0 == 0 → skipped.
    # only the 4.0->5.0 gap for a2 emits one silence.
    segs = _segs(("a0.wav", 0.0, 2.0), ("a1.wav", 2.0, 4.0), ("a2.wav", 5.0, 6.0))
    g, pe, has_gap = build_batch_filtergraph(segs, 0.0)
    assert has_gap is True
    # exactly one gap → K=1, no asplit needed (direct [S0] branch)
    assert "asplit" not in g
    assert "[S0]atrim=0:1.000" in g
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
    assert "[S0]atrim=0:2.000" in g1
    assert "[sil0][clip0]concat=n=2" in g1
    assert pe1 == 8.0


def test_each_gap_gets_exclusive_silence_branch():
    # FR-40 regression: 3 gaps of different lengths with a gap-less segment
    # in between. The old graph reused one [SIL0] for all three atrim filters.
    segs = _segs(
        ("a0.wav", 0.5, 1.5),   # leading gap 0.5 → branch 0
        ("a1.wav", 1.5, 2.5),   # no gap
        ("a2.wav", 4.5, 5.5),   # gap 2.0 → branch 1
        ("a3.wav", 6.0, 7.0),   # gap 0.5 → branch 2
    )
    g, pe, has_gap = build_batch_filtergraph(segs, 0.0)
    assert has_gap is True
    assert pe == 7.0
    assert "asplit=3[S0][S1][S2]" in g
    assert "[SIL0]" not in g
    for k in range(3):
        assert g.count("[S%d]atrim=" % k) == 1
    assert "[S0]atrim=0:0.500" in g
    assert "[S1]atrim=0:2.000" in g
    assert "[S2]atrim=0:0.500" in g
    # gap-less segment keeps its clip without a silence node
    assert "[sil0][clip0][clip1][sil2][clip2][sil3][clip3]concat=n=7" in g


def test_single_gap_uses_direct_silence_branch():
    # K=1: no asplit, one [S0] branch; the gap still belongs to segment 1.
    segs = _segs(("a0.wav", 0.0, 2.0), ("a1.wav", 5.0, 6.0))
    g, pe, has_gap = build_batch_filtergraph(segs, 0.0)
    assert has_gap is True
    assert "asplit" not in g
    assert "[S0]atrim=0:3.000" in g
    assert "[sil0]" not in g
    assert "[sil1][clip1]concat=n=3" in g
    assert pe == 6.0


def test_batch_size_constant_reasonable():
    # Keep each ffmpeg command well under Windows' ~32767 char limit.
    assert BATCH_SIZE <= 200
    assert BATCH_SIZE >= 10
