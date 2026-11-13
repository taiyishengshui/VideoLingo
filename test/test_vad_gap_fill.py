"""core/asr_backend/vad_gap_fill.py — VAD 漏检补转（通用机制）。

pyannote VAD 偶发把整段真实语音判成非语音 → 那段字幕整段消失。本模块扫描
「有声但 VAD 没覆盖」的空档，绕开 VAD 单独转写补回。测试只覆盖纯逻辑与
ffmpeg 探测；模型相关的编排用假对象走通。
"""

import os
import subprocess
from dataclasses import dataclass

import pytest

from core.asr_backend import vad_gap_fill as vg

AUDIO = os.path.join("output", "audio", "raw.mp3")
_has_audio = os.path.exists(AUDIO)
needs_audio = pytest.mark.skipif(not _has_audio, reason="需要 output/audio/raw.mp3")


def _segs(*pairs):
    return [{"start": a, "end": b, "text": "x"} for a, b in pairs]


# ---------------------------------------------------------------- NoVad

def test_novad_marks_the_whole_input_as_speech():
    class W:
        shape = (1, 16000 * 7)
    spans = vg.NoVad()({"waveform": W(), "sample_rate": 16000})
    assert [(s.start, s.end) for s in spans] == [(0.0, 7.0)]


def test_novad_survives_malformed_input():
    assert vg.NoVad()({}) == []


def test_novad_is_a_vad_subclass():
    """必须是 Vad 子类：whisperx 靠 issubclass 决定用谁的 preprocess/merge。

    非子类会走 Pyannote 分支，其 merge_chunks 用 pyannote 的 Binarize 处理
    返回值（要求 Annotation，有 .data），普通 list 会直接抛
    `'list' object has no attribute 'data'` —— 补转全线失败就是这么来的。
    """
    try:
        from whisperx.vads import Vad
    except Exception:
        Vad = vg._FallbackVad
    assert issubclass(vg.NoVad, Vad)


def test_novad_output_is_consumable_by_merge_chunks():
    """回归：whisperx 会拿 __call__ 的返回值直接喂 merge_chunks。"""
    class W:
        shape = (1, 16000 * 75)
    spans = vg.NoVad()({"waveform": W(), "sample_rate": 16000})
    chunks = vg.NoVad.merge_chunks(spans, 30, 0.5, 0.363)
    # 75 秒整段都算语音 → 按 30s 切成 3 块（30 + 30 + 15）
    assert [round(c["end"] - c["start"], 1) for c in chunks] == [30.0, 30.0, 15.0]
    assert chunks[0]["start"] == 0.0 and round(chunks[-1]["end"], 1) == 75.0


# ------------------------------------------------- find_uncovered_gaps

def test_gap_between_two_segments_is_found():
    gaps = vg.find_uncovered_gaps(_segs((0.0, 5.0), (11.0, 15.0)), 3.0)
    assert gaps == [(5.0, 11.0)]


def test_short_gap_is_ignored():
    assert vg.find_uncovered_gaps(_segs((0.0, 5.0), (6.0, 9.0)), 3.0) == []


def test_leading_and_trailing_silence_are_not_gaps():
    """片头片尾本就没语音，不能每次都当成漏转去补。"""
    segs = _segs((30.0, 40.0), (50.0, 60.0))
    assert vg.find_uncovered_gaps(segs, 3.0) == [(40.0, 50.0)]


def test_multiple_gaps_sorted():
    segs = _segs((0.0, 5.0), (20.0, 25.0), (40.0, 45.0), (60.0, 65.0))
    assert vg.find_uncovered_gaps(segs, 3.0) == [(5.0, 20.0), (25.0, 40.0), (45.0, 60.0)]


def test_overlapping_segments_do_not_produce_negative_gaps():
    segs = _segs((0.0, 10.0), (8.0, 20.0))
    assert vg.find_uncovered_gaps(segs, 3.0) == []


def test_no_segments_means_no_gaps():
    assert vg.find_uncovered_gaps([], 3.0) == []


def test_segments_missing_timestamps_are_skipped():
    assert vg.find_uncovered_gaps([{"text": "no time"}, {"start": None, "end": 3}], 3.0) == []


