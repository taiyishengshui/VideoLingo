"""FR-28 广告位提醒：SRT 行号锚点、LLM 结果归一、边界定点、补集裁剪、EDL 落盘。

边界定点的关键不变式：
- 起点必须落在 [上一句正文末词, 广告首词] 的过场窗口里，不能切进正文，
  也不能晚于广告开口（那样会留广告头）。
- A 方案 scene_cut 取窗口内**最后一个**切点（第一个通常是离开正文镜头）。
- 成片按 EDL 取补集；EDL 里不勾选的段不参与裁剪。
"""
from __future__ import annotations

import json
import os

import pytest

from core.prompts import get_ad_detect_prompt
from core.services import ad_cut_service as cut
from core.services import ad_service as svc

_SRT = """1
00:00:00,000 --> 00:00:03,000
这是正文第一句

2
00:00:03,000 --> 00:00:06,000
这是正文第二句

3
00:00:09,000 --> 00:00:12,000
本期节目由 Skims 赞助播出

4
00:00:12,000 --> 00:00:15,000
使用优惠码 ABC 享受折扣

5
00:00:18,000 --> 00:00:21,000
回到正文继续聊
"""


def _write_srt(workspace, text=_SRT, name="trans.srt"):
    os.makedirs(workspace, exist_ok=True)
    p = os.path.join(workspace, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _cfg(**over):
    return {**svc.DEFAULTS, **over}


# ---------------- SRT → 行号锚点 ----------------

def test_parse_srt_keeps_continuous_line_numbers():
    lines = svc.parse_srt(_SRT)
    assert [l["n"] for l in lines] == [1, 2, 3, 4, 5]
    assert lines[0]["start"] == 0.0 and lines[0]["end"] == 3.0
    assert lines[2]["start"] == 9.0
    assert lines[2]["text"] == "本期节目由 Skims 赞助播出"


def test_parse_srt_ignores_index_rows_crlf_and_multiline():
    t = "7\r\n00:01:02,500 --> 00:01:04,250\r\n第一行\r\n第二行\r\n\r\n"
    lines = svc.parse_srt(t)
    assert len(lines) == 1
    assert lines[0]["n"] == 1
    assert lines[0]["start"] == 62.5 and lines[0]["end"] == 64.25
    assert lines[0]["text"] == "第一行 第二行"


def test_numbered_transcript_never_renumbers_on_budget():
    lines = svc.parse_srt(_SRT)
    body = svc.numbered_transcript(lines, char_budget=60)
    assert body.startswith("[1] 00:00 ")
    assert "…（转录超长，此处截断；共 5 行）" in body
    assert "[6]" not in body          # 截断不重新编号


def test_read_transcript_prefers_chinese_translation(tmp_path):
    ws = str(tmp_path / "output")
    _write_srt(ws, name="src.srt")
    _write_srt(ws)
    lines, source, label = svc.read_transcript(ws)
    assert source == "trans_srt" and "中文" in label and len(lines) == 5


def test_read_transcript_without_srt_raises(tmp_path):
    with pytest.raises(ValueError, match="trans.srt"):
        svc.read_transcript(str(tmp_path / "empty"))


# ---------------- LLM 结果归一 / 校验 ----------------

def test_normalize_ads_coerces_strings_and_dedupes():
    data = {"ads": [
        {"brand": "Skims", "start_line": "3", "end_line": "4",
         "note": "内衣", "confidence": "HIGH"},
        {"brand": "Skims", "start_line": 3, "end_line": 4, "note": "重复"},
        {"brand": "越界", "start_line": 99, "end_line": 100},
    ]}
    norm = svc.normalize_ads(data, 5)
    assert len(norm) == 1         # 越界的那条被丢掉，完全重复的合并
    assert norm[0]["confidence"] == "high"
    assert norm[0]["start_line"] == 3


def test_validate_ads_empty_is_valid():
    assert svc.validate_ads({"ads": []}, 5)["status"] == "success"


def test_validate_ads_rejects_bad_shape_with_retry_hint():
    r = svc.validate_ads({"ads": [{"brand": "X"}]}, 5)
    assert r["status"] == "error" and "start_line" in r["message"]
    assert svc.validate_ads({"titles": []}, 5)["status"] == "error"


def test_prompt_contains_transcript_and_json_example():
    p = get_ad_detect_prompt("[3] 00:09 本期由 Skims 赞助播出", "中文译文", 5)
    assert "[3] 00:09 本期由 Skims 赞助播出" in p
    assert '{"ads": [{' in p and "start_line" in p
    assert "1..5" in p


def test_prompt_custom_template_without_transcript_gets_injected():
    custom = "只输出 JSON，共 {{line_count}} 行"
    p = get_ad_detect_prompt("[1] 00:00 正文内容", "x", 5, template=custom)
    assert "共 5 行" in p
    assert "转录全文" in p and "[1] 00:00 正文内容" in p


# ---------------- 边界定点 ----------------

@pytest.fixture
def lines():
    return svc.parse_srt(_SRT)


def test_scene_cut_uses_last_cut_minus_pad(lines):
    # 窗口 [6, 9]，两个切点：+0.3 离开正文镜头、+1.4 上赞助卡
    cfg = _cfg(boundary_mode="scene_cut", pre_roll_pad=0.3)
    segs = svc.refine_segments(
        [{"brand": "Skims", "start_line": 3, "end_line": 4,
          "note": "内衣", "confidence": "high"}],
        lines, cfg,
        scene_fn=lambda m, a, b, t: [6.3, 7.4],
        silence_fn=lambda m, a, b: [],
        video="fake.mp4")
    s = segs[0]
    assert s["start"] == 7.1              # 7.4 - 0.3
    assert s["start_basis"] == "scene_cut"
    assert s["transcript_start"] == 9.0
    assert s["start"] > s["prev_end"] and s["start"] < s["transcript_start"]


def test_scene_cut_when_no_cut_falls_back_to_silence_then_prev_end(lines):
    ad = [{"brand": "S", "start_line": 3, "end_line": 4,
           "note": "", "confidence": "high"}]
    cfg = _cfg(boundary_mode="scene_cut", pre_roll_pad=0.3)
    s = svc.refine_segments(ad, lines, cfg,
                            scene_fn=lambda *a: [],
                            silence_fn=lambda m, a, b: [(6.2, 8.0)],
                            video="v.mp4", audio="a.mp3")[0]
    assert s["start_basis"] == "silence" and abs(s["start"] - 7.1) < 1e-6
    s2 = svc.refine_segments(ad, lines, cfg,
                             scene_fn=lambda *a: [],
                             silence_fn=lambda *a: [],
                             video=None, audio=None)[0]
    assert s2["start_basis"] == "prev_end" and s2["start"] == 6.3


def test_prev_end_mode_never_calls_ffmpeg(lines):
    cfg = _cfg(boundary_mode="prev_end", pre_roll_pad=0.3)

    def boom(*a, **k):
        raise AssertionError("prev_end 模式不该调 ffmpeg")

    s = svc.refine_segments(
        [{"brand": "S", "start_line": 3, "end_line": 4,
          "note": "", "confidence": "high"}],
        lines, cfg, scene_fn=boom, silence_fn=boom, video="v.mp4")[0]
    assert s["start"] == 6.3 and s["start_basis"] == "prev_end"


def test_boundaries_never_cut_into_content_or_leave_ad_speech(lines):
    cfg = _cfg(boundary_mode="scene_cut", pre_roll_pad=3.0, post_roll_pad=3.0)
    s = svc.refine_segments(
        [{"brand": "S", "start_line": 3, "end_line": 4,
          "note": "", "confidence": "high"}],
        lines, cfg, scene_fn=lambda *a: [6.1, 8.9],
        silence_fn=lambda *a: [], video="v.mp4")[0]
    assert s["start"] >= s["prev_end"]          # 不切进正文
    assert s["start"] < s["transcript_start"]   # 不晚于广告开口
    assert s["end"] <= s["next_start"]          # 不切进下一段正文
    assert s["end"] > s["start"]


def test_first_ad_at_line_one_has_safe_fallback(lines):
    s = svc.refine_segments(
        [{"brand": "S", "start_line": 1, "end_line": 1,
          "note": "", "confidence": "low"}],
        lines, _cfg(boundary_mode="scene_cut"),
        scene_fn=lambda *a: [], silence_fn=lambda *a: [], video=None)[0]
    assert s["start_basis"] == "fallback" and s["start"] >= 0


# ---------------- 补集裁剪 ----------------

def test_build_keep_is_complement_and_skips_disabled():
    segs = [{"start": 10, "end": 20, "enabled": True},
            {"start": 30, "end": 40, "enabled": False},
            {"start": 50, "end": 60, "enabled": True}]
    assert cut.build_keep(segs, 100) == [(0.0, 10.0), (20.0, 50.0), (60.0, 100)]


def test_build_keep_handles_overlap_and_duration_clamp():
    segs = [{"start": 10, "end": 30, "enabled": True},
            {"start": 20, "end": 25, "enabled": True},
            {"start": 95, "end": 200, "enabled": True}]
    assert cut.build_keep(segs, 100) == [(0.0, 10.0), (30.0, 95.0)]


def test_build_command_uses_trim_concat_and_fades():
    cmd = cut.build_command("in.mp4", [(0, 10), (20, 30)], "out.mp4")
    joined = " ".join(cmd)
    assert "trim=start=0.000:end=10.000" in joined
    assert "atrim=start=20.000:end=30.000" in joined
    assert "concat=n=2:v=1:a=1" in joined
    assert "afade=t=in" in joined and "afade=t=out" in joined
    assert "-movflags" in joined


def test_build_command_without_audio_track():
    cmd = cut.build_command("in.mp4", [(0, 10)], "out.mp4", has_audio=False)
    joined = " ".join(cmd)
    assert "concat=n=1:v=1:a=0" in joined
    assert "atrim" not in joined and "-c:a" not in joined


def test_resolve_source_rejects_unknown_id(tmp_path):
    with pytest.raises(ValueError, match="裁剪源不可用"):
        cut.resolve_source(str(tmp_path), "../../secrets")


# ---------------- Preview sources (FR-28 v1.17) ----------------

@pytest.fixture
def preview_workspace(tmp_path, monkeypatch):
    from core.utils import media_files
    original = tmp_path / "input.mp4"
    original.write_bytes(b"original-video")
    monkeypatch.setattr(media_files, "find_video_files", lambda _w: str(original))
    return tmp_path


def test_preview_prefers_new_burned_video_and_marks_subtitles(preview_workspace):
    ws = preview_workspace
    assert cut.default_source(str(ws)) == "original"
    (ws / "output_sub.mp4").write_bytes(b"burned-video")
    (ws / "output_dub.mp4").write_bytes(b"dubbed-video")
    sources = cut.list_sources(str(ws))
    assert cut.default_source(sources=sources) == "burned"
    assert {s["id"]: s["subtitled"] for s in sources} == {
        "burned": True, "dubbed": True, "original": False}
    assert cut.resolve_source(str(ws), "burned") == str(ws / "output_sub.mp4")


def test_preview_default_handles_dubbed_original_and_empty(preview_workspace):
    ws = preview_workspace
    (ws / "output_dub.mp4").write_bytes(b"dubbed-video")
    assert cut.default_source(str(ws)) == "dubbed"
    (ws / "output_dub.mp4").unlink()
    assert cut.default_source(str(ws)) == "original"
    (ws / "input.mp4").unlink()
    assert cut.default_source(str(ws)) is None


def test_preview_skips_empty_generated_video(preview_workspace):
    ws = preview_workspace
    (ws / "output_sub.mp4").write_bytes(b"")
    assert cut.default_source(str(ws)) == "original"
    assert all(s["id"] != "burned" for s in cut.list_sources(str(ws)))


def test_preview_version_changes_when_same_filename_is_rebuilt(preview_workspace):
    ws = preview_workspace
    path = ws / "output_sub.mp4"
    path.write_bytes(b"old")
    before = cut.list_sources(str(ws))[0]["version"]
    path.write_bytes(b"new-with-more-subtitles")
    after = cut.list_sources(str(ws))[0]["version"]
    assert before != after


def test_ad_context_uses_one_snapshot_for_sources_and_default(monkeypatch):
    from server import ad_api
    sources = [{"id": "original", "subtitled": False}]
    monkeypatch.setattr(ad_api, "context", lambda _w: {})
    monkeypatch.setattr(ad_api, "list_sources", lambda _w: sources)
    def no_rescan(_w):
        raise AssertionError("Default selection must not scan the workspace again")
    monkeypatch.setattr(cut, "list_sources", no_rescan)
    out = ad_api.ad_context()
    assert out["sources"] is sources
    assert out["default_source"] == "original"


def test_preview_range_returns_selected_file(preview_workspace, monkeypatch):
    from fastapi.testclient import TestClient
    from server import ad_api
    from server.app import create_app
    ws = preview_workspace
    burned = b"burned-subtitles-video"
    (ws / "output_sub.mp4").write_bytes(burned)
    monkeypatch.setattr(ad_api, "_WORKSPACE", str(ws))
    client = TestClient(create_app())
    response = client.get("/api/ad/preview?source=burned&v=new", headers={"Range": "bytes=0-5"})
    assert response.status_code == 206
    assert response.content == burned[:6]
    assert response.headers["content-range"] == f"bytes 0-5/{len(burned)}"
    assert client.get("/api/ad/preview?source=original").content == b"original-video"
    assert client.get("/api/ad/preview?source=missing").status_code == 404


# ---------------- MD / EDL 落盘 ----------------

def test_render_md_has_table_and_boundary_comparison():
    seg = {"id": 1, "brand": "Skims", "note": "内衣", "confidence": "high",
           "start": 7.1, "end": 17.8, "transcript_start": 9.0,
           "transcript_end": 15.0, "prev_end": 6.0, "next_start": 18.0,
           "start_basis": "scene_cut", "end_basis": "prev_end",
           "scene_cuts": [6.3, 7.4]}
    md = svc.render_md([seg], {"label": "中文译文", "lines": 5, "chars": 100,
                               "duration": 21, "strategy": "画面切点",
                               "pre_roll_pad": 0.3, "post_roll_pad": 0.3})
    assert "1 段赞助中插" in md
    assert "| 1 | Skims |" in md and "画面切点" in md
    assert "边界对照" in md and "00:00:06.000" in md


def test_render_md_empty_case():
    assert "待裁剪区间（暂无）" in svc.render_md([], {"label": "x"})


def test_edl_atomic_save_load_and_manual_edit(tmp_path):
    ws = str(tmp_path / "output")
    edl = {"meta": {"label": "中文译文", "strategy": "画面切点", "lines": 5,
                    "chars": 100, "duration": 21,
                    "pre_roll_pad": 0.3, "post_roll_pad": 0.3},
           "segments": [{"id": 1, "brand": "S", "start": 7.0, "end": 17.0,
                         "transcript_start": 9.0, "transcript_end": 15.0,
                         "prev_end": 6.0, "next_start": 18.0,
                         "note": "", "start_basis": "scene_cut",
                         "end_basis": "prev_end", "scene_cuts": []}],
           "sponsorblock": []}
    svc.save_edl(ws, edl)
    assert svc.load_edl(ws)["segments"][0]["brand"] == "S"
    edited = [{**edl["segments"][0], "start": 6.5, "end": 17.5,
               "enabled": False}]
    out = svc.update_edl_segments(ws, edited)
    assert out["segments"][0]["start"] == 6.5
    assert out["segments"][0]["enabled"] is False
    assert svc.load_edl(ws)["segments"][0]["end"] == 17.5
    assert os.path.isfile(os.path.join(ws, svc.SLOTS_FILE))
    with pytest.raises(ValueError, match="没有有效"):
        svc.update_edl_segments(ws, [{"id": 1, "start": 20, "end": 10}])


# ---------------- 端到端（假 LLM，不花钱） ----------------

def test_detect_ads_end_to_end_with_fake_llm(tmp_path, monkeypatch, chain_cfg):
    ws = str(tmp_path / "output")
    _write_srt(ws)
    monkeypatch.setattr(svc, "_find_input_video", lambda w: "fake.mp4")
    monkeypatch.setattr(svc, "_find_audio", lambda w: None)

    seen = {}

    def fake_ask(prompt, **kw):
        seen["prompt"] = prompt
        seen["valid"] = kw["valid_def"]({"ads": [
            {"brand": "Skims", "start_line": 3, "end_line": 4,
             "note": "内衣口播", "confidence": "high"}]})
        seen["title"] = kw["log_title"]
        return {"ads": [{"brand": "Skims", "start_line": 3, "end_line": 4,
                         "note": "内衣口播", "confidence": "high"}]}

    res = svc.detect_ads(ws, _ask=fake_ask,
                         _scene_fn=lambda m, a, b, t: [6.3, 7.4],
                         _silence_fn=lambda *a: [])
    assert seen["title"] == "ad_detect"
    assert seen["valid"]["status"] == "success"
    assert "[3] 00:09 本期节目由 Skims 赞助播出" in seen["prompt"]
    assert len(res["segments"]) == 1
    assert res["segments"][0]["start"] == 7.1
    assert res["meta"]["boundary_mode"] == "scene_cut"
    assert os.path.isfile(os.path.join(ws, svc.SLOTS_FILE))
    with open(os.path.join(ws, svc.EDL_FILE), encoding="utf-8") as f:
        assert json.load(f)["segments"][0]["brand"] == "Skims"


def test_context_without_transcript_is_disabled(tmp_path, chain_cfg):
    ctx = svc.context(str(tmp_path / "nothing"))
    assert ctx["transcript"]["available"] is False
    assert ctx["has_edl"] is False
    assert ctx["config"]["boundary_mode"] == "scene_cut"


def test_load_config_reads_yaml_overrides(cfg_path):
    cfg_path("""
llm: {}
ad:
  boundary_mode: prev_end
  pre_roll_pad: 0.5
  scene_threshold: 0.2
  cut_preset: fast
""")
    cfg = svc.load_config()
    assert cfg["boundary_mode"] == "prev_end"
    assert cfg["pre_roll_pad"] == 0.5 and cfg["scene_threshold"] == 0.2
    assert cfg["sponsorblock"] is False       # 缺项退回默认


# --- FR-28 / FR-30 媒体信息接口（帧率 / 时长 / 编码参数） -------------------

def test_media_info_endpoint_uses_source_whitelist(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server import ad_api
    from server.app import create_app

    (tmp_path / "output_sub.mp4").write_bytes(b"x" * 100)
    monkeypatch.setattr(ad_api, "_WORKSPACE", str(tmp_path))
    info = {"source": "burned", "fps": 23.976, "duration": 2880.134,
            "width": 1920, "height": 1080, "video_codec": "h264",
            "audio_codec": "aac", "video_bitrate": 2044485,
            "audio_bitrate": 127357, "has_video": True, "has_audio": True}
    def fake_media_info(w, s):
        # 真实实现里白名单校验在 media_info → resolve_source；这里模拟它
        if s != "burned":
            raise ValueError(f"裁剪源不可用：{s}")
        return info

    monkeypatch.setattr(ad_api, "media_info", fake_media_info)
    client = TestClient(create_app())
    ok = client.get("/api/ad/media-info?source=burned")
    assert ok.status_code == 200
    assert ok.json()["fps"] == 23.976
    assert ok.json()["duration"] == 2880.134      # 「切到片尾」需要真实时长
    # 旧路径别名仍可用，前端升级后不留断点
    assert client.get("/api/ad/frame-rate?source=burned").json()["fps"] == 23.976
    assert client.get("/api/ad/media-info?source=../../etc/passwd").status_code == 404
    assert client.get("/api/ad/media-info?source=missing").status_code == 404


# --- FR-30 自定义切点 -------------------------------------------------------

def _manual(start, end, note="手动"):
    return {"id": int(start), "brand": "手动切点", "note": note,
            "confidence": "manual", "start_basis": "manual", "end_basis": "manual",
            "enabled": True, "start": float(start), "end": float(end),
            "transcript_start": float(start), "transcript_end": float(end),
            "prev_end": None, "next_start": None, "start_line": 0, "end_line": 0,
            "start_gap": 0, "scene_cuts": []}


def test_manual_segments_create_edl_from_scratch(tmp_path):
    ws = str(tmp_path)
    assert svc.load_edl(ws) is None
    edl = svc.update_edl_segments(ws, [_manual(0, 10, "片头"),
                                       _manual(100, 120, "中间")])
    assert edl["meta"]["source"] == "manual"
    assert len(edl["segments"]) == 2
    assert svc.load_edl(ws)["segments"][0]["start"] == 0.0
    md = (tmp_path / svc.SLOTS_FILE).read_text(encoding="utf-8")
    assert "2 段手动切点" in md and "手动指定" in md
    assert "nan" not in md.lower()
    assert "## 边界对照" not in md          # 全是手动段时不渲染语义对照表


def test_manual_head_middle_tail_complement(tmp_path):
    ws = str(tmp_path)
    edl = svc.update_edl_segments(ws, [_manual(0, 10), _manual(100, 120),
                                       _manual(280, 300)])
    keep = cut.build_keep(edl["segments"], 300.0)
    assert keep == [(10.0, 100.0), (120.0, 280.0)]
    assert sum(b - a for a, b in keep) == 250.0


def test_manual_segments_render_alongside_ads():
    ads = [{"id": 0, "brand": "Skims", "note": "口播", "confidence": "high",
            "start_basis": "scene_cut", "end_basis": "scene_cut",
            "enabled": True, "start": 50.0, "end": 60.0,
            "transcript_start": 51.5, "transcript_end": 60.0,
            "prev_end": 48.0, "next_start": 61.0, "start_line": 3,
            "end_line": 10, "start_gap": 3.5, "scene_cuts": [49.2, 50.6]}]
    md = svc.render_md(ads + [_manual(0, 10)], {"label": "trans.srt", "lines": 100})
    assert "1 段赞助中插 + 1 段手动切点" in md
    assert "## 边界对照" in md               # 有赞助段时对照表还在
    assert "手动指定 | —" in md              # 手动段不显示「比转录提前」
    assert "NaN" not in md and "nan" not in md


def test_detect_ads_preserves_manual_segments(tmp_path, monkeypatch, chain_cfg):
    ws = str(tmp_path)
    _write_srt(ws)
    # 先只有手动切点
    svc.update_edl_segments(ws, [_manual(0, 1, "片头")])
    monkeypatch.setattr(svc, "_find_input_video", lambda w: "fake.mp4")
    monkeypatch.setattr(svc, "_find_audio", lambda w: None)

    def fake_ask(prompt, **kw):
        kw["valid_def"]({"ads": [{"brand": "Skims", "start_line": 3,
                                  "end_line": 4, "note": "口播",
                                  "confidence": "high"}]})
        return {"ads": [{"brand": "Skims", "start_line": 3, "end_line": 4,
                         "note": "口播", "confidence": "high"}]}

    res = svc.detect_ads(ws, _ask=fake_ask,
                         _scene_fn=lambda m, a, b, t: [6.3, 7.4],
                         _silence_fn=lambda *a: [])
    assert len(res["segments"]) == 2         # 1 赞助 + 1 手动
    assert {s["brand"] for s in res["segments"]} == {"Skims", "手动切点"}
    assert [s["id"] for s in res["segments"]] == [0, 1]   # 统一重编号
    md = (tmp_path / svc.SLOTS_FILE).read_text(encoding="utf-8")
    assert "1 段赞助中插 + 1 段手动切点" in md


# ---------------- FR-32 识别段也能删 / 清空 / 恢复 -------------------------

def _auto(start, end, brand="Skims", sid=0):
    return {"id": sid, "brand": brand, "note": "口播", "confidence": "high",
            "start_basis": "scene_cut", "end_basis": "scene_cut",
            "enabled": True, "start": float(start), "end": float(end),
            "transcript_start": float(start) + 1.5, "transcript_end": float(end),
            "prev_end": float(start) - 1.0, "next_start": float(end) + 1.0,
            "start_line": 3, "end_line": 10, "start_gap": 2.5,
            "scene_cuts": []}


def test_update_edl_can_clear_all_segments(tmp_path):
    ws = str(tmp_path)
    svc.update_edl_segments(ws, [_auto(50, 60), _auto(100, 110, "Wayfair", 1)])
    edl = svc.update_edl_segments(ws, [])
    assert edl["segments"] == []
    assert len(edl["dismissed"]) == 2          # 删掉的自动段留档
    assert svc.load_edl(ws)["segments"] == []
    md = (tmp_path / svc.SLOTS_FILE).read_text(encoding="utf-8")
    assert "待裁剪区间（暂无）" in md and "已手动删除 2 段" in md


def test_invalid_payload_still_rejected_instead_of_clearing(tmp_path):
    ws = str(tmp_path)
    svc.update_edl_segments(ws, [_auto(50, 60)])
    with pytest.raises(ValueError, match="没有有效"):
        svc.update_edl_segments(ws, [{"id": 0, "start": 20, "end": 10}])
    assert len(svc.load_edl(ws)["segments"]) == 1     # 没被误清空


def test_disabling_or_nudging_a_segment_is_not_a_delete(tmp_path):
    ws = str(tmp_path)
    svc.update_edl_segments(ws, [_auto(50, 60)])
    edl = svc.update_edl_segments(ws, [{**_auto(50.4, 60), "enabled": False}])
    assert edl["dismissed"] == []


def test_is_dismissed_matches_after_boundary_nudge():
    d = [_auto(50, 60)]
    assert svc.is_dismissed(_auto(50.3, 60.2), d)
    assert not svc.is_dismissed(_auto(80, 90), d)
    assert not svc.is_dismissed(_auto(50, 60, brand="Other"), d)


def test_restore_clears_dismissed_memory(tmp_path):
    ws = str(tmp_path)
    svc.update_edl_segments(ws, [_auto(50, 60)])
    assert len(svc.update_edl_segments(ws, [])["dismissed"]) == 1
    out = svc.update_edl_segments(ws, [_auto(50, 60, sid=7)], dismissed=[])
    assert out["dismissed"] == [] and len(out["segments"]) == 1


def test_detect_ads_skips_deleted_but_keeps_manual(tmp_path, monkeypatch, chain_cfg):
    ws = str(tmp_path)
    _write_srt(ws)
    monkeypatch.setattr(svc, "_find_input_video", lambda w: "fake.mp4")
    monkeypatch.setattr(svc, "_find_audio", lambda w: None)

    def fake_ask(prompt, **kw):
        ads = {"ads": [{"brand": "Skims", "start_line": 3, "end_line": 4,
                        "note": "口播", "confidence": "high"}]}
        kw["valid_def"](ads)
        return ads

    def detect():
        return svc.detect_ads(ws, _ask=fake_ask,
                              _scene_fn=lambda m, a, b, t: [6.3, 7.4],
                              _silence_fn=lambda *a: [])

    first = detect()
    assert [s["brand"] for s in first["segments"]] == ["Skims"]
    # 页面删掉这段识别结果，同时留一个手动切点
    svc.update_edl_segments(ws, [_manual(0, 1, "片头")])
    second = detect()
    assert [s["brand"] for s in second["segments"]] == ["手动切点"]
    assert len(second["dismissed"]) == 1
    # 点「恢复已删除」→ 重新识别又回来
    svc.update_edl_segments(ws, [{**first["segments"][0], "id": 9},
                                 _manual(0, 1, "片头")], dismissed=[])
    third = detect()
    assert {s["brand"] for s in third["segments"]} == {"Skims", "手动切点"}
    assert third["dismissed"] == []


def test_put_edl_accepts_empty_and_dismissed_override(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server import ad_api
    from server.app import create_app

    monkeypatch.setattr(ad_api, "_WORKSPACE", str(tmp_path))
    client = TestClient(create_app())
    ok = client.put("/api/ad/edl", json={"segments": [_auto(50, 60)]})
    assert ok.status_code == 200
    cleared = client.put("/api/ad/edl", json={"segments": []})
    assert cleared.status_code == 200
    assert cleared.json()["edl"]["segments"] == []
    assert len(cleared.json()["edl"]["dismissed"]) == 1
    restored = client.put("/api/ad/edl", json={
        "segments": [{**_auto(50, 60), "id": 3}], "dismissed": []})
    assert restored.status_code == 200
    assert restored.json()["edl"]["dismissed"] == []


# ---------------- FR-29 扩展到裁剪：编码参数不写第二份（G-01） --------------

def test_build_command_uses_injected_encode_args():
    args = ["-c:v", "h264_nvenc", "-b:v", "390k", "-c:a", "aac", "-b:a", "109k"]
    cmd = cut.build_command("in.mp4", [(0, 10)], "out.mp4", encode_args=args)
    joined = " ".join(cmd)
    assert "-b:v 390k" in joined and "-c:a aac -b:a 109k" in joined
    assert "-crf" not in joined and "libx264" not in joined
    assert "192k" not in joined                 # 旧写死值必须消失
    assert cmd[-1] == "out.mp4"
    assert cmd[-3:-1] == ["-movflags", "+faststart"]


def test_build_command_falls_back_to_crf_without_encode_args():
    cmd = cut.build_command("in.mp4", [(0, 10)], "out.mp4")
    joined = " ".join(cmd)
    assert "-crf 20" in joined and "libx264" in joined and "192k" in joined


def test_encode_args_for_returns_none_when_match_source_off(monkeypatch):
    from core.services import encode_service as enc
    monkeypatch.setattr(enc, "load_config", lambda: {"match_source": False})
    args, desc = cut._encode_args_for("x.mp4", "veryfast", 20, True)
    assert args is None and "旧行为" in desc


def _cut_probe_info():
    from core.utils import media_probe
    info = media_probe.empty_info()
    info.update({"has_video": True, "has_audio": True, "video_codec": "h264",
                 "video_bitrate": 390302, "audio_bitrate": 109016,
                 "audio_codec": "opus", "fps_raw": "24000/1001",
                 "pix_fmt": "yuv420p"})
    return info


def test_encode_args_for_cut_uses_cq_and_probes_original(monkeypatch):
    """v1.18：裁剪走 CQ30（二次转码放宽），且探测原片而非已压过的产物。"""
    from core.services import encode_service as enc
    from core.utils import media_probe

    info = _cut_probe_info()
    probed = []
    monkeypatch.setattr(media_probe, "probe_media",
                        lambda p: (probed.append(p), info)[1])
    monkeypatch.setattr(enc, "load_config", lambda: dict(enc.DEFAULTS))
    args, desc = cut._encode_args_for(
        "output/output_sub.mp4", "veryfast", 20, True,
        probe_source="output/original.mp4")
    assert probed == ["output/original.mp4"]      # 不拿已压一次的参数当目标
    assert args and args[args.index("-cq") + 1] == "30"
    assert args[args.index("-b:v") + 1] == "0" and "-maxrate" not in args
    assert args[args.index("-c:a") + 1] == "aac"  # 滤镜输出不能 copy
    assert "copy" not in args
    assert "二次转码放宽" in desc and "帧率 24000/1001" in desc


def test_encode_args_for_cut_falls_back_to_src_without_probe_source(monkeypatch):
    from core.services import encode_service as enc
    from core.utils import media_probe

    info = _cut_probe_info()
    probed = []
    monkeypatch.setattr(media_probe, "probe_media",
                        lambda p: (probed.append(p), info)[1])
    monkeypatch.setattr(enc, "load_config", lambda: dict(enc.DEFAULTS))
    args, _ = cut._encode_args_for("output/output_sub.mp4", "veryfast", 20, True)
    assert args and probed == ["output/output_sub.mp4"]


def test_encode_args_for_cut_bitrate_mode_still_aligns_source(monkeypatch):
    """rate_control=bitrate 回退：仍按源码率锁峰值（v1.7 行为逐字保留）。"""
    from core.services import encode_service as enc
    from core.utils import media_probe

    monkeypatch.setattr(media_probe, "probe_media", lambda _p: _cut_probe_info())
    monkeypatch.setattr(enc, "load_config",
                        lambda: {**enc.DEFAULTS, "rate_control": "bitrate"})
    args, desc = cut._encode_args_for("x.mp4", "veryfast", 20, True)
    assert args and args[args.index("-b:v") + 1] == "390k"
    assert args[args.index("-c:a") + 1] == "aac"
    assert "copy" not in args
    assert "码率 390k" in desc and "帧率 24000/1001" in desc
