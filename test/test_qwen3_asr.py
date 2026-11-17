"""Qwen3-ASR adapter unit tests (FR-35).

Pure logic only: no qwen-asr / torch / CUDA / ffmpeg / real model weights.
The runner is mocked at subprocess level; parse_stamps is tested directly
because ForcedAligner's item shape may be dicts or dataclass-like objects.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.asr_backend import qwen3_asr as q3
from core.asr_backend import qwen3_asr_runner as runner


@pytest.fixture
def values(monkeypatch):
    """Replace config.yaml reads with defaults (+ a real python.exe)."""
    data = dict(q3._DEFAULTS)
    data["python_exe"] = sys.executable
    monkeypatch.setattr(
        q3, "_load",
        lambda key, default: data.get(key.split(".")[-1], default),
    )
    return data


class _Completed:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _payload():
    return {"segments": [{
        "text": "hello world",
        "start": 1.0, "end": 2.5,
        "words": [
            {"word": "hello", "start": 1.0, "end": 1.5},
            {"word": "world", "start": 1.7, "end": 2.5},
        ],
    }]}


# ---------------- window splitting ----------------

def test_build_windows_short_segment_is_single_window():
    assert q3.build_windows(0, 60, 270, 1.5) == [[0.0, 60.0]]


def test_build_windows_long_segment_covers_all_with_overlap():
    wins = q3.build_windows(0, 900, 270, 1.5)
    assert len(wins) > 3
    assert wins[0][0] == 0.0
    assert wins[-1][1] == 900.0
    assert all(win[1] - win[0] <= 270 + 1e-6 for win in wins)
    assert all(round(wins[i + 1][0] - wins[i][0], 3) == 268.5
               for i in range(len(wins) - 2))
    # no gap between neighbours (overlap actually shared)
    assert all(wins[i + 1][0] <= wins[i][1] for i in range(len(wins) - 1))


def test_build_windows_rejects_invalid_ranges():
    with pytest.raises(ValueError):
        q3.build_windows(10, 10, 270, 1.5)
    with pytest.raises(ValueError):
        q3.build_windows(0, 100, 301, 1.5)
    with pytest.raises(ValueError):
        q3.build_windows(0, 100, 50, 50)


# ---------------- config / signature ----------------

def test_config_defaults(values):
    cfg = q3.qwen3_config()
    assert cfg["model"] == "Qwen/Qwen3-ASR-1.7B"
    assert cfg["aligner"] == "Qwen/Qwen3-ForcedAligner-0.6B"
    assert cfg["language"] == "auto"
    assert cfg["max_window_seconds"] == 270
    assert cfg["dtype"] == "bfloat16"


def test_config_rejects_unsupported_aligner_language(values):
    values["language"] = "ar"  # ASR supports 52, ForcedAligner only 11
    with pytest.raises(ValueError, match="11 语种"):
        q3.qwen3_config()


def test_config_rejects_window_over_5_minutes(values):
    values["max_window_seconds"] = 600
    with pytest.raises(ValueError, match="5 分钟"):
        q3.qwen3_config()


def test_config_rejects_bad_overlap(values):
    values["overlap_seconds"] = 270
    with pytest.raises(ValueError):
        q3.qwen3_config()


def test_signature_excludes_python_exe(values):
    sig = q3.qwen3_signature()
    assert "python_exe" not in sig
    assert set(sig) == set(q3._SIGNATURE_KEYS)


# ---------------- runner result normalization ----------------

def test_normalize_shifts_to_absolute_time():
    out = q3.normalize_runner_result(_payload(), 1000.0)
    seg = out["segments"][0]
    assert seg["start"] == 1001.0 and seg["end"] == 1002.5
    assert seg["words"][0]["start"] == 1001.0
    assert seg["words"][1]["start"] == 1001.7
    # shape matches process_transcription: word/start/end per word
    assert all(set(w) >= {"word", "start", "end"} for w in seg["words"])


def test_normalize_rejects_missing_words():
    with pytest.raises(RuntimeError, match="words"):
        q3.normalize_runner_result({"segments": [{"text": "x"}]}, 0)
    with pytest.raises(RuntimeError, match="segments"):
        q3.normalize_runner_result({"segments": []}, 0)


def test_parse_stamps_accepts_dicts_and_objects():
    class Item:
        text = "字"
        start_time = 0.1
        end_time = 0.3

    assert runner.parse_stamps({"time_stamps": [[{"text": "a", "start": 0.0,
                                                  "end": 0.2}]]}) == [
        {"word": "a", "start": 0.0, "end": 0.2}]
    assert runner.parse_stamps({"time_stamps": [Item()]}) == [
        {"word": "字", "start": 0.1, "end": 0.3}]


def test_parse_stamps_missing_is_explicit_error():
    with pytest.raises(RuntimeError, match="ForcedAligner"):
        runner.parse_stamps({"text": "only text"})


# ---------------- subprocess behaviour: NO silent fallback ----------------

def test_missing_python_exe_fails_loudly(values, monkeypatch):
    values["python_exe"] = ""

    def must_not_run(*args, **kwargs):
        raise AssertionError("must not start any ASR when unconfigured")

    monkeypatch.setattr(q3.subprocess, "run", must_not_run)
    with pytest.raises(RuntimeError, match="独立 conda 环境"):
        q3.transcribe_audio_qwen3_asr("raw.mp3", "vocal.mp3", 0, 60)


def test_runner_failure_fails_loudly(values, monkeypatch):
    monkeypatch.setattr(q3.subprocess, "run",
                        lambda *a, **k: _Completed(stderr="CUDA OOM", returncode=4))
    with pytest.raises(RuntimeError, match="退出码 4"):
        q3.transcribe_audio_qwen3_asr("raw.mp3", "vocal.mp3", 0, 60)


def test_success_path_builds_spec_and_shifts_time(values, monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        spec = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        captured["spec"] = spec
        captured["cmd"] = cmd[:2]
        return _Completed(stdout="progress on stderr only\n" +
                          json.dumps(_payload(), ensure_ascii=False))

    monkeypatch.setattr(q3.subprocess, "run", fake_run)
    out = q3.transcribe_audio_qwen3_asr("raw.mp3", "vocal.mp3", 1000.0, 1060.0)

    spec = captured["spec"]
    assert spec["language"] is None            # auto -> language=None
    assert spec["windows"] == [[0.0, 60.0]]
    assert spec["max_inference_batch_size"] == 8
    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][1].endswith("qwen3_asr_runner.py")
    assert out["segments"][0]["words"][0]["start"] == 1001.0


def test_language_mapping_and_long_windows(values, monkeypatch):
    values["language"] = "zh"
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["spec"] = json.loads(Path(cmd[-1]).read_text(encoding="utf-8"))
        return _Completed(stdout=json.dumps(_payload()))

    monkeypatch.setattr(q3.subprocess, "run", fake_run)
    q3.transcribe_audio_qwen3_asr("raw.mp3", "vocal.mp3", 0, 900)
    assert captured["spec"]["language"] == "Chinese"
    assert len(captured["spec"]["windows"]) == 4
    assert captured["spec"]["start"] == 0.0


def test_non_json_stdout_is_explicit_error(values, monkeypatch):
    monkeypatch.setattr(q3.subprocess, "run",
                        lambda *a, **k: _Completed(stdout="not json"))
    with pytest.raises(RuntimeError, match="JSON"):
        q3.transcribe_audio_qwen3_asr("raw.mp3", "vocal.mp3", 0, 60)


# ---------------- checkpoint fingerprint guard (static) ----------------

def test_fingerprint_contains_runtime_and_qwen3_signature():
    src = Path(__file__).resolve().parents[1] / "core" / "_2_asr.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_audio_fingerprint")
    body = ast.get_source_segment(src.read_text(encoding="utf-8"), fn)
    assert '"runtime"' in body
    assert "qwen3_signature" in body


# ---------------- runner main(): mocked, no GPU / no real weights ----------------

def test_runner_main_aggregates_windows_and_dedupes_overlap(tmp_path, monkeypatch, capsys):
    import types

    class FakeModel:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return FakeModel()

        def transcribe(self, audio=None, language=None, return_time_stamps=False):
            # One word inside the overlap (must be dropped by the 2nd window)
            # and one well inside the window (must be kept).
            return [types.SimpleNamespace(
                text="hello there",
                time_stamps=[[
                    {"text": "hello", "start_time": 0.0, "end_time": 0.4},
                    {"text": "there", "start_time": 10.0, "end_time": 10.5},
                ]],
            )]

    monkeypatch.setitem(sys.modules, "torch",
                        types.SimpleNamespace(bfloat16="bf16"))
    monkeypatch.setitem(sys.modules, "qwen_asr",
                        types.SimpleNamespace(Qwen3ASRModel=FakeModel))
    monkeypatch.setattr(runner, "decode_window", lambda *a, **k: object())

    spec = {
        "audio_file": "raw.mp3", "start": 0.0, "end": 538.5,
        "windows": [[0.0, 270.0], [268.5, 538.5]],
        "model": "Qwen/Qwen3-ASR-1.7B",
        "aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
        "language": None, "max_inference_batch_size": 8,
        "max_new_tokens": 1024, "device": "cuda:0", "dtype": "bfloat16",
        "overlap_seconds": 1.5,
    }
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(sys, "argv",
                        ["qwen3_asr_runner.py", "--spec-file", str(spec_file)])

    rc = runner.main()
    stdout = capsys.readouterr().out.strip()
    assert rc == 0
    payload = json.loads(stdout.splitlines()[-1])
    segs = payload["segments"]
    assert len(segs) == 2
    assert [w["word"] for w in segs[0]["words"]] == ["hello", "there"]
    assert segs[0]["words"][0]["start"] == 0.0
    # 2nd window: 268.5+0.0=268.5 falls inside overlap (cutoff 269.25) -> dropped
    assert [w["word"] for w in segs[1]["words"]] == ["there"]
    assert segs[1]["words"][0]["start"] == 278.5


# ---------------- FR-38: real qwen-asr 0.0.6 ForcedAlignResult shape ----------------

class _ForcedAlignItem:
    """Mirror qwen_asr.inference.qwen3_forced_aligner.ForcedAlignItem."""

    def __init__(self, text, start_time, end_time):
        self.text = text
        self.start_time = start_time
        self.end_time = end_time


class _ForcedAlignResult:
    """Mirror the real return type: `.items` plus __iter__/__len__/__getitem__."""

    def __init__(self, items):
        self.items = items

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        return self.items[idx]


class _BareIterable:
    """Iterable without `.items` — exercises the __iter__ fallback."""

    def __init__(self, items):
        self._items = items

    def __iter__(self):
        return iter(self._items)


def test_parse_stamps_accepts_forced_align_result_in_seconds():
    stamps = _ForcedAlignResult([
        _ForcedAlignItem("你好", 1.25, 1.75),
        _ForcedAlignItem("世界", 2.0, 2.4),
    ])
    assert runner.parse_stamps({"time_stamps": stamps}) == [
        {"word": "你好", "start": 1.25, "end": 1.75},
        {"word": "世界", "start": 2.0, "end": 2.4},
    ]


def test_parse_stamps_accepts_iterable_without_items():
    stamps = _BareIterable([_ForcedAlignItem("a", 0.5, 0.9)])
    assert runner.parse_stamps({"time_stamps": stamps}) == [
        {"word": "a", "start": 0.5, "end": 0.9}]


def test_parse_stamps_dict_items_method_is_not_mistaken():
    # dict.items is a bound method — never mistake it for the item list.
    assert runner.parse_stamps({"time_stamps": {
        "text": "字", "start_time": 0.1, "end_time": 0.3}}) == [
        {"word": "字", "start": 0.1, "end": 0.3}]


def test_parse_stamps_empty_result_reports_real_type():
    with pytest.raises(RuntimeError, match="_ForcedAlignResult"):
        runner.parse_stamps({"time_stamps": _ForcedAlignResult([])})


def test_runner_forces_utf8_at_module_import():
    src = (Path(__file__).resolve().parents[1] / "core" / "asr_backend" /
           "qwen3_asr_runner.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in tree.body
             if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
             and getattr(n.value.func, "id", None) == "_force_utf8"]
    assert len(calls) == 1
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_force_utf8")
    assert "utf-8" in ast.get_source_segment(src, fn)


def test_runner_main_with_real_forced_align_result(tmp_path, monkeypatch, capsys):
    import types

    class FakeModel:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return FakeModel()

        def transcribe(self, audio=None, language=None, return_time_stamps=False):
            return [types.SimpleNamespace(
                text="你好世界",
                time_stamps=_ForcedAlignResult([
                    _ForcedAlignItem("你好", 0.0, 0.4),
                    _ForcedAlignItem("世界", 10.0, 10.5),
                ]),
            )]

    monkeypatch.setitem(sys.modules, "torch",
                        types.SimpleNamespace(bfloat16="bf16"))
    monkeypatch.setitem(sys.modules, "qwen_asr",
                        types.SimpleNamespace(Qwen3ASRModel=FakeModel))
    monkeypatch.setattr(runner, "decode_window", lambda *a, **k: object())

    spec = {
        "audio_file": "raw.mp3", "start": 0.0, "end": 538.5,
        "windows": [[0.0, 270.0], [268.5, 538.5]],
        "model": "Qwen/Qwen3-ASR-1.7B",
        "aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
        "language": None, "max_inference_batch_size": 8,
        "max_new_tokens": 1024, "device": "cuda:0", "dtype": "bfloat16",
        "overlap_seconds": 1.5,
    }
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(sys, "argv",
                        ["qwen3_asr_runner.py", "--spec-file", str(spec_file)])

    rc = runner.main()
    stdout = capsys.readouterr().out.strip()
    assert rc == 0
    segs = json.loads(stdout.splitlines()[-1])["segments"]
    assert len(segs) == 2
    assert [w["word"] for w in segs[0]["words"]] == ["你好", "世界"]
    assert segs[0]["words"][1]["start"] == 10.0     # seconds, NOT /1000
    assert [w["word"] for w in segs[1]["words"]] == ["世界"]
    assert segs[1]["words"][0]["start"] == 278.5


# --- FR-39: word-level stamp normalization ---------------------------------

def test_normalize_words_drops_zero_width_hallucination():
    words = [
        {"word": "hello", "start": 0.0, "end": 1.0},
        {"word": "thrivecosmeticscausemeticscomjillian", "start": 1.0, "end": 1.0},
        {"word": "world", "start": 1.0, "end": 2.0},
    ]
    out, stats = runner.normalize_words(words)
    assert [w["word"] for w in out] == ["hello", "world"]
    assert stats["dropped"] == 1
    # An ALIGNED long word is never dropped, even when >30 chars.
    out2, stats2 = runner.normalize_words(
        [{"word": "x" * 35, "start": 0.0, "end": 1.0}])
    assert [w["word"] for w in out2] == ["x" * 35]
    assert stats2["dropped"] == 0


def test_normalize_words_interpolates_zero_width_run_by_char_ratio():
    words = [
        {"word": "A", "start": 0.0, "end": 1.0},
        {"word": "to", "start": 2.0, "end": 2.0},
        {"word": "a", "start": 2.0, "end": 2.0},
        {"word": "B", "start": 3.0, "end": 4.0},
    ]
    out, stats = runner.normalize_words(words)
    assert stats["interpolated"] == 2 and stats["still_zero"] == 0
    to, a = out[1], out[2]
    assert (to["start"], a["end"]) == (1.0, 3.0)
    ratio = (to["end"] - to["start"]) / (a["end"] - a["start"])
    assert abs(ratio - 2.0) < 0.01          # "to" 2 chars : "a" 1 char
    assert out[0]["end"] == 1.0 and out[3]["start"] == 3.0
    assert all(w["start"] < w["end"] for w in out)


def test_normalize_words_keeps_zero_width_without_gap_and_never_shifts_neighbours():
    words = [
        {"word": "A", "start": 0.0, "end": 2.0},
        {"word": "to", "start": 2.0, "end": 2.0},
        {"word": "B", "start": 2.0, "end": 3.0},
    ]
    before = [dict(w) for w in words]
    out, stats = runner.normalize_words(words)
    assert stats["still_zero"] == 1 and stats["interpolated"] == 0
    assert (out[1]["start"], out[1]["end"]) == (2.0, 2.0)
    assert (out[2]["start"], out[2]["end"]) == (2.0, 3.0)
    assert words == before                  # input is never mutated


def test_normalize_words_leaves_aligned_words_untouched():
    words = [
        {"word": "hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.5, "end": 1.2},
        {"word": "again", "start": 1.2, "end": 1.9},
    ]
    out, stats = runner.normalize_words([dict(w) for w in words])
    assert out == words
    assert stats == {"dropped": 0, "interpolated": 0,
                     "still_zero": 0, "split": 0,
                     "stretch_groups": 0, "stretched": 0,
                     "stretch_seconds": 0.0}


def test_normalize_words_splits_url_like_long_word_proportionally():
    words = [
        {"word": "Go", "start": 470.0, "end": 471.0},
        {"word": "thrivecosmeticscomjillian", "start": 471.682, "end": 474.562},
        {"word": "for", "start": 474.882, "end": 475.122},
    ]
    out, stats = runner.normalize_words(words)
    assert stats["split"] == 1
    assert [w["word"] for w in out] == [
        "Go", "thrivecosmetics.com", "jillian", "for"]
    a, b = out[1], out[2]
    assert a["start"] == 471.682 and b["end"] == 474.562
    assert a["start"] < a["end"] == b["start"] < b["end"]
    assert (out[0]["start"], out[0]["end"]) == (470.0, 471.0)
    assert (out[3]["start"], out[3]["end"]) == (474.882, 475.122)


def test_normalize_words_does_not_split_chinese_punctuation_or_ordinary_long_words():
    cases = [
        "thrivecosmetics",                                # too short
        "thrive cosmetics com jillian",                   # spaces
        "thrive-cosmetics.com/jillian",                   # punctuation
        "这是一段很长的中文词级时间戳字符串测试内容示例文字",  # Chinese, char-level
        "pneumonoultramicroscopicsilicovolcanoconiosis",  # no URL token
        "accommodationrecommendation",                    # 'com' occurs twice
    ]
    for text in cases:
        out, stats = runner.normalize_words(
            [{"word": text, "start": 0.0, "end": 2.0}])
        assert [w["word"] for w in out] == [text], text
        assert stats["split"] == 0, text


def test_normalize_words_interpolation_stays_inside_anchor_window():
    words = [
        {"word": "A", "start": 0.0, "end": 1.0},
        {"word": "you", "start": 1.0, "end": 1.0},
        {"word": "know", "start": 1.0, "end": 1.0},
        {"word": "B", "start": 1.08, "end": 2.0},
    ]
    out, stats = runner.normalize_words(words)
    assert stats["interpolated"] == 2
    for w in out[1:3]:
        assert 1.0 <= w["start"] < w["end"] <= 1.08
    assert out[2]["end"] == 1.08
    assert all(out[i]["end"] <= out[i + 1]["start"]
               for i in range(len(out) - 1))


def test_normalize_words_stats_keys_and_three_field_contract():
    out, stats = runner.normalize_words(
        [{"word": "ok", "start": 0.0, "end": 1.0}])
    assert out == [{"word": "ok", "start": 0.0, "end": 1.0}]
    assert set(stats) == {"dropped", "interpolated", "still_zero", "split",
                          "stretch_groups", "stretched", "stretch_seconds"}
    assert all(set(w) == {"word", "start", "end"} for w in out)


def test_runner_main_applies_normalization_and_keeps_single_json(
        tmp_path, monkeypatch, capsys):
    import types

    class FakeModel:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return FakeModel()

        def transcribe(self, audio=None, language=None, return_time_stamps=False):
            return [types.SimpleNamespace(
                text="hello world",
                time_stamps=_ForcedAlignResult([
                    _ForcedAlignItem("hello", 0.0, 1.0),
                    _ForcedAlignItem("to", 1.04, 1.04),
                    _ForcedAlignItem(
                        "thrivecosmeticscausemeticscomjillian", 1.1, 1.1),
                    _ForcedAlignItem("thrivecosmeticscomjillian", 1.2, 3.0),
                    _ForcedAlignItem("world", 3.0, 4.0),
                ]),
            )]

    monkeypatch.setitem(sys.modules, "torch",
                        types.SimpleNamespace(bfloat16="bf16"))
    monkeypatch.setitem(sys.modules, "qwen_asr",
                        types.SimpleNamespace(Qwen3ASRModel=FakeModel))
    monkeypatch.setattr(runner, "decode_window", lambda *a, **k: object())
    spec = {
        "audio_file": "raw.mp3", "start": 0.0, "end": 4.0,
        "windows": [[0.0, 4.0]],
        "model": "Qwen/Qwen3-ASR-1.7B",
        "aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
        "language": None, "max_inference_batch_size": 8,
        "max_new_tokens": 1024, "device": "cuda:0", "dtype": "bfloat16",
        "overlap_seconds": 1.5,
    }
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(sys, "argv",
                        ["qwen3_asr_runner.py", "--spec-file", str(spec_file)])

    assert runner.main() == 0
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1                       # stdout still one JSON only
    words = json.loads(lines[-1])["segments"][0]["words"]
    assert [w["word"] for w in words] == [
        "hello", "to", "thrivecosmetics.com", "jillian", "world"]
    assert all(set(w) == {"word", "start", "end"} for w in words)
    assert (words[1]["start"], words[1]["end"]) == (1.0, 1.2)
    assert "dropped=1" in captured.err
    assert "interpolated=1" in captured.err
    assert "split=1" in captured.err


# --- FR-41: evenly-spread inflated word runs --------------------------------

def _stretched_line():
    """The real 8:02 ASR checkpoint: 4 words each exactly 1.52 s, 3.04 s apart."""
    return [
        {"word": "That's", "start": 478.722, "end": 478.962},
        {"word": "Probably", "start": 478.962, "end": 479.042},
        {"word": "the", "start": 479.042, "end": 479.842},
        {"word": "biggest", "start": 479.842, "end": 479.922},
        {"word": "problem", "start": 479.922, "end": 481.442},
        {"word": "that", "start": 482.962, "end": 484.482},
        {"word": "I", "start": 486.002, "end": 487.522},
        {"word": "see", "start": 489.042, "end": 490.562},
        {"word": "is", "start": 490.722, "end": 490.802},
        {"word": "that", "start": 490.802, "end": 491.042},
    ]


def test_detect_uniform_stretch_matches_real_8min_case():
    assert runner.detect_uniform_stretch(_stretched_line()) == [(4, 8)]


def test_normalize_words_shrinks_inflated_run_without_moving_starts():
    words = _stretched_line()
    before = [dict(w) for w in words]
    out, stats = runner.normalize_words(words)
    assert stats["stretch_groups"] == 1
    assert stats["stretched"] == 4
    assert stats["stretch_seconds"] > 4.0
    assert len(out) == len(before) == 10
    assert [w["word"] for w in out] == [w["word"] for w in before]
    assert [w["start"] for w in out] == [w["start"] for w in before]
    for k in (4, 5, 6, 7):
        cap = runner.STRETCH_BASE_DUR + runner.STRETCH_SEC_PER_CHAR * len(out[k]["word"])
        assert 0 < out[k]["end"] - out[k]["start"] <= cap + 1e-6
        assert out[k]["end"] < before[k]["end"]
    # FR-41 never relocates a word: the sentence window is unchanged here.
    assert out[0]["start"] == 478.722 and out[-1]["end"] == 491.042
    assert words == before                      # input is never mutated


def test_uniform_stretch_ignores_ordinary_aligned_words():
    words = [{"word": "hello", "start": 0.0, "end": 0.5},
             {"word": "there", "start": 0.6, "end": 1.1},
             {"word": "friend", "start": 1.2, "end": 1.7}]
    out, stats = runner.normalize_words(words)
    assert stats["stretch_groups"] == 0 and stats["stretched"] == 0
    assert out == words


def test_uniform_stretch_requires_regular_start_intervals():
    words = [{"word": "a" * 8, "start": s, "end": s + 1.5}
             for s in (0.0, 3.0, 9.0, 10.0)]
    assert runner.detect_uniform_stretch(words) == []
    _, stats = runner.normalize_words(words)
    assert stats["stretched"] == 0


def test_uniform_stretch_requires_three_words_and_five_second_span():
    two = [{"word": "x" * 8, "start": 0.0, "end": 1.5},
           {"word": "y" * 8, "start": 3.0, "end": 4.5}]
    short_span = [{"word": "x" * 8, "start": 0.0, "end": 1.2},
                  {"word": "y" * 8, "start": 1.3, "end": 2.5},
                  {"word": "z" * 8, "start": 2.6, "end": 3.8}]
    assert runner.detect_uniform_stretch(two) == []
    assert runner.detect_uniform_stretch(short_span) == []
    _, stats = runner.normalize_words(short_span)
    assert stats["stretched"] == 0


def test_uniform_stretch_leaves_chinese_and_zero_width_words_alone():
    words = [
        {"word": "这", "start": 0.0, "end": 0.2},
        {"word": "是", "start": 0.2, "end": 0.4},
        {"word": "测", "start": 0.4, "end": 0.6},
        {"word": "试", "start": 0.6, "end": 0.6},
        {"word": "。", "start": 0.6, "end": 0.8},
    ]
    out, stats = runner.normalize_words([dict(w) for w in words])
    assert stats["stretch_groups"] == 0 and stats["stretched"] == 0
    assert [w["word"] for w in out] == [w["word"] for w in words]


def test_uniform_stretch_never_overlaps_the_next_word():
    # Artificial overlapping stamps: the char-based cap must still be clipped
    # to the successor's start, so no word can swallow its neighbour.
    words = [{"word": "x" * 15, "start": round(i * 0.5, 3),
              "end": round(i * 0.5 + 1.5, 3)} for i in range(8)]
    out, stats = runner.normalize_words(words)
    assert stats["stretch_groups"] == 1
    assert stats["stretched"] == 8
    assert all(out[i]["end"] <= out[i + 1]["start"] + 1e-6
               for i in range(len(out) - 1))
    assert [w["start"] for w in out] == [w["start"] for w in words]


def test_runner_main_reports_stretch_diagnostics(tmp_path, monkeypatch, capsys):
    import types

    class FakeModel:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return FakeModel()

        def transcribe(self, audio=None, language=None, return_time_stamps=False):
            return [types.SimpleNamespace(
                text="problem that I see",
                time_stamps=_ForcedAlignResult([
                    _ForcedAlignItem("problem", 0.0, 1.52),
                    _ForcedAlignItem("that", 3.04, 4.56),
                    _ForcedAlignItem("I", 6.08, 7.60),
                    _ForcedAlignItem("see", 9.12, 10.64),
                ]),
            )]

    monkeypatch.setitem(sys.modules, "torch",
                        types.SimpleNamespace(bfloat16="bf16"))
    monkeypatch.setitem(sys.modules, "qwen_asr",
                        types.SimpleNamespace(Qwen3ASRModel=FakeModel))
    monkeypatch.setattr(runner, "decode_window", lambda *a, **k: object())
    spec = {
        "audio_file": "raw.mp3", "start": 0.0, "end": 12.0,
        "windows": [[0.0, 12.0]],
        "model": "Qwen/Qwen3-ASR-1.7B",
        "aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
        "language": None, "max_inference_batch_size": 8,
        "max_new_tokens": 1024, "device": "cuda:0", "dtype": "bfloat16",
        "overlap_seconds": 1.5,
    }
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    monkeypatch.setattr(sys, "argv",
                        ["qwen3_asr_runner.py", "--spec-file", str(spec_file)])

    assert runner.main() == 0
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1                       # stdout still one JSON only
    words = json.loads(lines[-1])["segments"][0]["words"]
    assert [w["word"] for w in words] == ["problem", "that", "I", "see"]
    assert [w["start"] for w in words] == [0.0, 3.04, 6.08, 9.12]
    assert words[0]["end"] < 1.52
    assert "stretch_groups=1" in captured.err
    assert "stretched=4" in captured.err