def test_explicit_span_includes_unfilled_tail_and_whole_empty_result():
    assert vg.find_uncovered_gaps(_segs((0, 2)), 3, span=(0, 27)) == [(2, 27)]
    assert vg.find_uncovered_gaps([], 3, span=(0, 27)) == [(0, 27)]
    assert vg.find_uncovered_gaps(_segs((4, 25)), 3, span=(0, 30)) == [(0, 4), (25, 30)]


def test_word_coverage_exposes_hole_inside_long_segment():
    seg = {"start": 0, "end": 30, "words": [
        {"start": 0, "end": 3}, {"start": 25, "end": 30}]}
    assert vg.find_uncovered_gaps([seg], 3) == [(3, 25)]
    assert vg.find_uncovered_gaps([{"start": 0, "end": 30, "words": []}],
                                  3, span=(0, 30)) == [(0, 30)]


# ------------------------------------------------------ speech_ratio

def test_speech_ratio_from_detected_silence(monkeypatch):
    """使用确定的ffmpeg输出，不依赖已被换掉的历史68分钟视频。"""
    monkeypatch.setattr(vg.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, stderr="silence_duration: 2.0"))
    assert vg.speech_ratio("fixture.mp3", 0, 10) == pytest.approx(0.8)


def test_speech_ratio_is_zero_when_window_is_empty():
    assert vg.speech_ratio(AUDIO if _has_audio else "none.mp3", 10.0, 10.0) == 0.0


def test_speech_ratio_nonzero_exit_fails_open(monkeypatch):
    monkeypatch.setattr(vg.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 1, stderr="silence_duration: 10.0"))
    assert vg.speech_ratio("missing.mp3", 0, 10) == 1.0


def test_speech_ratio_fails_open_when_ffmpeg_is_missing(monkeypatch):
    """探测失败当作有声：宁可多转一段，也不能因探测出错而漏内容。"""
    def boom(*a, **k):
        raise OSError("no ffmpeg")
    monkeypatch.setattr(vg.subprocess, "run", boom)
    assert vg.speech_ratio("whatever.mp3", 0.0, 10.0) == 1.0


# ------------------------------------------- looks_like_hallucination

@pytest.mark.parametrize("text", ["", "   ", ".", "...", "-", "♪"])
def test_junk_text_is_rejected(text):
    assert vg.looks_like_hallucination(text)


def test_repeated_single_word_is_rejected():
    assert vg.looks_like_hallucination("Yeah. Yeah. Yeah. Yeah. Yeah.")


def test_real_speech_is_accepted():
    assert not vg.looks_like_hallucination(
        "Why Am I Like This is literally available tomorrow.")


def test_short_but_real_text_is_accepted():
    assert not vg.looks_like_hallucination("Yes.")


# ------------------------------------------------------ gap_fill_settings

def test_settings_fall_back_to_defaults(monkeypatch, tmp_path):
    """config 缺键也不能崩 —— 补转是增强功能，配置不完整应走默认值。"""
    monkeypatch.setattr(vg, "_load_key", lambda k, d: d)
    s = vg.gap_fill_settings()
    assert s["enabled"] is True
    assert s["min_gap"] == vg.DEFAULT_SETTINGS["min_gap"]


def test_settings_disabled_short_circuits(monkeypatch):
    monkeypatch.setattr(vg, "_load_key", lambda k, d: False if k.endswith("vad_gap_fill") else d)
    assert vg.gap_fill_settings()["enabled"] is False


# ------------------------------------------------------------- fill_gaps

class _FakeModel:
    """最小 whisperx 替身：记录 vad_model 是否被换掉、返回预设 segment。"""

    def __init__(self, texts):
        self.texts = texts
        self.vad_model = object()
        self.calls = []
        self.vad_during_call = []

    def transcribe(self, audio, batch_size=None, **kw):
        self.calls.append(len(audio))
        self.vad_during_call.append(type(self.vad_model).__name__)
        return {"segments": [{"start": 0.0, "end": 5.0, "text": t,
                              "words": [{"start": 0.0, "end": 5.0, "word": t}]}
                             for t in self.texts]}


@pytest.fixture(autouse=True)
def isolated_audio_detection(monkeypatch, request):
    """编排测试使用有声替身，不读真实output或不存在的x.mp3。"""
    if request.node.name.startswith("test_fill"):
        monkeypatch.setattr(vg, "speech_ratio", lambda *a, **kw: 1.0)


