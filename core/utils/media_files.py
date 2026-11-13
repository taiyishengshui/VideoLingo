"""Media input file discovery — shared tool, not a pipeline step.

Extracted from core/_1_ytdlp.py (2026-09-08) so any step / backend / server
can locate the user's input media without importing a numbered step module.

Rules: numbered step files orchestrate; reusable tools live here.
Note: this module lives inside the core.utils package, so it must import
from core.utils.config_utils directly — `from core.utils import *` would be
circular.
"""
import os
import sys
import glob
import json

from core.utils.config_utils import load_key

INPUT_MANIFEST = "input_manifest.json"
# Files VideoLingo generates itself — never treat them as user input.
GENERATED_AUDIO_NAMES = {"dub.mp3", "normalized_dub.wav"}


def find_video_files(save_path='output'):
    # batch mode: the input video lives OUTSIDE the workspace — the manifest
    # (written by Job.prepare) points at its absolute path
    manifest = _read_input_manifest(save_path)
    if manifest and manifest[1] == "video":
        return manifest[0]

    video_files = [file for file in glob.glob(save_path + "/*") if os.path.splitext(file)[1][1:].lower() in load_key("allowed_video_formats")]
    # change \\ to /, this happen on windows
    if sys.platform.startswith('win'):
        video_files = [file.replace("\\", "/") for file in video_files]
    # Only exclude VideoLingo's own generated outputs — matching by prefix
    # ("output/output") would wrongly reject user files named like
    # "output_extra.mp4". Compare basenames instead.
    generated_video_names = {"output_sub.mp4", "output_dub.mp4"}
    video_files = [file for file in video_files
                   if os.path.basename(file) not in generated_video_names
                   and not file.startswith("output/output")]
    if len(video_files) != 1:
        raise ValueError(f"Number of videos found {len(video_files)} is not unique. Please check.")
    return video_files[0]


def find_audio_files(save_path='output'):
    # batch mode: mirror of find_video_files — trust the manifest first
    manifest = _read_input_manifest(save_path)
    if manifest and manifest[1] == "audio":
        return manifest[0]

    audio_files = [file for file in glob.glob(save_path + "/*") if os.path.splitext(file)[1][1:].lower() in load_key("allowed_audio_formats")]
    if sys.platform.startswith('win'):
        audio_files = [file.replace("\\", "/") for file in audio_files]
    audio_files = [file for file in audio_files if os.path.basename(file) not in GENERATED_AUDIO_NAMES]
    if len(audio_files) != 1:
        raise ValueError(f"Number of audio files found {len(audio_files)} is not unique. Please check.")
    return audio_files[0]


def _safe_find_video_file(save_path='output'):
    try:
        return find_video_files(save_path)
    except ValueError as e:
        if "found 0" in str(e):
            return None
        raise


def _safe_find_audio_file(save_path='output'):
    try:
        return find_audio_files(save_path)
    except ValueError as e:
        if "found 0" in str(e):
            return None
        raise


def _read_input_manifest(save_path='output'):
    manifest_path = os.path.join(save_path, INPUT_MANIFEST)
    if not os.path.exists(manifest_path):
        return None
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    media_file = data.get("path")
    media_type = data.get("type")
    if media_type not in {"video", "audio"} or not media_file or not os.path.exists(media_file):
        return None
    return media_file.replace("\\", "/") if sys.platform.startswith('win') else media_file, media_type


def find_media_file(save_path='output'):
    manifest = _read_input_manifest(save_path)
    if manifest:
        return manifest
    video_file = _safe_find_video_file(save_path)
    if video_file:
        return video_file, "video"
    audio_file = _safe_find_audio_file(save_path)
    if audio_file:
        return audio_file, "audio"
    raise ValueError("No media file found. Please download or upload a media file first.")


def is_audio_only_input(save_path='output'):
    # True when the input is a standalone audio file (no video present).
    # In this case VideoLingo only produces subtitle files; no video output.
    try:
        _, media_type = find_media_file(save_path)
        return media_type == "audio"
    except Exception:
        return False
