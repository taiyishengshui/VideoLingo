"""Alignment of source sentences to word-level timestamps.

Regression tests for core.utils.subtitle_time.get_sentence_timestamps.

The old implementation scanned the concatenated ASR words for each sentence
as an exact character substring with a single global cursor. Any divergence
between the sentence text and the word tokens (punctuation, casing, an ASR
correction) desynced the cursor and raised ValueError, killing the whole run.

The rewrite projects each sentence onto word rows by WORD-TOKEN COUNT (both
sides measured with the same tokenizer), which is robust to those divergences
and degrades gracefully (warns + clamps to the tail) instead of raising.

Rows are grouped as [a row carrying tokens] + [any following empty rows], and a
group is consumed as a unit. That handles two cases at once:

  * BLANK rows from asr_correct MERGE fixes (a garbled "finsuchaway" corrected
    to "in such a way" blanks the 3 consumed rows but keeps row count and every
    timecode) — the merged token keeps its FULL audio span;
  * punctuation-only rows ("—") carry 0 tokens, so they ride along with a
    neighbour instead of eating one of the sentence's tokens.

Counting tokens (not rows) also keeps long runs in sync: the old row-count rule
treated a merged row as 1, so every sentence over-consumed rows and the tail
ran dry — see test_merged_word_does_not_starve_the_tail.

Run from the repo root:  python -m pytest test/test_subtitle_time.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from core.utils.subtitle_time import _find_anchor, get_sentence_timestamps


def _words(texts, starts, ends):
    return pd.DataFrame({"text": texts, "start": starts, "end": ends})


def _sent(sources):
    return pd.DataFrame({"Source": sources})


def test_normal_alignment():
    words = _words(
        ["This", "videotape", "is", "edited", "from", "an", "advanced"],
        [0.0, 0.2, 0.5, 0.6, 0.9, 1.0, 1.1],
        [0.2, 0.5, 0.6, 0.9, 1.0, 1.1, 1.6],
    )
    sent = _sent(["This videotape is edited", "from an advanced"])
    ts = get_sentence_timestamps(words, sent)
    assert len(ts) == 2
    assert ts[0] == (0.0, 0.9)
    assert ts[1] == (0.9, 1.6)


def test_case_punctuation_divergence_no_raise():
    # Old code would desync on the apostrophe / casing and hard-fail.
    words = _words(
        ["I", "like", "tearing", "belief's", "apart"],
        [0.0, 0.1, 0.3, 0.6, 0.9],
        [0.1, 0.3, 0.6, 0.9, 1.2],
    )
    sent = _sent(["I LIKE tearing belief's apart"])
    ts = get_sentence_timestamps(words, sent)
    assert len(ts) == 1
    assert ts[0] == (0.0, 1.2)


def test_corrected_word_no_crash():
    # ASR correction changed "belief's" -> "beliefs" in the word store.
    words = _words(
        ["I", "like", "tearing", "beliefs", "apart"],
        [0.0, 0.1, 0.3, 0.6, 0.9],
        [0.1, 0.3, 0.6, 0.9, 1.2],
    )
    sent = _sent(["I like tearing belief's apart"])
    ts = get_sentence_timestamps(words, sent)
    assert len(ts) == 1
    assert ts[0] == (0.0, 1.2)


def test_severe_divergence_clamps_no_raise():
    words = _words(["one", "two", "three"], [0.0, 0.5, 1.0], [0.5, 1.0, 1.5])
    sent = _sent(["one two three four five", "six seven"])
    ts = get_sentence_timestamps(words, sent)
    assert len(ts) == 2
    assert ts[1] == (1.0, 1.5)  # clamped to tail
    # Both sentences clamp to the tail, so sentence 0 would otherwise run to
    # 1.5 — past where sentence 1 starts (1.0). Overlap guard trims it back.
    assert ts[0] == (0.0, 1.0)


def test_punctuation_only_token_ignored():
    words = _words(["hello", "—", "world"], [0.0, 0.3, 0.3], [0.3, 0.3, 0.6])
    sent = _sent(["hello world"])
    ts = get_sentence_timestamps(words, sent)
    assert len(ts) == 1
    assert ts[0] == (0.0, 0.6)


def test_merge_fix_consumes_blank_rows_full_span():
    # The exact bug: asr_correct merges a garbled token into "in such a way",
    # blanking the 3 consumed rows. Row count + timecodes stay identical, so
    # the source sentence must consume all 7 rows (1 text + 3 blank + that +
    # you + slowly) and the span must reach the LAST row's end (2.1) — the
    # full merged audio, not collapsed to row 0's end (0.3).
    words = _words(
        ["in such a way", "", "", "", "that", "you", "slowly"],
        [0.0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8],
        [0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.1],
    )
    sent = _sent(["in such a way that you slowly"])  # 7 tokens -> rows 0..6
    ts = get_sentence_timestamps(words, sent)
    assert len(ts) == 1
    assert ts[0] == (0.0, 2.1)


def test_merge_then_next_sentence_continues_after_blanks():
    # Merge row at index 0 (4 rows incl. 3 blanks) must not leak into the next
    # sentence; the following sentence starts consuming at row 6.
    words = _words(
        ["in such a way", "", "", "", "that", "you", "slowly", "expand"],
        [0.0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.1],
        [0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.1, 2.4],
    )
    sent = _sent(["in such a way that you", "slowly expand"])
    ts = get_sentence_timestamps(words, sent)
    assert len(ts) == 2
    assert ts[0] == (0.0, 1.8)   # 6 tokens -> rows 0..5
    assert ts[1] == (1.8, 2.4)   # 2 tokens -> rows 6..7 (no leak)


def test_merge_alone_keeps_full_audio_span():
    # A sentence that is exactly the merged token must keep the audio of all
    # 4 rows (1 text + 3 blank), not collapse to the first row's end (0.3).
    words = _words(
        ["in such a way", "", "", ""],
        [0.0, 0.3, 0.6, 0.9],
        [0.3, 0.6, 0.9, 1.2],
    )
    sent = _sent(["in such a way"])
    ts = get_sentence_timestamps(words, sent)
    assert ts[0] == (0.0, 1.2)


def test_merged_word_does_not_starve_the_tail():
    # Regression: counting a merged row as ONE row (old row-count rule) made
    # every sentence over-consume rows, so the last sentences ran out and got
    # clamped on top of each other. Counting word TOKENS keeps it in sync.
    words = _words(
        ["alpha", "in such a way", "", "", "", "omega"],
        [0.0, 0.5, 0.8, 1.1, 1.4, 1.7],
        [0.5, 0.8, 1.1, 1.4, 1.7, 2.0],
    )
    sent = _sent(["alpha in such a way", "omega"])
    ts = get_sentence_timestamps(words, sent)
    assert ts == [(0.0, 1.7), (1.7, 2.0)]


def test_empty_word_store_does_not_crash():
    words = _words([], [], [])
    sent = _sent(["anything"])
    assert get_sentence_timestamps(words, sent) == [(0.0, 0.0)]


def test_timestamps_non_decreasing():
    words = _words(["a", "b", "c", "d"], [0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 3.0, 4.0])
    sent = _sent(["a b", "c", "d"])
    ts = get_sentence_timestamps(words, sent)
    for i in range(1, len(ts)):
        assert ts[i][0] >= ts[i - 1][1] - 1e-9


# --- anchor alignment -------------------------------------------------------
# Counting tokens alone lets one rewritten sentence push every later sentence
# later: the cursor only moves forward, so over-eating is never given back.
# Anchoring re-locates each sentence by its own opening words first.

def test_anchor_cancels_over_eaten_words():
    words = _words(
        ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"],
        [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
        [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
    )
    # Sentence 1 carries an extra word the audio does not have, so counting
    # makes it swallow "golf" too; sentence 2 must still start at golf (6.0).
    sent = _sent(["alpha bravo charlie", "delta echo foxtrot EXTRAWORD", "golf hotel"])
    ts = get_sentence_timestamps(words, sent)
    assert ts[0] == (0.0, 3.0)
    assert ts[2] == (6.0, 8.0)
    # over-eating fixed on both sides: sentence 1 no longer runs past golf
    assert ts[1] == (3.0, 6.0)


def test_anchor_never_leaves_a_cue_overlapping_the_next():
    words = _words(["a", "b", "c", "b", "c"], [0.0, 1.0, 2.0, 3.0, 4.0],
                   [1.0, 2.0, 3.0, 4.0, 5.0])
    sent = _sent(["a b c", "b c"])   # sentence 0 eats b,c; sentence 1 rewinds
    ts = get_sentence_timestamps(words, sent)
    assert ts[1] == (3.0, 5.0)
    assert ts[0][1] <= ts[1][0]      # no overlap left behind


def test_find_anchor_prefers_nearest_behind_then_first_ahead():
    flat = ["x", "a", "b", "c", "a", "b", "c"]
    positions = {}
    for i, t in enumerate(flat):
        positions.setdefault(t, []).append(i)
    key = ["a", "b", "c"]
    assert _find_anchor(key, flat, positions, 0, 4, 7) == 4   # exact hit
    assert _find_anchor(key, flat, positions, 0, 6, 7) == 4   # nearest behind
    assert _find_anchor(key, flat, positions, 0, 5, 7) == 4
    assert _find_anchor(key, flat, positions, 5, 6, 7) is None  # behind-window floor


def test_find_anchor_skips_rewritten_opening_word():
    # The splitter prepended "actually" — the audio starts at "quick".
    flat = ["quick", "brown", "fox"]
    positions = {}
    for i, t in enumerate(flat):
        positions.setdefault(t, []).append(i)
    assert _find_anchor(["actually", "quick", "brown"], flat, positions, 0, 0, 3) == 0


def test_anchor_leaves_already_correct_alignment_untouched():
    words = _words(["one", "two", "three", "four"], [0.0, 1.0, 2.0, 3.0],
                   [1.0, 2.0, 3.0, 4.0])
    sent = _sent(["one two", "three four"])
    assert get_sentence_timestamps(words, sent) == [(0.0, 2.0), (2.0, 4.0)]


def test_repeated_sentence_consumes_next_occurrence_not_previous():
    text = "number four be honest number four be honest".split()
    words = _words(text, list(range(8)), list(range(1, 9)))
    assert get_sentence_timestamps(words, _sent(["number four be honest"] * 2)) == [(0, 4), (4, 8)]


def test_whole_sentence_avoids_three_word_prefix_from_previous_cue():
    text = "you can do this later you can do that now".split()
    words = _words(text, list(range(10)), list(range(1, 11)))
    assert get_sentence_timestamps(words, _sent(["you can do this later", "you can do that now"])) == [(0, 5), (5, 10)]


def test_whole_text_normalization_handles_split_contraction_without_drift():
    text = ["I", "didn't", "know", "this", "would", "work", "next", "sentence"]
    words = _words(text, list(range(8)), list(range(1, 9)))
    assert get_sentence_timestamps(words, _sent(["I did n't know this would work", "next sentence"])) == [(0, 6), (6, 8)]


def test_punctuation_only_cue_does_not_consume_following_word():
    words = _words(["hello", "world"], [0, 1], [1, 2])
    assert get_sentence_timestamps(words, _sent(["hello", "—", "world"])) == [(0, 1), (1, 1), (1, 2)]


def test_srt_rounding_does_not_lose_one_millisecond():
    from core.utils.subtitle_time import convert_to_srt_format
    assert convert_to_srt_format(574.86, 599.9996) == "00:09:34,860 --> 00:10:00,000"
