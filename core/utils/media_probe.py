"""ffprobe 原始媒体参数读取（工具层：只取数，不做任何编码决策，FR-29）。

分层：数据在这里，「输出该用什么码率 / 编码器」的唯一决策在
`core/services/encode_service.py`。所有解析型 ffprobe 调用都走
`capture_output=True`（G-07 明确允许：这是解析输出，不是转码日志）。

读不到的字段一律给 0 / None / False，调用方自己兜底；任何异常都返回
空结构而不是抛错 —— 编码参数探测失败不该让整条流水线崩掉。
"""

from __future__ import annotations

import json
import subprocess


def _fraction(raw) -> float:
    """'24000/1001' → 23.976；'0/0' / 脏数据 → 0。"""
    try:
        num, _, den = str(raw or "").partition("/")
        v = float(num) / (float(den) if den and float(den) else 1.0)
        return round(v, 6) if 0 < v < 1000 else 0.0
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _int(raw, default=0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float(raw, default=0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def empty_info() -> dict:
    return {
        "duration": 0.0, "size": 0, "format_bitrate": 0,
        "width": 0, "height": 0, "fps": 0.0, "fps_raw": "",
        "video_codec": "", "pix_fmt": "", "video_bitrate": 0,
        "audio_codec": "", "audio_bitrate": 0, "sample_rate": 0,
        "channels": 0, "has_video": False, "has_audio": False,
    }


def probe_media(path: str) -> dict:
    """一次 ffprobe 读出编码对齐所需的全部参数。

    视频流 `bit_rate` 缺失时（部分 mkv/webm 不写）按
    「总码率 − 音频码率」→「文件大小/时长 − 音频码率」逐级估算。
    """
    info = empty_info()
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration,bit_rate,size",
             "-show_entries", "stream=codec_type,codec_name,width,height,pix_fmt,"
                              "r_frame_rate,avg_frame_rate,bit_rate,sample_rate,channels",
             "-of", "json", path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
        data = json.loads(p.stdout or "{}")
    except Exception:                                              # noqa: BLE001
        return info

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    info["duration"] = max(0.0, _float(fmt.get("duration")))
    info["size"] = _int(fmt.get("size"))
    info["format_bitrate"] = _int(fmt.get("bit_rate"))

    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        return info

    info["has_video"] = True
    info["video_codec"] = str(v.get("codec_name") or "").lower()
    info["width"] = _int(v.get("width"))
    info["height"] = _int(v.get("height"))
    info["pix_fmt"] = str(v.get("pix_fmt") or "")
    raw_fps = str(v.get("avg_frame_rate") or "").strip()
    if raw_fps in ("", "0/0", "N/A"):
        raw_fps = str(v.get("r_frame_rate") or "").strip()
    info["fps_raw"] = raw_fps
    info["fps"] = _fraction(raw_fps)

    abr = 0
    if a:
        info["has_audio"] = True
        info["audio_codec"] = str(a.get("codec_name") or "").lower()
        info["sample_rate"] = _int(a.get("sample_rate"))
        info["channels"] = _int(a.get("channels"))
        abr = _int(a.get("bit_rate"))
        info["audio_bitrate"] = abr

    vbr = _int(v.get("bit_rate"))
    if not vbr:
        vbr = max(0, info["format_bitrate"] - abr)
    if not vbr and info["size"] and info["duration"]:
        vbr = max(0, int(info["size"] * 8 / info["duration"]) - abr)
    info["video_bitrate"] = vbr
    if not info["audio_bitrate"] and info["has_audio"]:
        info["audio_bitrate"] = max(0, info["format_bitrate"] - vbr)
    return info
