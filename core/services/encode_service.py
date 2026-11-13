"""烧录 / 合成 / 裁剪输出的编码参数 —— 唯一决策点（FR-29，G-01）。

背景（2026-11-10 实测）：`_7_sub_into_vid.py` 原来只给 ffmpeg 传滤镜，
`ffmpeg_gpu: true` 时补一个 `-c:v h264_nvenc`，其余全交给 NVENC 默认。
当前片原片是 AV1 408 kbps / opus 109 kbps / 23.976fps / 180 MB，
产物变成 H264 2044 kbps / aac 127 kbps / **748 MB（4.2 倍）**。

v1.18 起默认改为**恒定质量（CQ）**，不再用 `-maxrate` 锁死峰值码率。
用户反馈「和原视频一样的码率，很多地方变虚，镜头切换会虚一段时间」，
实测根因不是码率数字而是码率分配：原片 AV1 是 VBR（峰值 1336k = 3.78×
均值、关键帧跟着场景走），`-b:v 353k -maxrate 353k -bufsize 706k` 把
瞬时码率也锁在 353k，最复杂的 15 秒原片 1150k → 产物仅 460k；NVENC
固定 GOP 250 帧（10.43s）不做场景切换检测，切镜头后要糊到下一个 IDR。
30s 样片对照原片 PSNR：当前参数 40.05 dB / 1.39 MB，CQ32 46.44 dB /
2.55 MB（+6.4 dB，约 1.8× 体积）；缩短 GOP 或强制切点 IDR 只多 0.85 dB
却涨 10~32% 体积，不做默认。

这里读 `core/utils/media_probe.py` 的原始参数，决定输出该用什么编码器、
质量控制方式、帧率、音频策略；三个入口只负责把参数拼进命令，不许各自
再写一份"该用多少码率 / 什么 CQ"的判断。`quality_profile="cut"` 用于
「按此裁剪」—— 它是对 `output_sub.mp4` 的第二次有损转码，单独放宽
2 档（`encode.cut_cq_level`）抵消损失。

为什么不保持 AV1 编码：RTX 3060 硬件不支持 AV1 编码，`av1_nvenc` 只是
编译进来了、运行时会失败；软编 47 分钟 1080p 不可接受。所以输出仍是
H264 —— 同码率下 H264 画质天然低于 AV1（30~50%），恒定质量让复杂镜头
自己拿码率，比"对齐平均码率"更合理。旧 bitrate 模式逐字保留可回退。

实测（20s 样片，旧 bitrate 模式）：`-b:v 408k -maxrate 408k -bufsize 816k
-r 24000/1001 -c:a copy` → 视频 407937 bps（目标 408326，误差 0.1%）、
opus 直通 104266 bps、总 541352 bps、帧率一致。
"""

from __future__ import annotations

from core.utils.media_probe import probe_media

DEFAULTS = {
    "rate_control": "cq",      # cq（恒定质量，默认）| bitrate（旧的削峰模式）
    "cq_level": 32,            # 第 6/11 步 NVENC 恒定质量级别（越小越清晰）
    "cut_cq_level": 30,        # 「按此裁剪」单独放宽 2 档（抵消二次转码）
    "cq_preset": "medium",     # CQ 模式速度预设
    "cq_gop": 0,               # 0 = 不传 -g，交给编码器（实测短 GOP 不划算）
    "match_source": True,      # 关掉 = 完全旧行为（编码器默认参数）
    "bitrate_scale": 1.0,      # 仅 bitrate 模式：目标视频码率 = 源码率 × 此值
    "fps_mode": "cfr",         # cfr（显式 -r）| passthrough（原样传时间戳）
    "copy_audio": True,        # 音频直通，零重编码；需要混音时自动忽略
    "min_video_bitrate": 200,  # 仅 bitrate 模式：kbps 下限
}
FPS_MODES = ("cfr", "passthrough")
RATE_CONTROLS = ("cq", "bitrate")
CQ_PRESETS = ("fast", "medium", "slow", "p1", "p2", "p3", "p4", "p5", "p6", "p7")
QUALITY_PROFILES = ("burn", "cut")     # cut = 「按此裁剪」二次转码
# NVENC CQ 与 x264 CRF 不是同一尺度；30s 样片实测 CQ32 ≈ crf23（46.4/48.0 dB）
CQ_TO_CRF_OFFSET = 9
_STR_CHOICES = {"fps_mode": FPS_MODES, "rate_control": RATE_CONTROLS,
                "cq_preset": CQ_PRESETS}
