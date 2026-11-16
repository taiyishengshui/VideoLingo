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
