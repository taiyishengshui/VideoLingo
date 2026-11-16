"""FR-34: one dubbing task row may contain several speakers.

Covers the three layers without touching real output/ files or loading any
model: turn detection in step 8.1, sub-line windows/contract in voice_map,
and the per-sub-line reference lookup in the index-tts consumer.
"""
import pandas as pd
import pytest

from core.services.subtitle_turn_service import looks_like_cross_turn
from core.tts_backend import index_tts, voice_map as vm


def test_cross_turn_signals():
    assert looks_like_cross_turn("谁会更有安全感？", "告诉我", "", "")
    assert looks_like_cross_turn("逼他成为非凡的人", "是的 父母常问我", "", "")
    assert looks_like_cross_turn("", "Yes I remember", "", "Yes I remember")
    assert not looks_like_cross_turn(
        "我们想处理自己的情绪", "我们可以吼叫", "", "")


def test_subline_windows_uses_srt_times(monkeypatch):
    monkeypatch.setattr(vm, "_active_splits", lambda: {})
    monkeypatch.setattr(vm, "_srt_blocks", lambda: [
        (10.0, 11.2, "A"), (11.2, 12.0, "B"), (12.0, 14.0, "C"),
    ])
    df = pd.DataFrame([
        {"number": 1, "start_time": "00:00:10.000",
         "end_time": "00:00:12.000", "lines": "['A', 'B']"},
        {"number": 2, "start_time": "00:00:12.000",
         "end_time": "00:00:14.000", "lines": "['C']"},
    ])
    w = vm.subline_windows(df)
    assert w["1"]["source"] == "srt"
    assert w["1"]["windows"] == [(0.0, 1.2), (1.2, 2.0)]
    assert w["2"]["windows"] == [(0.0, 2.0)]


def test_subline_windows_equal_split_fallback(monkeypatch):
    monkeypatch.setattr(vm, "_active_splits", lambda: {})
    monkeypatch.setattr(vm, "_srt_blocks", lambda: [])
    df = pd.DataFrame([
        {"number": 1, "start_time": "00:00:10.000",
         "end_time": "00:00:14.000", "lines": "['A', 'B']"},
    ])
    w = vm.subline_windows(df)
    assert w["1"]["source"] == "split"
    assert w["1"]["windows"] == [(0.0, 2.0), (2.0, 4.0)]


def test_mark_mixed_is_idempotent():
    df = pd.DataFrame([
        {"number": 1, "lines": "['A', 'B']"},
        {"number": 2, "lines": "['C']"},
    ])
    data = {}
    assert vm.mark_mixed(data, df) == 1
    assert data["segment_meta"]["1"]["flags"] == ["mixed"]
    assert vm.mark_mixed(data, df) == 1
    assert data["segment_meta"]["1"]["flags"] == ["mixed"]
    assert "2" not in data["segment_meta"]


def test_chunk_rows_falls_back_to_row_speaker():
    data = {"segments": {"7": "S0"}, "chunk_speakers": {}, "chunk_meta": {}}
    windows = {"7": {"lines": ["A", "B"], "windows": [(0, 1), (1, 2)],
                     "source": "srt"}}
    out = vm.chunk_rows(data, windows=windows)
    assert [r["speaker"] for r in out["7"]["rows"]] == ["S0", "S0"]
    assert out["7"]["rows"][0]["time"] == [0, 1]


def test_apply_chunk_overrides_validates(monkeypatch):
    data = {"speakers": {"S0": {}, "S1": {}}, "segments": {},
            "chunk_speakers": {}, "chunk_meta": {}}
    monkeypatch.setattr(vm, "_read_contract_raw", lambda: data)
    monkeypatch.setattr(vm, "subline_windows", lambda: {
        "7": {"lines": ["A", "B"], "windows": [], "source": "srt"}})
    monkeypatch.setattr(vm, "_write_contract_raw", lambda d: None)
    monkeypatch.setattr(vm, "_invalidate_consumer_cache", lambda: None)

    out = vm.apply_chunk_overrides({"7": {"1": "S1"}})
    assert out["changed"] == 1
    assert data["chunk_speakers"]["7"]["1"] == "S1"
    assert data["chunk_meta"]["7"]["1"]["manual"] is True
    with pytest.raises(KeyError):
        vm.apply_chunk_overrides({"7": {"9": "S1"}})
    with pytest.raises(KeyError):
        vm.apply_chunk_overrides({"7": {"0": "UNKNOWN"}})


# --------------------------------------------------------------- v1.26