def _run_fill(model, segs, settings=None, **over):
    kw = dict(
        model=model, segments=segs,
        raw_audio_segment=_FakeAudio(16000 * 10), vocal_audio_segment=_FakeAudio(16000 * 10),
        seg_start=1000.0, sample_rate=16000, raw_audio_file="x.mp3",
        model_a=None, metadata=None, align_device="cpu", batch_size=4,
        settings=settings or dict(vg.DEFAULT_SETTINGS),
        align_fn=lambda segs, ma, md, au, dev, **kw: {"segments": segs},
        retry_fn=lambda model, audio: {"segments": []},
    )
    kw.update(over)
    return vg.fill_gaps(**kw)


class _FakeAudio:
    def __init__(self, n):
        self._n = n

    def __getitem__(self, sl):
        return self

    def __len__(self):
        return self._n

    @property
    def size(self):
        return self._n


def test_fill_gaps_swaps_vad_then_restores_it():
    model = _FakeModel(["recovered speech here"])
    _run_fill(model, _segs((0.0, 5.0), (20.0, 25.0)))
    assert model.vad_during_call == ["NoVad"]
    assert type(model.vad_model).__name__ == "object"   # 已还原


def test_fill_gaps_returns_segments_in_segment_relative_time():
    """坐标必须与入参 segments 一致（分片内相对秒），不能加 seg_start。

    调用方合并后会统一 `+= start`；这里再加一次会让补转段整体后移一个分片。
    """
    model = _FakeModel(["recovered"])
    added = _run_fill(model, _segs((0.0, 5.0), (20.0, 25.0)))
    assert added and added[0]["start"] == pytest.approx(5.0)
    assert added[0]["words"][0]["start"] == pytest.approx(5.0)


def test_fill_gaps_skips_gap_when_it_is_silent(monkeypatch):
    monkeypatch.setattr(vg, "speech_ratio", lambda *a, **k: 0.0)
    model = _FakeModel(["recovered"])
    assert _run_fill(model, _segs((0.0, 5.0), (20.0, 25.0))) == []
    assert model.calls == []


def test_fill_gaps_drops_hallucinated_text():
    model = _FakeModel(["Yeah. Yeah. Yeah. Yeah. Yeah."])
    assert _run_fill(model, _segs((0.0, 5.0), (20.0, 25.0))) == []


def test_fill_gaps_survives_transcribe_failure():
    class Boom(_FakeModel):
        def transcribe(self, audio, batch_size=None, **kw):
            raise RuntimeError("CUDA exploded")
    assert _run_fill(Boom(["x"]), _segs((0.0, 5.0), (20.0, 25.0))) == []


def test_fill_gaps_discards_segment_when_align_fails():
    model = _FakeModel(["recovered"])
    bad_align = lambda segs, ma, md, au, dev, **kw: (_ for _ in ()).throw(
        RuntimeError("align died"))
    assert _run_fill(model, _segs((0.0, 5.0), (20.0, 25.0)), align_fn=bad_align) == []


def test_fill_gaps_no_gaps_no_work():
    model = _FakeModel(["x"])
    assert _run_fill(model, _segs((0.0, 5.0), (5.5, 9.0))) == []
    assert model.calls == []


@dataclass(frozen=True)
class _Options:
    initial_prompt: str | None = "A podcast host discusses relationship advice and mental health topics."
    hotwords: str | None = "Jillian, self-love"
    prefix: str | None = "prefix"


def test_fill_clears_prompts_temporarily_and_restores_on_failure():
    class Probe(_FakeModel):
        def transcribe(self, audio, **kw):
            assert self.options.initial_prompt is None
            assert self.options.hotwords is None
            assert self.options.prefix is None
            assert isinstance(self.vad_model, vg.NoVad)
            raise RuntimeError("test failure")
    model = Probe([])
    original = model.options = _Options()
    vad = model.vad_model
    assert _run_fill(model, _segs((0, 5), (20, 25))) == []
    assert model.options is original and model.vad_model is vad


def test_fill_retries_nonempty_but_incomplete_transcript_once():
    model = _FakeModel(["What is self-love and self-acceptance?"])
    calls = []
    def retry(model, audio):
        calls.append(True)
        return {"segments": [{"start": 0, "end": 27, "text": "Actual full spoken content.",
                              "words": [{"start": 0, "end": 27, "word": "content"}]}]}
    result = _run_fill(model, _segs((0, 5), (32, 35)), retry_fn=retry)
    assert calls == [True]
    assert len(result) == 1 and result[0]["text"] == "Actual full spoken content."
    assert result[0]["end"] == 32             # 只加局部偏移5，不加seg_start=1000


