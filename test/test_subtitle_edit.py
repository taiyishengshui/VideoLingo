"""FR-46: controlled subtitle editing, path safety and admission races."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.services import subtitle_edit_service as service
from server import subtitle_edit_api as api

SRT = "1\n00:00:00,100 --> 00:00:01,500\nHello\n\n2\n00:00:02,000 --> 00:00:03,000\nWorld\n"


def _workspace(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    (out / "src.srt").write_text(SRT, encoding="utf-8")
    (out / "trans.srt").write_text(SRT, encoding="utf-8")
    return tmp_path


def test_parse_serialize_roundtrip():
    rows = service.parse_srt(SRT, 10)
    assert rows == [
        {"start": 0.1, "end": 1.5, "text": "Hello"},
        {"start": 2.0, "end": 3.0, "text": "World"},
    ]
    assert service.parse_srt(service.serialize_srt(rows, 10), 10) == rows


def test_empty_track_roundtrip(tmp_path: Path):
    ws = _workspace(tmp_path)
    track = service.read_track(str(ws), "src.srt", 10)
    saved = service.save_track(str(ws), "src.srt", track["revision"], [], 10)
    assert saved["rows"] == [] and saved["revision"] == service.revision("")
    assert service.read_track(str(ws), "src.srt", 10)["rows"] == []
    assert (ws / "output" / "src.srt").read_text(encoding="utf-8") == ""


def test_reject_non_finite_and_invalid_rows():
    base = {"start": 0.0, "end": 1.0, "text": "ok"}
    for bad in (float("nan"), float("inf"), float("-inf")):
        for key in ("start", "end"):
            with pytest.raises(ValueError):
                service.serialize_srt([{**base, key: bad}])
    with pytest.raises(ValueError):
        service.serialize_srt([{"start": 0, "text": "missing"}])
    with pytest.raises(ValueError):
        service.serialize_srt("not-a-list")
    with pytest.raises(ValueError):
        service.serialize_srt([base, {"start": -0.5, "end": 2, "text": "backwards"}])


def test_serialize_and_save_allow_out_of_order_overlapping_rows(tmp_path: Path):
    ws = _workspace(tmp_path)
    rows = [
        {"start": 5.0, "end": 7.0, "text": "Later first"},
        {"start": 2.0, "end": 6.0, "text": "Earlier second"},
    ]
    track = service.read_track(str(ws), "src.srt", 10)
    saved = service.save_track(str(ws), "src.srt", track["revision"], rows, 10)
    assert saved["rows"] == rows
    assert service.read_track(str(ws), "src.srt", 10)["rows"] == rows


def test_revision_conflict_preserves_file(tmp_path: Path):
    ws = _workspace(tmp_path)
    path = ws / "output" / "src.srt"
    original = path.read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed outside"):
        service.save_track(str(ws), "src.srt", "stale", [], 10)
    assert path.read_text(encoding="utf-8") == original
    assert not list((ws / "output").glob(".src.srt.*.tmp"))


def test_whitelist_and_symlink_boundary(tmp_path: Path):
    ws = _workspace(tmp_path)
    for name in ("dub.srt", "../src.srt", "src.srt/evil"):
        with pytest.raises(ValueError):
            service.subtitle_path(str(ws), name)
    target = ws / "output" / "src.srt"
    target.unlink()
    outside = tmp_path / "outside.srt"
    outside.write_text(SRT, encoding="utf-8")
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="unsafe"):
        service.subtitle_path(str(ws), "src.srt")


@pytest.mark.parametrize("manager_state,engine_state", [
    ("running", "idle"), ("paused", "idle"),
    ("idle", "running"), ("idle", "stopping"),
])
def test_busy_state_rejects_context(monkeypatch, manager_state, engine_state):
    import server.task_manager as tm
    import core.batch_engine as be
    monkeypatch.setattr(tm.MANAGER, "state", manager_state)
    monkeypatch.setattr(be.ENGINE, "state", engine_state)
    with pytest.raises(api.HTTPException) as exc:
        api.get_context()
    assert exc.value.status_code == 409


def test_batch_scan_is_rejected_while_busy(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import core.batch_engine as be
    import server.batch_api as batch_api
    monkeypatch.setattr(be.ENGINE, "state", "running")
    app = FastAPI()
    app.include_router(batch_api.router)
    with TestClient(app) as client:
        r = client.post("/api/batch/scan", json={"root": "."})
    assert r.status_code == 409


def test_preview_uses_source_extension_mime(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import server.task_manager as tm
    import core.batch_engine as be
    monkeypatch.setattr(tm.MANAGER, "state", "idle")
    monkeypatch.setattr(be.ENGINE, "state", "idle")
    source = tmp_path / "clip.mkv"
    source.write_bytes(b"0123456789")
    monkeypatch.setattr(api, "_with_context", lambda scope, video: (str(tmp_path), str(source), 10.0, 30.0, "auto"))
    monkeypatch.setattr(api, "_ensure_idle", lambda: None)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        r = client.get("/api/subtitles/preview", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.headers["content-type"].startswith("video/x-matroska")
    assert r.content == b"0123"


def test_media_info_reports_detected_and_fallback_fps(monkeypatch):
    monkeypatch.setattr(api, "probe_media", lambda path: {"duration": 12.5, "fps": 23.976})
    assert api._media_info("clip.mp4") == (12.5, 23.976, "auto")
    monkeypatch.setattr(api, "probe_media", lambda path: {"duration": 12.5, "fps": 0})
    assert api._media_info("clip.mp4") == (12.5, 30.0, "fallback")


def test_context_requires_positive_duration(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    import server.task_manager as tm
    import core.batch_engine as be
    monkeypatch.setattr(tm.MANAGER, "state", "idle")
    monkeypatch.setattr(be.ENGINE, "state", "idle")
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    monkeypatch.setattr(api, "find_media_file", lambda out: (str(source), "video"))
    monkeypatch.setattr(api, "probe_media", lambda path: {"duration": 0})
    with pytest.raises(api.HTTPException) as exc:
        api.get_context()
    assert exc.value.status_code == 422


def test_context_urlencodes_batch_video(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    import server.task_manager as tm
    import core.batch_engine as be
    from core.utils.job_context import Job
    source = tmp_path / "a & b.mp4"
    source.write_bytes(b"x")
    job = Job(str(tmp_path), str(source))
    out = tmp_path / "_log" / "a & b" / "output"
    out.mkdir(parents=True)
    monkeypatch.setattr(job, "workspace", str(out.parent))
    (out / "src.srt").write_text(SRT, encoding="utf-8")
    (out / "trans.srt").write_text(SRT, encoding="utf-8")
    monkeypatch.setattr(tm.MANAGER, "state", "idle")
    monkeypatch.setattr(be.ENGINE, "state", "idle")
    monkeypatch.setattr(be.ENGINE, "jobs", [job])
    monkeypatch.setattr(api, "probe_media", lambda path: {"duration": 10.0})
    data = api.get_context("batch", str(source))
    assert "%26" in data["preview_url"] and "%3A" in data["preview_url"]
    assert data["tracks"]["src.srt"]["rows"][0]["text"] == "Hello"


def test_batch_video_identifier_is_windows_path_safe(monkeypatch, tmp_path: Path):
    import core.batch_engine as be
    from core.utils.job_context import Job
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    job = Job(str(tmp_path), str(source))
    monkeypatch.setattr(be.ENGINE, "jobs", [job])
    monkeypatch.setattr(be.ENGINE, "state", "idle")
    monkeypatch.setattr(api, "_ensure_idle", lambda: None)
    workspace, resolved = api._resolve_context("batch", str(source).lower().replace("/", "\\"))
    assert workspace == job.workspace
    assert os.path.normcase(os.path.realpath(resolved)) == os.path.normcase(os.path.realpath(str(source)))


# --- FR-47: step-6 SRTs separate blocks with three CRLFs (two blank lines) ---
PIPELINE_SRT = (
    "1\n00:00:00,100 --> 00:00:01,500\nHello\n\n\n"
    "2\n00:00:02,000 --> 00:00:03,000\nWorld\n\n\n"
    "3\n00:00:03,000 --> 00:00:04,000\nAgain\n"
)


def test_parse_pipeline_srt_with_extra_blank_lines():
    rows = service.parse_srt(PIPELINE_SRT.replace("\n", "\r\n"), 10)
    assert [row["text"] for row in rows] == ["Hello", "World", "Again"]
    assert rows[0]["start"] == 0.1 and rows[-1]["end"] == 4.0
    assert service.parse_srt(service.serialize_srt(rows, 10), 10) == rows


def test_parse_srt_tolerates_bom_mixed_newlines_and_whitespace_blanks():
    raw = ("\ufeff1\r\n00:00:00,100 --> 00:00:01,500\r\nLine A\nLine B"
           "\r\n \n \r\n\r\n2\n00:00:02,000 --> 00:00:03,000\nWorld\r")
    rows = service.parse_srt(raw, 10)
    assert [row["text"] for row in rows] == ["Line A\nLine B", "World"]


def test_parse_srt_reports_block_index_and_stays_strict():
    with pytest.raises(ValueError, match="block 2"):
        service.parse_srt("1\n00:00:00,100 --> 00:00:01,500\nHi\n\n\n"
                          "BAD\n00:00:02,000 --> 00:00:03,000\nWorld\n", 10)
    with pytest.raises(ValueError, match="block 2"):
        service.parse_srt("1\n00:00:00,100 --> 00:00:01,500\nHi\n\n\n"
                          "2\nXX --> YY\nWorld\n", 10)
    with pytest.raises(ValueError, match="block 1"):
        service.parse_srt("1\n00:00:00,100 --> 00:00:01,500\n\n\n"
                          "2\n00:00:02,000 --> 00:00:03,000\nWorld\n", 10)


def test_serialize_rejects_blank_line_inside_text():
    with pytest.raises(ValueError, match="blank line"):
        service.serialize_srt([{"start": 0, "end": 1, "text": "A\n\nB"}])


def test_save_pipeline_track_normalizes_and_keeps_all_rows(tmp_path: Path):
    from core.services import subtitle_source_service as source_service
    ws = tmp_path
    out = ws / "output"
    out.mkdir()
    path = out / "src.srt"
    path.write_bytes(PIPELINE_SRT.replace("\n", "\r\n").encode("utf-8"))

    track = service.read_track(str(ws), "src.srt", 10)
    assert len(track["rows"]) == 3
    saved = service.save_track(str(ws), "src.srt", track["revision"], track["rows"], 10)
    assert saved["rows"] == track["rows"]
    assert service.read_track(str(ws), "src.srt", 10)["rows"] == track["rows"]

    text = path.read_text(encoding="utf-8")
    assert "\r" not in text and "\n\n\n" not in text
    assert text.count("\n\n") == 2
    assert len(source_service.parse_srt_blocks(str(path))) == 3


def test_context_reads_pipeline_srt_without_422(monkeypatch, tmp_path: Path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import server.task_manager as tm
    import core.batch_engine as be
    out = tmp_path / "output"
    out.mkdir()
    for name in ("src.srt", "trans.srt"):
        (out / name).write_bytes(PIPELINE_SRT.replace("\n", "\r\n").encode("utf-8"))
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    monkeypatch.setattr(tm.MANAGER, "state", "idle")
    monkeypatch.setattr(be.ENGINE, "state", "idle")
    monkeypatch.setattr(api, "_with_context",
                        lambda scope, video: (str(tmp_path), str(source), 10.0, 24.0, "auto"))
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.get("/api/subtitles/context")
    assert response.status_code == 200
    assert response.json()["fps"] == 24.0
    assert response.json()["fps_source"] == "auto"
    assert len(response.json()["tracks"]["src.srt"]["rows"]) == 3