_BOUNDS = {"bitrate_scale": (0.2, 10.0), "min_video_bitrate": (50, 50000),
           "cq_level": (0, 51), "cut_cq_level": (0, 51), "cq_gop": (0, 10000)}
# x264 的 veryfast / superfast 不是 NVENC 的合法 -preset 值（NVENC 只有
# p1..p7 / fast / medium / slow 等），直接透传会让 ffmpeg 报参数错。
# 裁剪把 ad.cut_preset 传进来时，在这里按编码器归一，别让入口层各写一份。
_X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium")
_NVENC_PRESETS = {"ultrafast": "fast", "superfast": "fast", "veryfast": "fast",
                  "faster": "fast", "fast": "fast", "medium": "medium"}


def load_config() -> dict:
    """读 `encode.*`；整段缺失 / 单项非法都退回默认值（G-08：手改文件）。"""
    cfg = dict(DEFAULTS)
    try:
        from core.utils.config_utils import load_key
        for key, default in DEFAULTS.items():
            try:
                v = load_key(f"encode.{key}")
            except (KeyError, OSError, ValueError):
                continue
            if v is None or v == "":
                continue
            if isinstance(default, bool):
                cfg[key] = (bool(v) if isinstance(v, bool)
                            else str(v).lower() in ("1", "true", "yes", "on"))
            elif isinstance(default, str):
                val = str(v).strip()
                if val in _STR_CHOICES.get(key, ()):
                    cfg[key] = val
            elif isinstance(default, float):
                lo, hi = _BOUNDS[key]
                try:
                    cfg[key] = max(lo, min(hi, float(v)))
                except (TypeError, ValueError):
                    pass
            else:
                lo, hi = _BOUNDS[key]
                try:
                    cfg[key] = max(int(lo), min(int(hi), int(v)))
                except (TypeError, ValueError):
                    pass
    except Exception:                                              # noqa: BLE001
        pass
    return cfg


def _encoder_for(codec: str, gpu: bool) -> str:
    """GPU 时尽量沿用同族 NVENC；AV1 / 未知一律落 H264（硬件/时长约束）。"""
    if not gpu:
        return "libx264"           # 软编 HEVC/AV1 太慢，统一 H264
    return {"h264": "h264_nvenc", "hevc": "hevc_nvenc"}.get(codec, "h264_nvenc")


def _cq_args(encoder: str, level: int, preset: str, gop: int) -> list[str]:
    """恒定质量控制参数 —— 唯一决策点，不传 maxrate/bufsize（不削峰）。

    NVENC 的 CQ 与 x264 的 CRF 尺度不同，这里按 30s 样片实测做换算；
    AQ / lookahead / multipass 让码率更聪明地分配到复杂帧与切换处。
    """
    if encoder.endswith("_nvenc"):
        args = ["-rc", "vbr", "-cq", str(level), "-b:v", "0",
                "-preset", preset,
                "-spatial_aq", "1", "-temporal_aq", "1",
                "-rc-lookahead", "32", "-multipass", "fullres"]
        if gop > 0:
            args += ["-g", str(gop)]
        return args
    # x264 不认 p1..p7，也不认 NVENC 专属 AQ 参数
    x264_preset = preset if preset in _X264_PRESETS else "medium"
    return ["-crf", str(max(0, min(51, level - CQ_TO_CRF_OFFSET))),
            "-preset", x264_preset]


