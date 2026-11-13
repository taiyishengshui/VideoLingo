"""FR-29：烧录 / 合成输出的编码参数对齐原视频（工具层探测 + 服务层决策）。"""
from __future__ import annotations

import json
import types

import pytest

from core.services import encode_service as enc
from core.services.ad_cut_service import probe_frame_rate
from core.utils import media_probe
from core.utils.media_probe import empty_info, probe_media

_SAMPLE = {
    "streams": [
        {"codec_type": "video", "codec_name": "av1", "width": 1920, "height": 1080,
         "pix_fmt": "yuv420p", "avg_frame_rate": "24000/1001",
         "r_frame_rate": "24000/1001", "bit_rate": "408326"},
        {"codec_type": "audio", "codec_name": "opus", "sample_rate": "48000",
         "channels": 2, "bit_rate": "109016"},
    ],
    "format": {"duration": "2880.134", "size": "187801634",
               "bit_rate": "521646"},
}


def _mock_run(monkeypatch, payload):
    body = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(media_probe.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout=body))


def _info(**over):
    d = empty_info()
    d.update({"has_video": True, "has_audio": True,
              "video_codec": "av1", "video_bitrate": 408326,
              "audio_codec": "opus", "audio_bitrate": 109016,
              "fps": 23.976, "fps_raw": "24000/1001", "pix_fmt": "yuv420p",
              "width": 1920, "height": 1080, "duration": 2880.134,
              "format_bitrate": 521646})
    d.update(over)
    return d


def _cfg(**over):
    return {**enc.DEFAULTS, **over}


# ---------------- 工具层：ffprobe 解析 ----------------

def test_probe_media_reads_full_stream_info(monkeypatch):
    _mock_run(monkeypatch, _SAMPLE)
    i = probe_media("x.mp4")
    assert i["video_codec"] == "av1" and i["audio_codec"] == "opus"
    assert i["video_bitrate"] == 408326 and i["audio_bitrate"] == 109016
    assert i["fps_raw"] == "24000/1001" and i["fps"] == 23.976024
    assert i["pix_fmt"] == "yuv420p" and i["duration"] == 2880.134
    assert i["width"] == 1920 and i["height"] == 1080
    assert i["has_video"] and i["has_audio"]


def test_probe_media_falls_back_to_r_frame_rate(monkeypatch):
    p = json.loads(json.dumps(_SAMPLE))
    p["streams"][0]["avg_frame_rate"] = "0/0"
    p["streams"][0]["r_frame_rate"] = "25/1"
    _mock_run(monkeypatch, p)
    assert probe_media("x.mp4")["fps"] == 25.0


def test_probe_media_estimates_video_bitrate_from_format(monkeypatch):
    p = json.loads(json.dumps(_SAMPLE))
    del p["streams"][0]["bit_rate"]
    _mock_run(monkeypatch, p)
    assert probe_media("x.mp4")["video_bitrate"] == 521646 - 109016


def test_probe_media_estimates_from_size_when_no_bitrate(monkeypatch):
    p = {"streams": [{"codec_type": "video", "codec_name": "h264",
                      "avg_frame_rate": "30/1"}],
         "format": {"duration": "100", "size": "10000000"}}
    _mock_run(monkeypatch, p)
    i = probe_media("x.mp4")
    assert i["video_bitrate"] == 800000       # 10MB / 100s
    assert not i["has_audio"]


@pytest.mark.parametrize("body", [
    "", "{}", "not-json",
    '{"streams":[{"codec_type":"video","avg_frame_rate":"0/0"}]}',
    '{"streams":[{"codec_type":"video","avg_frame_rate":"9999/1"}]}',
])
def test_probe_media_bad_output_never_raises(monkeypatch, body):
    _mock_run(monkeypatch, body)
    i = probe_media("x.mp4")
    assert i["fps"] == 0.0 and i["video_bitrate"] == 0


def test_probe_media_missing_ffprobe_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise OSError("ffprobe not found")
    monkeypatch.setattr(media_probe.subprocess, "run", boom)
    assert probe_media("x.mp4") == empty_info()


def test_probe_frame_rate_is_thin_wrapper_of_probe_media(monkeypatch):
    """帧率解析只允许一份（G-01）：ad_cut_service 只是转发。"""
    monkeypatch.setattr(media_probe, "probe_media",
                        lambda p: {"fps": 23.976024})
    assert probe_frame_rate("x.mp4") == 23.976


# ---------------- 服务层：编码参数决策 ----------------

