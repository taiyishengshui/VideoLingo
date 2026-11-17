"""FR-44 layer diagnostic pure-function tests; no model or GPU required."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

_PATH = Path(__file__).resolve().parent.parent / "check_asr_layers.py"
_SPEC = importlib.util.spec_from_file_location("check_asr_layers", _PATH)
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def test_extract_words_supports_forced_align_result_items():
    result = SimpleNamespace(items=[
        SimpleNamespace(text="That's", start_time=1.2, end_time=1.5),
        {"text": "Probably", "start_time": 2.0, "end_time": 2.4},
    ])
    assert probe.extract_words(result) == [
        {"word": "That's", "start": 1.2, "end": 1.5},
        {"word": "Probably", "start": 2.0, "end": 2.4},
    ]


def test_extract_words_handles_missing_or_invalid_items():
    assert probe.extract_words(None) == []
    assert probe.extract_words([{"bad": "shape"}]) == []


def test_direct_forced_align_result_is_read_as_result_itself():
    result = SimpleNamespace(items=[
        SimpleNamespace(text="Probably", start_time=2.0, end_time=2.4),
    ])
    parsed = probe._path_result(lambda: result, 440.0)
    assert parsed["ok"] is True
    assert parsed["text"] == "Probably"
    assert parsed["words"] == [{
        "word": "Probably", "start": 2.0, "end": 2.4,
        "absolute_start": 442.0, "absolute_end": 442.4,
    }]


def test_serialize_words_keeps_relative_and_absolute_seconds():
    words = [{"word": "Probably", "start": 47.8, "end": 48.2}]
    assert probe.serialize_words(words, 440.0) == [{
        "word": "Probably", "start": 47.8, "end": 48.2,
        "absolute_start": 487.8, "absolute_end": 488.2,
    }]


def test_locate_terms_uses_normalized_tokens_and_absolute_time():
    words = [
        {"word": "That's", "start": 38.7, "end": 39.0},
        {"word": "Probably", "start": 47.8, "end": 48.2},
    ]
    assert probe.locate_terms(words, 440.0) == {"That's": 478.7, "Probably": 487.8}
    assert probe.locate_terms([words[0]], 440.0)["Probably"] is None


def test_text_comparison_blocks_alignment_only_attribution_when_text_differs():
    same = probe.compare_texts("That's probably the biggest", "That’s, probably THE biggest!")
    different = probe.compare_texts("That's probably", "Probably")
    assert same["normalized_equal"] is True
    assert "can be compared" in same["attribution_note"]
    assert different["normalized_equal"] is False
    assert "cannot be attributed" in different["attribution_note"]


def test_windows_capture_production_and_diagnostic_ranges():
    assert probe.WINDOWS["production"] == (268.5, 270.0)
    assert probe.WINDOWS["diagnostic"] == (440.0, 80.0)


def test_script_is_isolated_and_defaults_report_to_temp_directory():
    source = _PATH.read_text(encoding="utf-8")
    assert "from core" not in source and "import core" not in source
    assert 'DEFAULT_AUDIO = ROOT / "output" / "audio" / "raw.mp3"' in source
    assert 'parser.add_argument("--out", default=str(Path(tempfile.gettempdir())' in source
    assert "out.write_text(" in source


def test_audio_summary_hashes_actual_samples_and_labels_unavailable():
    summary = probe._audio_summary((bytes([1, 2, 3, 4]), 16000))
    assert summary["available"] is True
    assert summary["sample_count"] == 4
    assert summary["duration"] == 0.0
    assert len(summary["sha256"]) == 64
    assert probe._audio_summary("not audio")["available"] is False


def test_timestamp_summary_and_missing_raw_fields_are_explicit():
    assert probe._timestamp_summary([1, 2, 3]) == {
        "count": 3, "first": [1.0, 2.0, 3.0], "last": [1.0, 2.0, 3.0],
        "available": True,
    }
    assert probe._field_summary({}, ("timestamps",))["available"] is False


def test_alignment_input_match_checks_text_language_and_exact_audio():
    audio = probe._audio_summary((bytes([0, 1, 2, 3]), 16000))
    traced = [{"text": "hello", "language": "English", "audio": audio}]
    matched = probe.alignment_input_match("hello", "English", audio, traced)
    mismatched = probe.alignment_input_match("hello", "English", audio, [
        {"text": "Hello!", "language": "English", "audio": audio}])
    assert matched["comparable"] is True and matched["all_equal"] is True
    assert mismatched["checks"]["text_exact"] is False
    assert mismatched["all_equal"] is False
    assert probe.alignment_input_match("hello", "English", audio, [
        {"text": "hello", "language": "English", "audio": {"available": False}}])[
            "comparable"] is False
    assert probe.alignment_input_match("hello", "English", audio, [])["comparable"] is False


def test_trace_combined_alignment_captures_batch_inputs_offset_and_results():
    class FakeAligner:
        def align(self, audio, text, language):
            return [SimpleNamespace(items=[
                SimpleNamespace(text=word, start_time=0.1, end_time=0.2)])
                for word in text]

        def fix_timestamp(self, values):
            return list(values)

    class FakeModel:
        def __init__(self):
            self.forced_aligner = FakeAligner()

        def _offset_align_result(self, result, offset_sec):
            return result

    model = FakeModel()
    original_align = model.forced_aligner.align
    original_offset = model._offset_align_result
    with probe.trace_combined_alignment(model, 440.0) as trace:
        results = model.forced_aligner.align(
            audio=[(bytes([0, 1, 2, 3]), 16000)],
            text=["Probably"], language=["English"])
        model._offset_align_result(results[0], 2.5)
        model.forced_aligner.fix_timestamp([2, 1, 3])
    entry = trace["calls"][0]
    assert entry["text"] == "Probably"
    assert entry["normalized_text"] == "probably"
    assert entry["language"] == "English"
    assert entry["offset_seconds"] == 2.5
    assert entry["absolute_start"] == 442.5
    assert entry["result"]["word_count"] == 1
    assert trace["timestamp_fixes"][0]["before"]["first"] == [2.0, 1.0, 3.0]
    assert trace["timestamp_fixes"][0]["after"]["first"] == [2.0, 1.0, 3.0]
    assert model.forced_aligner.align == original_align
    assert model._offset_align_result == original_offset


def test_trace_combined_alignment_restores_methods_when_call_raises():
    class FakeAligner:
        def align(self, *args, **kwargs):
            raise RuntimeError("align failed")

    class FakeModel:
        forced_aligner = FakeAligner()

        def _offset_align_result(self, result, offset_sec):
            return result

    model = FakeModel()
    original = model.forced_aligner.align
    try:
        with probe.trace_combined_alignment(model, 440.0):
            model.forced_aligner.align(audio=[], text=[], language=[])
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected align error")
    assert model.forced_aligner.align == original