def build_encode_args(source: str, *, gpu: bool | None = None,
                      audio_reencode: bool = False,
                      cfg: dict | None = None,
                      info: dict | None = None,
                      preset: str | None = None,
                      quality_profile: str = "burn") -> tuple[list[str], str]:
    """返回 `(ffmpeg 参数列表, 人读摘要)`。

    `rate_control="cq"`（默认）走恒定质量，不锁峰值码率；`bitrate` 是
    v1.7 的对齐源码率行为（保留回退）。`quality_profile="cut"` 用
    `cut_cq_level` 单独放宽，抵消「按此裁剪」的二次有损转码。

    `audio_reencode=True` 用于合成配音 / 裁剪拼接（filter_complex 产出的
    新音轨不能 copy）。`preset` 是速度预设，仅在 bitrate 模式生效（x264
    原值、NVENC 映射到合法名）；CQ 模式用 `encode.cq_preset`。
    `gpu` / `cfg` / `info` 允许显式注入，方便单测。
    """
    cfg = cfg if cfg is not None else load_config()
    if gpu is None:
        try:
            from core.utils.config_utils import load_key
            gpu = bool(load_key("ffmpeg_gpu"))
        except Exception:                                          # noqa: BLE001
            gpu = False

    fallback = ["-c:v", "h264_nvenc"] if gpu else []
    if not cfg.get("match_source", True):
        return fallback, "match_source=false：沿用编码器默认参数（旧行为）"

    info = info if info is not None else probe_media(source)
    if not info.get("has_video"):
        return fallback, "读不到输入视频流 → 退回编码器默认参数"

    codec = str(info.get("video_codec") or "").lower()
    encoder = _encoder_for(codec, gpu)
    args = ["-c:v", encoder]
    notes = [f"视频 {encoder}"]

    rate_control = str(cfg.get("rate_control", "cq")).lower()
    if rate_control not in RATE_CONTROLS:
        rate_control = "cq"
    if rate_control == "cq":
        profile = quality_profile if quality_profile in QUALITY_PROFILES else "burn"
        key = "cut_cq_level" if profile == "cut" else "cq_level"
        level = max(0, min(51, int(cfg.get(key, DEFAULTS[key]))))
        cq_preset = str(cfg.get("cq_preset", "medium"))
        if cq_preset not in CQ_PRESETS:
            cq_preset = "medium"
        gop = max(0, int(cfg.get("cq_gop", 0)))
        args += _cq_args(encoder, level, cq_preset, gop)
        extra = "（二次转码放宽）" if profile == "cut" else ""
        notes.append(f"恒定质量 CQ{level}{extra} · 预设 {cq_preset}"
                     " · AQ/lookahead/multipass")
        if gop > 0:
            notes.append(f"GOP {gop}")
    else:
        # v1.7 旧行为：按原片平均码率锁峰值（会削峰，仅作回退）
        if preset:
            if encoder == "libx264" and preset in _X264_PRESETS:
                args += ["-preset", preset]
                notes.append(f"预设 {preset}")
            elif encoder.endswith("_nvenc") and preset in _NVENC_PRESETS:
                mapped = _NVENC_PRESETS[preset]
                args += ["-preset", mapped]
                notes.append(f"预设 {mapped}（由 {preset} 映射）")

        vbr = int(info.get("video_bitrate") or 0)
        if vbr > 0:
            scale = float(cfg.get("bitrate_scale", 1.0))
            kbps = max(int(cfg.get("min_video_bitrate", 200)),
                       round(vbr * scale / 1000))
            args += ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
                     "-bufsize", f"{kbps * 2}k"]
            notes.append(f"码率 {kbps}k（原 {vbr / 1000:.0f}k ×{scale:g}）")
        else:
            notes.append("原片无码率信息 → 不强制码率")

    if info.get("pix_fmt"):
        args += ["-pix_fmt", str(info["pix_fmt"])]
    if cfg.get("fps_mode") == "passthrough":
        args += ["-fps_mode", "passthrough"]
        notes.append("时间戳原样传递")
    elif info.get("fps_raw"):
        args += ["-r", str(info["fps_raw"])]
        notes.append(f"帧率 {info['fps_raw']}")

    abr = int(info.get("audio_bitrate") or 0)
    has_audio = bool(info.get("has_audio"))
    if (not audio_reencode and cfg.get("copy_audio", True) and has_audio):
        args += ["-c:a", "copy"]
        notes.append(f"音频 {info.get('audio_codec') or '?'} 直通（{abr / 1000:.0f}k）")
    elif has_audio or audio_reencode:
        kbps = max(32, round(abr / 1000)) if abr > 0 else 128
        args += ["-c:a", "aac", "-b:a", f"{kbps}k"]
        # 第 11 步是 amix 混音、裁剪是 filter_complex 输出，两者都不能 copy；
        # 摘要里说「混音」会误导裁剪场景，统一写成「须重编码」。
        notes.append(f"音频 aac {kbps}k" + ("（须重编码）" if audio_reencode else ""))

    if codec == "av1":
        notes.append("原片 AV1：本机 NVENC 不支持 AV1 编码，输出 H264；"
                     + ("恒定质量下复杂镜头自行取码率，调 encode.cq_level 权衡体积"
                        if rate_control == "cq" else
                        "同码率画质略低，可调 encode.bitrate_scale"))
    return args, " · ".join(notes)