def test_build_encode_args_defaults_to_constant_quality():
    """v1.18 默认 CQ：不再用 -maxrate/-bufsize 削峰，复杂镜头自己拿码率。"""
    args, desc = enc.build_encode_args("x.mp4", gpu=True,
                                       cfg=_cfg(), info=_info())
    assert args[:2] == ["-c:v", "h264_nvenc"]
    assert args[args.index("-rc") + 1] == "vbr"
    assert args[args.index("-cq") + 1] == "32"
    assert args[args.index("-b:v") + 1] == "0"
    assert "-maxrate" not in args and "-bufsize" not in args
    assert args[args.index("-spatial_aq") + 1] == "1"
    assert args[args.index("-temporal_aq") + 1] == "1"
    assert args[args.index("-rc-lookahead") + 1] == "32"
    assert args[args.index("-multipass") + 1] == "fullres"
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-r") + 1] == "24000/1001"
    assert args[args.index("-c:a") + 1] == "copy"
    assert "CQ32" in desc and "直通" in desc and "AV1" in desc


def test_bitrate_mode_still_matches_source():
    """v1.7 旧行为逐字保留，rate_control=bitrate 可回退。"""
    args, desc = enc.build_encode_args(
        "x.mp4", gpu=True, cfg=_cfg(rate_control="bitrate"), info=_info())
    assert args == ["-c:v", "h264_nvenc",
                    "-b:v", "408k", "-maxrate", "408k", "-bufsize", "816k",
                    "-pix_fmt", "yuv420p", "-r", "24000/1001", "-c:a", "copy"]
    assert "408k" in desc and "直通" in desc and "AV1" in desc


def test_build_encode_args_software_encoder():
    args, _ = enc.build_encode_args("x.mp4", gpu=False,
                                    cfg=_cfg(), info=_info())
    assert args[0:2] == ["-c:v", "libx264"]
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "copy"


def test_build_encode_args_reencodes_audio_for_dubbing():
    args, desc = enc.build_encode_args("x.mp4", gpu=True, audio_reencode=True,
                                       cfg=_cfg(), info=_info())
    assert args[-4:] == ["-c:a", "aac", "-b:a", "109k"]
    assert "须重编码" in desc


def test_build_encode_args_match_source_false_keeps_old_behaviour():
    args, _ = enc.build_encode_args("x.mp4", gpu=True,
                                    cfg=_cfg(match_source=False), info=_info())
    assert args == ["-c:v", "h264_nvenc"]      # 旧行为：只有编码器，没有码率
    args2, _ = enc.build_encode_args("x.mp4", gpu=False,
                                     cfg=_cfg(match_source=False), info=_info())
    assert args2 == []


def test_build_encode_args_passthrough_fps():
    args, _ = enc.build_encode_args("x.mp4", gpu=True,
                                    cfg=_cfg(fps_mode="passthrough"), info=_info())
    assert "-r" not in args
    assert args[args.index("-fps_mode") + 1] == "passthrough"


def test_build_encode_args_bitrate_scale():
    args, _ = enc.build_encode_args(
        "x.mp4", gpu=True,
        cfg=_cfg(rate_control="bitrate", bitrate_scale=2.0), info=_info())
    assert "817k" in args        # 408326 × 2 / 1000 = 816.652 → 817


def test_build_encode_args_min_bitrate_floor():
    args, _ = enc.build_encode_args(
        "x.mp4", gpu=True,
        cfg=_cfg(rate_control="bitrate", min_video_bitrate=500),
        info=_info(video_bitrate=100000))
    assert "500k" in args


def test_build_encode_args_when_copy_audio_disabled():
    args, _ = enc.build_encode_args("x.mp4", gpu=True,
                                    cfg=_cfg(copy_audio=False), info=_info())
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "aac"


def test_build_encode_args_without_audio_stream():
    args, _ = enc.build_encode_args("x.mp4", gpu=True, cfg=_cfg(),
                                    info=_info(has_audio=False, audio_bitrate=0))
    assert "-c:a" not in args


def test_build_encode_args_unreadable_source_falls_back():
    args, desc = enc.build_encode_args("x.mp4", gpu=True, cfg=_cfg(),
                                       info=empty_info())
    assert args == ["-c:v", "h264_nvenc"]
    assert "读不到输入视频流" in desc


def test_encoder_for_av1_falls_back_to_h264():
    # RTX 3060 硬件不支持 AV1 编码，软编又太慢 → 输出保持 H264
    assert enc._encoder_for("av1", True) == "h264_nvenc"
    assert enc._encoder_for("av1", False) == "libx264"
    assert enc._encoder_for("hevc", True) == "hevc_nvenc"
    assert enc._encoder_for("h264", True) == "h264_nvenc"


def test_load_config_reads_yaml_overrides(cfg_path):
    cfg_path("""
llm: {}
encode:
  match_source: false
  bitrate_scale: 1.5
  fps_mode: passthrough
  copy_audio: false
  min_video_bitrate: 300
""")
    cfg = enc.load_config()
    assert cfg["match_source"] is False
    assert cfg["bitrate_scale"] == 1.5 and cfg["min_video_bitrate"] == 300
    assert cfg["fps_mode"] == "passthrough" and cfg["copy_audio"] is False


# ---------------- 速度预设按编码器归一（裁剪会传入 ad.cut_preset） --------

