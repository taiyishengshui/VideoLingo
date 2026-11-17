"""FR-36 / v1.38: single subtitle-source decision (pure logic, no ffmpeg)."""
from __future__ import annotations

import pytest

from core.services import subtitle_source_service as s


def _write_srt(path, rows):
    parts = []
    for i, (start, end, text) in enumerate(rows, 1):
        parts.append(
            f"{i}\n{s.format_srt_time(start)} --> {s.format_srt_time(end)}\n{text}\n\n"
        )
    path.write_text("".join(parts), encoding="utf-8")
    return path


def test_format_srt_time_is_millisecond_stable():
    assert s.format_srt_time(5.703) == "00:00:05,703"
    assert s.format_srt_time(3661.009) == "01:01:01,009"
    assert s.format_srt_time(0.0) == "00:00:00,000"


def test_parse_srt_blocks_roundtrip(tmp_path):
    p = _write_srt(tmp_path / "a.srt", [(5.703, 8.25, "Hello"), (9.0, 11.5, "World")])
    assert s.parse_srt_blocks(str(p)) == [(5.703, 8.25, "Hello"), (9.0, 11.5, "World")]


def test_parse_srt_blocks_multiline_text(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text("1\n00:00:01,000 --> 00:00:02,000\nline A\nline B\n\n", encoding="utf-8")
    assert s.parse_srt_blocks(str(p))[0][2] == "line A\nline B"


def test_parse_srt_blocks_bad_timecode(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text("1\nBAD --> TIMECODE\ntext\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="bad SRT timecode"):
        s.parse_srt_blocks(str(p))


def test_parse_srt_blocks_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        s.parse_srt_blocks(str(tmp_path / "nope.srt"))


def test_build_dub_src_pairs_text_with_dub_axis(tmp_path):
    _write_srt(tmp_path / "src.srt", [(1.0, 2.0, "One"), (3.0, 4.0, "Two")])
    _write_srt(tmp_path / "dub.srt", [(1.5, 2.9, "一"), (5.0, 7.25, "二")])
    out = s.build_dub_src_srt(str(tmp_path))
    assert s.parse_srt_blocks(out) == [(1.5, 2.9, "One"), (5.0, 7.25, "Two")]


def test_build_dub_src_count_mismatch_refuses(tmp_path):
    _write_srt(tmp_path / "src.srt", [(1.0, 2.0, "One")])
    _write_srt(tmp_path / "dub.srt", [(1.0, 2.0, "一"), (3.0, 4.0, "二")])
    with pytest.raises(ValueError, match="count mismatch"):
        s.build_dub_src_srt(str(tmp_path))


def test_build_dub_src_missing_refuses(tmp_path):
    with pytest.raises(FileNotFoundError, match="subtitle source missing"):
        s.build_dub_src_srt(str(tmp_path))


def test_subtitle_source_missing_key_is_legacy(monkeypatch):
    def boom(key):
        raise KeyError("not found in configuration")

    monkeypatch.setattr("core.utils.config_utils.load_key", boom)
    assert s.subtitle_source() == s.LEGACY


def test_subtitle_source_invalid_is_legacy(monkeypatch):
    monkeypatch.setattr("core.utils.config_utils.load_key", lambda k: "something-else")
    assert s.subtitle_source() == s.LEGACY


def test_subtitle_source_unified(monkeypatch):
    monkeypatch.setattr("core.utils.config_utils.load_key", lambda k: "UNIFIED")
    assert s.subtitle_source() == s.UNIFIED


def test_plan_sub_is_bilingual_in_order():
    assert s.subtitle_plan("sub", "output") == [
        ("output/src.srt", "src"), ("output/trans.srt", "trans")
    ]


def test_plan_dub_legacy_is_unchanged_single_filter(monkeypatch):
    monkeypatch.setattr("core.utils.config_utils.load_key", lambda k: "legacy")
    assert s.subtitle_plan("dub", "output") == [("output/dub.srt", "trans")]


def test_plan_dub_unified_builds_english_line(monkeypatch, tmp_path):
    monkeypatch.setattr("core.utils.config_utils.load_key", lambda k: "unified")
    _write_srt(tmp_path / "src.srt", [(1.0, 2.0, "One"), (3.0, 4.0, "Two")])
    _write_srt(tmp_path / "dub.srt", [(1.5, 2.9, "一"), (5.0, 7.25, "二")])
    plan = s.subtitle_plan("dub", str(tmp_path))
    assert [role for _, role in plan] == ["src", "trans"]
    assert plan[0][0].endswith("dub_src.srt")
    assert "\\" not in plan[0][0] and "\\" not in plan[1][0]
    assert (tmp_path / "dub_src.srt").is_file()


def test_plan_unknown_version_raises():
    with pytest.raises(ValueError, match="unknown subtitle version"):
        s.subtitle_plan("nope")


def test_burn_steps_delegate_to_single_service():
    """Neither burn-in step may hard-code its own subtitle list again."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    step7 = (root / "core/_7_sub_into_vid.py").read_text(encoding="utf-8")
    step12 = (root / "core/_12_dub_to_vid.py").read_text(encoding="utf-8")
    assert 'subtitle_plan("sub", OUTPUT_DIR)' in step7
    assert 'subtitle_plan("dub", "output")' in step12
    assert "DUB_SUB_FILE" not in step12
    assert "output/dub.srt" not in step12
    assert "dub_src.srt" not in step12
