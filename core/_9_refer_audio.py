"""Pipeline step 9: extract speaker reference audio segments.

2026-09-08: implementation moved to core/tts_backend/refer_audio.py so TTS
backends can use it without importing a numbered step. This file is now
only the pipeline entry point (kept for the registry and CLI habit).
"""
from core.tts_backend.refer_audio import extract_refer_audio_main

__all__ = ["extract_refer_audio_main"]

if __name__ == "__main__":
    extract_refer_audio_main()
