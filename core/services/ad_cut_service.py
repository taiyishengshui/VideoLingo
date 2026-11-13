"""按 EDL 取补集 → 精确裁剪并拼接成「无广告成片」（FR-28 阶段二）。

用户 2026-11-10 确认的方案：
  * 裁剪源默认用**已烧录双语字幕的 output_sub.mp4** —— 字幕在画面里，
    切完拼完直接是成片，**不需要平移任何 SRT 时间戳、不需要重烧字幕**。
  * 成片 = EDL 广告区间的**补集**：保留 [0, A1) / [A1.end, A2) / … 再 concat。
  * 页面可预览视频、微调每段起止秒、选裁剪源，确认后才执行。

为什么用单条 `trim/atrim + concat` 而不是 `-ss -c copy`：
  `-c copy` 会把切点吸附到关键帧，实际切点可能偏 1~2 秒（往后吸会留
  广告头、往前吸会吃掉正文句尾）。filter_complex 是**帧级精确**的，
  避开了这个坑（已在当前片实测：2 段各 3s → 成片 6.006s）。
代价是要重编码一次（output_sub.mp4 本身已重编过，会有二次压缩）。
编码参数**必须**与烧录/合成一样来自 `core/services/encode_service.py`
这个唯一决策点（FR-29 / G-01）—— 曾经这里写死 `libx264 crf20 + aac 192k`，
结果源片已压到 390k，裁完又被 CRF 撑大好几倍。现在默认按源片码率/
帧率/音频码率输出；只有 `encode.match_source=false` 才退回 `ad.cut_*`。

拼接点音频做了 30ms 淡入淡出，避免「啪」一声。原片永远不动，
只生成新文件 output/output_no_ads.mp4，可随时删掉重来。
"""

from __future__ import annotations

import json
import os
import subprocess
import time

from core.utils import rprint
from core.utils.subprocess_log import run_logged

from core.services.ad_service import EDL_FILE, load_edl

OUTPUT_NAME = "output_no_ads.mp4"
PRESET_CHOICES = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium")
DEFAULT_PRESET = "veryfast"
DEFAULT_CRF = 20
FADE = 0.03

# 固定白名单：不接受用户传路径，避免越界读文件（G-09）
# subtitled = 画面里已经烧进字幕（配音版也用字幕 filter 烧了译文字幕）。
# 这个属性是「能不能边看字幕边切」的依据，只在这里标注一份。
_GENERATED = (
    ("burned", "output_sub.mp4", "已烧录双语字幕（output_sub.mp4）", True),
    ("dubbed", "output_dub.mp4", "中文配音版（output_dub.mp4）", True),
)


def _cut_cfg():
    try:
        from core.utils.config_utils import load_key
        preset = str(load_key("ad.cut_preset") or DEFAULT_PRESET).strip()
        crf = int(load_key("ad.cut_crf"))
    except Exception:                                              # noqa: BLE001
        return DEFAULT_PRESET, DEFAULT_CRF
    return (preset if preset in PRESET_CHOICES else DEFAULT_PRESET,
            max(10, min(35, crf)))


def list_sources(workspace="output") -> list[dict]:
    """可用裁剪源，固定 id + 真实路径；不暴露任意路径参数。

    每项带 `subtitled`：画面里是否有烧录字幕（前端据此标注预览源）。
    """
    out = []
    for key, name, label, subtitled in _GENERATED:
        p = os.path.join(workspace, name)
        try:
            stat = os.stat(p)
        except OSError:
            continue
        if os.path.isfile(p) and stat.st_size > 0:
            out.append({"id": key, "label": label, "path": p,
                        "size": stat.st_size, "subtitled": subtitled,
                        "version": f"{stat.st_mtime_ns}-{stat.st_size}"})
    try:
        from core.utils.media_files import find_video_files
        p = find_video_files(workspace)
        if p and os.path.isfile(p):
            stat = os.stat(p)
            out.append({"id": "original", "label": "原始视频（无字幕）",
                        "path": p, "size": stat.st_size,
                        "subtitled": False,
                        "version": f"{stat.st_mtime_ns}-{stat.st_size}"})
    except Exception:                                              # noqa: BLE001
        pass
    return out


def default_source(workspace="output", *, sources=None) -> str | None:
    """Prefer burned subtitles; use the same snapshot as the context API."""
    if sources is None:
        sources = list_sources(workspace)
    if not sources:
        return None
    for s in sources:
        if s["id"] == "burned":
            return s["id"]
    return sources[0]["id"]