def test_step_numbers_match_source_file_numbers():
    """页面序号必须和源码 _2…_12 一致：第 10 步就是「生成配音」。"""
    from core import pipeline

    assert len(pipeline.STEPS) == 12
    assert [pipeline.STEP_NO[s.id] for s in pipeline.STEPS] == list(range(1, 13))
    assert pipeline.STEP_NO["align"] == 5
    assert pipeline.STEP_NO["gen_sub"] == 6
    assert pipeline.STEP_NO["merge_sub"] == 7
    assert pipeline.STEP_NO["gen_audio"] == 10
    assert [s["no"] for s in pipeline.pipeline_status()] == list(range(1, 13))


def test_generate_subtitles_is_a_separate_zero_llm_step():
    """只重跑「生成字幕」不得删第 5 步产物、也不得登记 LLM 任务。"""
    from core import pipeline

    align = pipeline.get_step("align")
    gen = pipeline.get_step("gen_sub")
    assert align.calls == (("core._5_split_sub", "split_for_sub_main"),)
    assert gen.calls == (("core._6_gen_sub", "align_timestamp_main"),)
    assert gen.llm_titles == ()
    assert "output/src.srt" in gen.markers
    assert not set(align.markers) & set(gen.markers)


def test_split_text_at_prefers_a_natural_boundary():
    parts = vm._split_text_at("谁会更有安全感？当然是那个普通的孩子", 0.45)
    assert parts == ["谁会更有安全感？", "当然是那个普通的孩子"]
    assert vm._split_text_at("短", 0.5) is None


def test_subline_windows_keeps_srt_cursor_after_manual_split(monkeypatch):
    """拆分行只消费 1 个 srt 块，不能把后面所有行挤偏一位。"""
    monkeypatch.setattr(vm, "_srt_blocks", lambda: [
        (10.0, 12.0, "A"), (12.0, 14.0, "B")])
    monkeypatch.setattr(vm, "_active_splits", lambda: {
        "1": {"lines": ["A1", "A2"], "at": 1.0, "n_src": 1}})
    df = pd.DataFrame([
        {"number": 1, "start_time": "00:00:10.000",
         "end_time": "00:00:12.000", "lines": "['A1', 'A2']"},
        {"number": 2, "start_time": "00:00:12.000",
         "end_time": "00:00:14.000", "lines": "['B']"},
    ])
    w = vm.subline_windows(df)
    assert w["1"]["windows"] == [(0.0, 1.0), (1.0, 2.0)]
    assert w["1"]["split"] is True
    assert w["2"]["windows"] == [(0.0, 2.0)]


def test_every_row_is_expandable_even_with_one_subtitle():
    data = {"segments": {"7": "S0"}, "chunk_speakers": {}, "chunk_meta": {}}
    windows = {"7": {"lines": ["A"], "windows": [(0, 2)], "source": "srt"}}
    out = vm.chunk_rows(data, windows=windows)
    assert out["7"]["mixed"] is False
    assert len(out["7"]["rows"]) == 1
    assert out["7"]["rows"][0]["speaker"] == "S0"


def test_whole_row_override_also_wins_over_sublines(monkeypatch):
    data = {"speakers": {"S0": {}, "S1": {}}, "segments": {"7": "S0"},
            "overrides": {}, "segment_meta": {},
            "chunk_speakers": {"7": {"0": "S1", "1": "S0"}},
            "chunk_meta": {}}
    monkeypatch.setattr(vm, "_read_contract_raw", lambda: data)
    monkeypatch.setattr(vm, "_write_contract_raw", lambda d: None)
    monkeypatch.setattr(vm, "_invalidate_consumer_cache", lambda: None)
    vm.apply_overrides({"7": "S1"})
    assert data["segments"]["7"] == "S1"
    assert set(data["chunk_speakers"]["7"].values()) == {"S1"}


def test_resolve_reference_prefers_subline(monkeypatch):
    data = {
        "segments": {"645": "S0"},
        "chunk_speakers": {"645": {"0": "S0", "1": "S1"}},
        "speakers": {"S0": {"ref": "/a.wav"}, "S1": {"ref": "/b.wav"}},
    }
    monkeypatch.setattr(index_tts, "_get_voice_map", lambda: data)
    cfg = {"refer_mode": 4, "spk_audio_prompt": ""}
    assert index_tts._resolve_reference(645, cfg, chunk_index=1) == "/b.wav"
    assert index_tts._resolve_reference(645, cfg) == "/a.wav"
    assert index_tts._resolve_reference(645, cfg, chunk_index=9) == "/a.wav"