def test_preset_is_mapped_for_nvenc_not_passthrough():
    # x264 的 veryfast 不是 NVENC 合法 -preset，直接透传会让 ffmpeg 报错
    args, desc = enc.build_encode_args(
        "x.mp4", gpu=True, info=_info(),
        cfg=_cfg(rate_control="bitrate"), preset="veryfast")
    assert args[args.index("-preset") + 1] == "fast"
    assert "veryfast" not in args
    assert "由 veryfast 映射" in desc


def test_preset_kept_verbatim_for_libx264():
    args, _ = enc.build_encode_args(
        "x.mp4", gpu=False, info=_info(),
        cfg=_cfg(rate_control="bitrate"), preset="veryfast")
    assert args[args.index("-preset") + 1] == "veryfast"


def test_unknown_preset_is_ignored():
    args, _ = enc.build_encode_args(
        "x.mp4", gpu=True, info=_info(),
        cfg=_cfg(rate_control="bitrate"), preset="nonsense")
    assert "-preset" not in args


def test_no_preset_by_default_in_bitrate_mode():
    args, _ = enc.build_encode_args(
        "x.mp4", gpu=True, info=_info(), cfg=_cfg(rate_control="bitrate"))
    assert "-preset" not in args


# ---------------- v1.18 恒定质量模式 ----------------

def test_cq_mode_uses_cq_preset_not_caller_preset():
    """CQ 模式的速度预设由 encode.cq_preset 决策，入口传的 preset 不生效。"""
    args, _ = enc.build_encode_args(
        "x.mp4", gpu=True, info=_info(),
        cfg=_cfg(cq_preset="slow"), preset="veryfast")
    assert args[args.index("-preset") + 1] == "slow"
    assert "veryfast" not in args


def test_cq_level_and_gop_are_configurable():
    args, desc = enc.build_encode_args(
        "x.mp4", gpu=True, info=_info(),
        cfg=_cfg(cq_level=26, cq_gop=48))
    assert args[args.index("-cq") + 1] == "26"
    assert args[args.index("-g") + 1] == "48"
    assert "GOP 48" in desc


def test_cut_profile_relaxes_cq_for_second_transcode():
    """「按此裁剪」是第二次有损转码，单独放宽 2 档。"""
    burn, _ = enc.build_encode_args("x.mp4", gpu=True, info=_info(), cfg=_cfg())
    cut, desc = enc.build_encode_args("x.mp4", gpu=True, info=_info(),
                                      cfg=_cfg(), quality_profile="cut")
    assert burn[burn.index("-cq") + 1] == "32"
    assert cut[cut.index("-cq") + 1] == "30"
    assert "二次转码放宽" in desc
    assert "-maxrate" not in cut


def test_unknown_quality_profile_falls_back_to_burn():
    args, _ = enc.build_encode_args("x.mp4", gpu=True, info=_info(),
                                    cfg=_cfg(), quality_profile="nope")
    assert args[args.index("-cq") + 1] == "32"


def test_x264_cq_uses_crf_offset():
    """软编不认 NVENC 的 -cq/AQ，用 CRF = CQ - 9（30s 样片实测换算）。"""
    args, desc = enc.build_encode_args("x.mp4", gpu=False, info=_info(),
                                       cfg=_cfg(), quality_profile="cut")
    assert "-cq" not in args and "-spatial_aq" not in args
    assert args[args.index("-crf") + 1] == "21"     # cut_cq_level 30 - 9
    assert args[args.index("-preset") + 1] == "medium"
    assert "CQ30" in desc


def test_invalid_rate_control_and_preset_fall_back():
    args, _ = enc.build_encode_args(
        "x.mp4", gpu=True, info=_info(),
        cfg=_cfg(rate_control="bogus", cq_preset="nonsense"))
    assert args[args.index("-cq") + 1] == "32"
    assert args[args.index("-preset") + 1] == "medium"


def test_load_config_reads_cq_overrides(cfg_path):
    cfg_path("""
llm: {}
encode:
  rate_control: bitrate
  cq_level: 26
  cut_cq_level: 24
  cq_preset: slow
  cq_gop: 48
""")
    cfg = enc.load_config()
    assert cfg["rate_control"] == "bitrate"
    assert cfg["cq_level"] == 26 and cfg["cut_cq_level"] == 24
    assert cfg["cq_preset"] == "slow" and cfg["cq_gop"] == 48


def test_load_config_clamps_cq_bounds(cfg_path):
    cfg_path("""
llm: {}
encode:
  rate_control: nonsense
  cq_level: 999
  cut_cq_level: -5
  cq_preset: bogus
""")
    cfg = enc.load_config()
    assert cfg["rate_control"] == "cq"          # 非法值回退默认
    assert cfg["cq_level"] == 51 and cfg["cut_cq_level"] == 0
    assert cfg["cq_preset"] == "medium"