def resolve_source(workspace, source_id) -> str:
    """严格按 id 白名单解析；未知 id / 产物还没生成都给出明确错误。"""
    for s in list_sources(workspace):
        if s["id"] == source_id:
            return s["path"]
    raise ValueError(
        f"裁剪源不可用：{source_id}。请先跑完「合并字幕到视频」生成 output_sub.mp4，"
        "或换一个下拉里的文件")


def probe(path) -> tuple[float, bool]:
    """返回 (时长秒, 是否有音轨)。parse 型调用，capture_output 是必要的。"""
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,start_time", "-show_entries",
             "stream=codec_type,start_time", "-of", "json", path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
        d = json.loads(p.stdout or "{}")
        return (max(0.0, float(d.get("format", {}).get("duration") or 0)),
                any(s.get("codec_type") == "audio" for s in d.get("streams", [])))
    except Exception:                                              # noqa: BLE001
        return 0.0, True


def probe_frame_rate(path) -> float:
    """读取视频流平均帧率（逐帧微调的步长 / 帧网格吸附依据）。

    解析统一走 `core/utils/media_probe.py`，这里只做薄转发（G-01：
    不允许第二份 ffprobe 帧率解析）。返回 0 表示探测失败，由前端
    回退到用户手选的 fps。
    """
    from core.utils.media_probe import probe_media
    return round(float(probe_media(path).get("fps") or 0.0), 3)


def media_info(workspace, source_id) -> dict:
    """预览源的 fps / 时长 / 编码参数（FR-30 手动切点需要真实片尾时间）。

    仍走 `resolve_source` 固定 id 白名单，不接受任意路径（G-09）。
    """
    from core.utils.media_probe import probe_media
    info = probe_media(resolve_source(workspace, source_id))
    # 与 probe_frame_rate 的显示精度一致（23.976 而不是 23.976024），
    # 前端 fps 下拉选项才不会多出一个几乎重复的条目
    info["fps"] = round(float(info.get("fps") or 0.0), 3)
    return {"source": source_id, **info}


def build_keep(segments, duration) -> list[tuple]:
    """EDL 广告区间 → 正常段（补集）。返回的区间两两不重叠、升序、夹在时长内。"""
    cuts = []
    for s in segments or []:
        if not isinstance(s, dict) or not s.get("enabled", True):
            continue
        try:
            a, b = float(s.get("start")), float(s.get("end"))
        except (TypeError, ValueError):
            continue
        if b > a:
            cuts.append((max(0.0, a), min(duration, b)))
    cuts.sort()
    keep, cursor = [], 0.0
    for a, b in cuts:
        if b <= a:
            continue
        if a > cursor + 0.04:
            keep.append((round(cursor, 3), round(a, 3)))
        cursor = max(cursor, b)
    if cursor < duration - 0.04:
        keep.append((round(cursor, 3), round(duration, 3)))
    return [(a, b) for a, b in keep if b - a > 0.04]


def build_command(src, keep, out, *, has_audio=True,
                  preset=DEFAULT_PRESET, crf=DEFAULT_CRF,
                  encode_args=None) -> list[str]:
    """单条 filter_complex：每段 trim/atrim → setpts → concat，帧级精确。

    `encode_args` 由 `encode_service.build_encode_args` 生成（唯一决策点）；
    传 None = `encode.match_source=false` 或探测失败，退回旧 crf 行为。
    注意 `-map [outa]` 是滤镜输出、不是原始流，**音频永远不能 copy**，
    所以调用方要用 `audio_reencode=True` 拿参数。
    """
    parts, inputs = [], []
    for i, (a, b) in enumerate(keep):
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        if has_audio:
            d = b - a
            fade = (f",afade=t=in:st=0:d={FADE},"
                    f"afade=t=out:st={max(FADE, d - FADE):.3f}:d={FADE}") if d > 0.2 else ""
            parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},"
                         f"asetpts=PTS-STARTPTS{fade}[a{i}]")
            inputs.append(f"[v{i}][a{i}]")
        else:
            inputs.append(f"[v{i}]")
    tail = "[outv][outa]" if has_audio else "[outv]"
    parts.append(f"{''.join(inputs)}concat=n={len(keep)}:v=1:a="
                 f"{1 if has_audio else 0}{tail}")
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", src,
           "-filter_complex", ";".join(parts), "-map", "[outv]"]
    if has_audio:
        cmd += ["-map", "[outa]"]
    if encode_args:
        cmd += list(encode_args)
    else:
        # 旧行为：只在 encode.match_source=false / 探测不到源片参数时使用
        if has_audio:
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-pix_fmt", "yuv420p"]
    cmd += ["-movflags", "+faststart", out]
    return cmd


