"""Local ASR decode policy shared by transcription and checkpoint fingerprints.

Domain prose is metadata for correction, not a transcript for Whisper to continue.
The default follows the user's reference backend: empty prompt, temperature zero,
no forced n-gram suppression. Advanced hint modes require an explicit config.
"""
from __future__ import annotations

DECODE_VERSION = 3


def decode_settings() -> dict:
    from core.utils.config_utils import load_key
    def get(key, default):
        try:
            value = load_key("whisper." + key)
            return default if value is None else value
        except KeyError:
            return default
    mode = str(get("hint_mode", "off")).lower()
    if mode not in ("off", "hotwords", "domain"):
        raise ValueError("whisper.hint_mode must be off, hotwords or domain")
    return {"version": DECODE_VERSION, "hint_mode": mode,
            "beam_size": max(1, int(get("beam_size", 5))),
            "no_repeat_ngram_size": max(0, int(get("no_repeat_ngram_size", 0)))}


def build_asr_options(hints=None, settings=None) -> dict:
    cfg = decode_settings() if settings is None else settings
    hints = hints or {}
    mode = cfg["hint_mode"]
    return {"temperatures": [0.0], "condition_on_previous_text": False,
            "beam_size": cfg["beam_size"],
            "no_repeat_ngram_size": cfg["no_repeat_ngram_size"],
            "initial_prompt": hints.get("initial_prompt") if mode == "domain" else "",
            "hotwords": hints.get("hotwords") if mode in ("hotwords", "domain") else None}