def test_fill_complete_transcript_does_not_retry():
    calls = []
    result = _run_fill(_FakeModel(["complete speech"]), _segs((0, 5), (10, 15)),
                       retry_fn=lambda *a: calls.append(True))
    assert result and calls == []


def test_fill_unresolved_gap_is_reported_without_infinite_retry(monkeypatch):
    messages, calls = [], []
    monkeypatch.setattr(vg, "rprint", messages.append)
    def retry(*a):
        calls.append(True)
        return {"segments": []}
    result = _run_fill(_FakeModel(["short partial result"]),
                       _segs((0, 5), (32, 35)), retry_fn=retry)
    assert calls == [True] and result
    assert any("未解决有声空档" in s for s in messages)


def test_fill_short_phrase_with_pauses_is_not_skipped_at_47_percent(monkeypatch):
    monkeypatch.setattr(vg, "speech_ratio", lambda *a, **kw: 0.47)
    model = _FakeModel(["and it's been a long time"])
    result = _run_fill(model, _segs((0, 5), (8.882, 12)))
    assert model.calls and result


def test_fill_retries_speech_with_many_pauses_below_ratio_threshold(monkeypatch):
    monkeypatch.setattr(vg, "speech_ratio", lambda f, a, b, *args:
                        1.0 if a == 1005 and b == 1032 else 0.4)
    calls = []
    def retry(*args):
        calls.append(True)
        return {"segments": []}
    _run_fill(_FakeModel(["short partial result"]), _segs((0, 5), (32, 35)), retry_fn=retry)
    assert calls == [True]                 # 22s剩余窗口×40%仍有8.8s声音


def test_fill_does_not_retry_silent_residual_tail(monkeypatch):
    monkeypatch.setattr(vg, "speech_ratio", lambda f, a, b, *args:
                        1.0 if a == 1005 and b == 1032 else 0.0)
    calls = []
    result = _run_fill(_FakeModel(["spoken introduction"]), _segs((0, 5), (32, 35)),
                       retry_fn=lambda *a: calls.append(True))
    assert result and calls == []


def test_prompt_echo_requires_both_long_overlap_and_low_alignment():
    text = "Jillian A podcast host discusses relationship advice and mental health topics."
    segment = {"text": text, "words": [{"score": 0.3}]}
    assert vg.prompt_echo(segment, _Options().initial_prompt)
    assert not vg.prompt_echo({**segment, "words": [{"score": 0.9}]}, _Options().initial_prompt)
    assert not vg.prompt_echo({**segment, "text": "I accept myself."}, _Options().initial_prompt)


def test_fill_echo_replacement_requires_good_alignment():
    original = {"start": 0, "end": 5, "text": "wrong prompt text",
                "words": [{"start": 0, "end": 5, "score": 0.2}]}
    segments = [original]
    def align(segs, *args, **kw):
        for seg in segs:
            for w in seg["words"]:
                w["score"] = 0.9
        return {"segments": segs}
    result = _run_fill(_FakeModel(["actual speech"]), segments,
                       repair_spans=[original], align_fn=align)
    assert segments == [] and result[0]["text"] == "actual speech"


def test_fill_echo_keeps_original_when_recovery_is_uncertain():
    original = {"start": 0, "end": 5, "text": "wrong prompt text"}
    segments = [original]
    assert _run_fill(_FakeModel(["no scored words"]), segments, repair_spans=[original]) == []
    assert segments == [original]


def test_fill_internal_word_hole_replaces_whole_segment_to_keep_chronology():
    original = {"start": 0, "end": 30, "text": "before after", "words": [
        {"word": "before", "start": 0, "end": 3, "score": 0.9},
        {"word": "after", "start": 25, "end": 30, "score": 0.9}]}
    segments = [original]
    class Full(_FakeModel):
        def transcribe(self, audio, **kw):
            return {"segments": [{"start": 0, "end": 30,
                    "text": "full recovery", "words": [
                        {"word": "before recovered after", "start": 0, "end": 30, "score": 0.9}]}]}
    result = _run_fill(Full([]), segments)
    assert segments == []
    assert result[0]["start"] == 0 and result[0]["end"] == 30
