"""Decode policy regressions: domain prose must not replace the spoken audio."""
from core.services import asr_service as svc


def test_default_decode_matches_reference_backend(cfg_path):
    cfg_path("whisper: {}")
    settings = svc.decode_settings()
    assert settings["hint_mode"] == "off"
    assert settings["version"] == svc.DECODE_VERSION
    options = svc.build_asr_options({"initial_prompt": "A podcast host discusses...", "hotwords": "Jillian"})
    assert options["initial_prompt"] == "" and options["hotwords"] is None
    assert options["temperatures"] == [0.0]
    assert options["no_repeat_ngram_size"] == 0
    assert options["condition_on_previous_text"] is False


def test_only_explicit_hint_modes_enable_them(cfg_path):
    cfg_path("whisper:\n  hint_mode: hotwords\n  beam_size: 8\n")
    options = svc.build_asr_options({"initial_prompt": "A podcast host", "hotwords": "Jillian"})
    assert options["initial_prompt"] == ""
    assert options["hotwords"] == "Jillian" and options["beam_size"] == 8
    cfg_path("whisper:\n  hint_mode: domain\n")
    assert svc.build_asr_options({"initial_prompt": "domain text"})["initial_prompt"] == "domain text"


def test_decode_changes_alter_checkpoint_fingerprint(cfg_path):
    cfg_path("whisper:\n  hint_mode: off\n")
    before = svc.decode_settings()
    cfg_path("whisper:\n  hint_mode: domain\n")
    assert svc.decode_settings() != before