def _encode_args_for(src, preset, crf, has_audio, probe_source=None):
    """裁剪的编码参数 —— 只向 encode_service 取，绝不在这里另算码率（G-01）。

    返回 `(args, desc)`；`args=None` 表示退回旧 crf 行为（配置关闭或探测
    不到源片视频流）。`-map [outa]` 是滤镜输出，音频必须重编码。

    `probe_source` 是**原始输入视频**（v1.18）：裁剪源 `src` 常常是已经
    压过一次的 `output_sub.mp4`，用它的参数当目标会把二次转码的损失
    固化进去。找不到原片时回退 `src`。`quality_profile="cut"` 让服务层
    单独放宽恒定质量档位，抵消第二次有损编码。
    """
    try:
        from core.services.encode_service import (
            build_encode_args, load_config as load_encode_config)
        from core.utils.media_probe import probe_media
        cfg = load_encode_config()
        target = probe_source or src
        info = probe_media(target) if cfg.get("match_source", True) else {}
        if info.get("has_video"):
            args, desc = build_encode_args(
                target, audio_reencode=bool(has_audio), cfg=cfg,
                info=info, preset=preset, quality_profile="cut")
            if args:
                return args, desc
    except Exception:                                              # noqa: BLE001
        pass                       # 探测/配置异常不能挡住裁剪本身
    return None, (f"{preset} crf{crf}（encode.match_source=false 或探测失败，"
                  "旧行为：成片会明显变大）")


def cut_video(workspace="output", source_id="burned", segments=None) -> dict:
    """执行裁剪；`segments=None` 时读已保存的 EDL。原视频不动，只生成新文件。"""
    src = resolve_source(workspace, source_id)
    if segments is None:
        edl = load_edl(workspace)
        segments = edl["segments"] if edl else None
    if not segments:
        raise ValueError("没有待裁剪区间，无需裁剪")
    duration, has_audio = probe(src)
    if duration <= 0:
        raise ValueError(f"读不到视频时长：{src}")
    keep = build_keep(segments, duration)
    if not keep:
        raise ValueError("裁剪区间覆盖了整段视频，没有可保留的正常段")

    out = os.path.join(workspace, OUTPUT_NAME)
    if os.path.abspath(out) == os.path.abspath(src):
        raise ValueError("输出文件与裁剪源相同，拒绝覆盖")
    preset, crf = _cut_cfg()
    # 编码参数优先按**原片**探测：src 常是已压过一次的 output_sub.mp4
    # （v1.18，抵消二次转码把压过的参数当目标）。找不到原片时服务层回退 src。
    original = next((s["path"] for s in list_sources(workspace)
                     if s["id"] == "original"), None)
    encode_args, desc = _encode_args_for(src, preset, crf, has_audio,
                                         probe_source=original)
    cmd = build_command(src, keep, out, has_audio=has_audio,
                        preset=preset, crf=crf, encode_args=encode_args)
    kept = sum(b - a for a, b in keep)
    removed = max(0.0, duration - kept)   # 补集长度之差，重叠区间也不会重复计
    rprint(f"[cyan]✂️ 裁剪：{src} → {out}[/cyan]")
    rprint(f"[cyan]   {len(keep)} 段正常内容 / {duration:.1f}s → 约 {kept:.1f}s"
           f"（去掉 {removed:.1f}s 待裁剪区间）[/cyan]")
    rprint(f"[cyan]   编码参数：{desc}[/cyan]")
    t0 = time.time()
    proc = run_logged(cmd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 裁剪失败（exit {proc.returncode}），"
                           "详情见页面日志 / cmd 窗口")
    out_dur, _ = probe(out)
    if out_dur <= 0:
        raise RuntimeError("ffmpeg 已结束但产物不可读，请检查磁盘空间与日志")
    rprint(f"[green]✅ 裁剪成片已生成 → {out}（{out_dur:.1f}s，"
           f"耗时 {time.time() - t0:.0f}s）[/green]")
    return {"saved_to": out, "source": src, "source_id": source_id,
            "duration": round(out_dur, 3), "removed": round(removed, 3),
            "keep": [{"start": a, "end": b} for a, b in keep],
            "elapsed": round(time.time() - t0, 1),
            "edl_file": os.path.join(workspace, EDL_FILE)}
