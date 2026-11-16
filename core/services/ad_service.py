"""广告位提醒：识别赞助中插 + 定准裁剪边界。

分两层，对应两个不同的问题：

① **语义识别**（LLM）：把带**行号**的转录全文送进模型，让它标出「哪几段是
   赞助口播」。关键点：让模型返回 1-based 行号区间，而不是让它编时间戳 ——
   行号能稳定地映回 SRT 的真实时间，模型给的时间戳则经常是幻觉。

② **边界定点**：转录给出的「广告开始」其实是**广告第一句话的首词时间**，
   那时已经进广告了。实测（2026-11-10，当前片 4 段赞助）上一句正文末词到
   广告首词之间有稳定的 2.50~3.09s 过场空隙，空隙里通常有两个画面切换点
   （约 +0.25~0.8s 离开正文镜头、+1.25~1.7s 上赞助卡）。

   **默认 `boundary_mode=scene_cut`（用户 2026-11-10 最终选定 A 方案）**：
       候选窗口 = [上一句正文末词, 广告首词]
       窗口内用 ffmpeg 场景切换分（帧差数值，**不理解画面内容**）找切点，
       取**最后一个**切点 − pre_roll_pad 作为裁剪起点，终点同理取
       [广告末句末词, 下一句正文首词] 内第一个切点 + post_roll_pad。
   取最后一个起点切点是因为窗口里通常有两个：+0.25~0.8s 是「离开正文
   镜头」，+1.25~1.7s 才是「上赞助卡」。往前留 pad 是为了切点那一帧可能
   已经带赞助卡，保证不残留广告画面；pad 设 0 即严格用切点那一秒。
   切点检测只用 `select='gt(scene,...)'+showinfo`，不抽帧、不调 VLM：
   实测 4/4 命中，本地几秒跑完，不花模型钱。

   可选 `prev_end`（取补集时最简单、不依赖视频文件）/ `silence`（静音段
   中点）。找不到视频或窗口内没切点时自动退回「上一句正文末词 + pad」，
   并在每条区间的 start_basis / end_basis 里如实标出用的哪种依据。

产物（都不进 11 步注册表，按钮触发）：
  output/ad_slots.md       人读表格，含「转录起点 vs 修正后起点」对照
  output/ad_segments.json  机器可读 EDL，将来自动裁剪的唯一输入，可手工改

入口层（server/ad_api.py）只做参数校验与 HTTP 包装，决策都在这里（G-01）。
LLM 走 ask_gpt 的 prompt 精确缓存（G-05）：重复点击不会二次付费。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time

from core.utils import rprint

SLOTS_FILE = "ad_slots.md"
EDL_FILE = "ad_segments.json"
META_FILE = "video_meta.json"
EDL_VERSION = 1
MAX_ADS = 40
MIN_TRANSCRIPT_LINES = 3

# config.yaml `ad.*` 缺省值（手改立即生效，load_key 每次现开文件）
DEFAULTS = {
    "boundary_mode": "scene_cut",  # scene_cut（默认，A 方案）| prev_end | silence
    "pre_roll_pad": 0.30,        # 切点往前留的余量，别切掉正文尾音
    "post_roll_pad": 0.30,       # 广告结束后多留一点，别切进正文
    "scene_threshold": 0.08,     # ffmpeg 场景切换分阈值
    "min_gap_seconds": 0.80,     # 小于这个空隙不做定点，直接用保守 padding
    "sponsorblock": False,       # 可选：查 SponsorBlock 众包库作交叉校验
    "transcript_chars": 60000,   # 送进 prompt 的转录字符上限
}
BOUNDARY_MODES = ("prev_end", "scene_cut", "silence")
_BOUNDS = {
    "pre_roll_pad": (0.0, 3.0),
    "post_roll_pad": (0.0, 3.0),
    "scene_threshold": (0.01, 0.9),
    "min_gap_seconds": (0.0, 10.0),
    "transcript_chars": (2000, 200000),
}

_SRT_TS = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
_SCENE_PTS = re.compile(r"pts_time:([0-9.]+)")
_SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """读 `ad.*`；整段缺失/单项非法都退回默认值，不让配置问题阻断识别。"""
    cfg = dict(DEFAULTS)
    try:
        from core.utils.config_utils import load_key
        for key, default in DEFAULTS.items():
            try:
                v = load_key(f"ad.{key}")
            except (KeyError, OSError, ValueError):
                continue
            if v is None or v == "":
                continue
            if isinstance(default, bool):
                cfg[key] = bool(v) if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif isinstance(default, str):
                if str(v).strip() in BOUNDARY_MODES:
                    cfg[key] = str(v).strip()
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


# ---------------------------------------------------------------------------
# 转录：SRT → 带行号的行
# ---------------------------------------------------------------------------

def _ts(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[dict]:
    """解析 SRT → [{n, start, end, text}]，n 是 1-based 连续行号。

    行号是本功能的**唯一锚点**：LLM 只准引用行号，时间由这里映回，
    避免模型自己编时间戳。多行的字幕块合并成一行。
    """
    out = []
    for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n")):
        rows = [r.strip() for r in block.strip().splitlines() if r.strip()]
        if not rows:
            continue
        start = end = None
        body = []
        for r in rows:
            m = _SRT_TS.search(r)
            if m:
                start = _ts(*m.group(1, 2, 3, 4))
                end = _ts(*m.group(5, 6, 7, 8))
                continue
            if r.isdigit() and not body:
                continue                      # SRT 序号行
            body.append(r)
        if not body or start is None or end is None:
            continue
        out.append({"n": len(out) + 1, "start": start,
                    "end": max(start, end), "text": " ".join(body)})
    return out


def read_transcript(workspace="output") -> tuple[list[dict], str, str]:
    """优先中文译文 trans.srt（用户确认的主素材），其次英文 src.srt。

    没有带时间戳的 SRT 就不做识别：没有行号→时间的映射，边界定点无从谈起。
    """
    for name, source, label in (
            ("trans.srt", "trans_srt", "中文译文字幕（trans.srt）"),
            ("src.srt", "src_srt", "英文原文字幕（src.srt）"),
            ("trans_src.srt", "trans_src_srt", "双语对照字幕（trans_src.srt）")):
        p = os.path.join(workspace, name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8-sig") as f:
                lines = parse_srt(f.read())
            if len(lines) >= MIN_TRANSCRIPT_LINES:
                return lines, source, label
        except OSError:
            pass
    raise ValueError(
        "没有可用的带时间戳字幕：广告位识别需要 output/trans.srt（中文译文，"
        "请先跑到第 6 步「生成字幕」）或 output/src.srt。")


def _fmt_clock(sec, ms=False):
    try:
        sec = max(0.0, float(sec))
    except (TypeError, ValueError):
        return "--:--:--"
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return (f"{int(h):02d}:{int(m):02d}:{s:06.3f}" if ms
            else f"{int(h):02d}:{int(m):02d}:{int(s):02d}")


def numbered_transcript(lines, char_budget=60000) -> str:
    """`[行号] mm:ss 文本`，只在每 50 行插时间标记会削弱定位，这里逐行给。

    时间戳用 mm:ss（不带毫秒）省 token；精确定点由代码用原始 SRT 时间做。
    超预算时从头尾均匀丢中间行会破坏行号语义 —— 改为直接截断并提示，
    绝不重新编号（行号必须能和 SRT 一一对上）。
    """
    parts = []
    used = 0
    for ln in lines:
        row = f"[{ln['n']}] {_fmt_clock(ln['start'])[3:]} {ln['text']}"
        if used + len(row) > char_budget:
            parts.append(f"…（转录超长，此处截断；共 {len(lines)} 行）")
            break
        parts.append(row)
        used += len(row) + 1
    return "\n".join(parts)


def transcript_brief(lines) -> dict:
    starts = [l["start"] for l in lines]
    ends = [l["end"] for l in lines]
    return {"lines": len(lines),
            "chars": sum(len(l["text"]) for l in lines),
            "duration": round((max(ends) - min(starts)) if starts else 0.0, 3)}


# ---------------------------------------------------------------------------
# LLM 结果校验 / 归一
# ---------------------------------------------------------------------------

def normalize_ads(data, line_count) -> list[dict]:
    """宽松归一：模型偶尔把行号写成字符串、把 confidence 写成中文。"""
    if not isinstance(data, dict):
        return []
    raw = data.get("ads")
    if not isinstance(raw, list):
        return []
    out = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        try:
            s, e = int(a.get("start_line")), int(a.get("end_line"))
        except (TypeError, ValueError):
            continue
        if not (1 <= s <= e <= line_count):
            continue
        conf = str(a.get("confidence") or "medium").strip().lower()
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        out.append({
            "brand": str(a.get("brand") or "").strip()[:60] or "未命名品牌",
            "start_line": s, "end_line": e,
            "note": str(a.get("note") or "").strip()[:120],
            "confidence": conf,
        })
    out.sort(key=lambda x: (x["start_line"], x["end_line"]))
    # 去掉完全重复，保留重叠（可能是「一段里两个品牌」，交给人工看）
    dedup = []
    for a in out:
        if not dedup or (a["start_line"], a["end_line"]) != (
                dedup[-1]["start_line"], dedup[-1]["end_line"]):
            dedup.append(a)
    return dedup[:MAX_ADS]


def validate_ads(data, line_count) -> dict:
    """ask_gpt valid_def 协议。失败 message 直接写给模型看重试方向。"""
    if not isinstance(data, dict):
        return {"status": "error", "message": "result must be a JSON object with an 'ads' array"}
    if not isinstance(data.get("ads"), list):
        return {"status": "error", "message": "'ads' must be an array (empty array if no ads found)"}
    if len(data["ads"]) > MAX_ADS:
        return {"status": "error", "message": f"at most {MAX_ADS} ad segments"}
    norm = normalize_ads(data, line_count)
    if len(norm) != len(data["ads"]):
        return {"status": "error",
                "message": f"every ad needs integer start_line/end_line with 1 <= start_line <= end_line <= {line_count}, "
                           "plus string brand/note/confidence(high|medium|low)"}
    return {"status": "success", "message": "ok"}


# ---------------------------------------------------------------------------
# 本地信号：ffmpeg 画面切点 / 静音段（只解析输出，不转发日志）
# ---------------------------------------------------------------------------

def _run_ffmpeg(args, timeout=45):
    """解析型调用：capture_output 是必要的（G-07 里「用于解析输出」的例外）。"""
    try:
        p = subprocess.run(["ffmpeg", "-hide_banner", *args],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def find_scene_cuts(media, t0, t1, threshold=0.08) -> list[float]:
    """返回 [t0, t1] 窗口内所有画面切换点的**绝对秒**。

    -ss 放在 -i 前做快速 seek；showinfo 的 pts_time 相对 seek 点，
    所以统一加回 ss。只取帧差数值，不解码/理解画面内容。
    """
    if not media or not os.path.isfile(media) or t1 <= t0:
        return []
    ss = max(0.0, t0 - 0.5)
    dur = min(30.0, (t1 - t0) + 1.0)
    out = _run_ffmpeg([
        "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", media, "-an",
        "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"])
    cuts = []
    for m in _SCENE_PTS.finditer(out):
        try:
            t = ss + float(m.group(1))
        except ValueError:
            continue
        if t0 - 0.01 <= t <= t1 + 0.01 and all(abs(t - c) > 0.08 for c in cuts):
            cuts.append(round(t, 3))
    return sorted(cuts)


def find_silences(media, t0, t1, noise_db=-35, min_duration=0.25) -> list[tuple]:
    """返回 [t0, t1] 窗口内的静音区间 [(start, end)]，绝对秒。"""
    if not media or not os.path.isfile(media) or t1 <= t0:
        return []
    ss = max(0.0, t0 - 0.5)
    dur = min(30.0, (t1 - t0) + 1.0)
    out = _run_ffmpeg([
        "-ss", f"{ss:.3f}", "-t", f"{dur:.3f}", "-i", media, "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-"])
    starts = [ss + float(x) for x in _SIL_START.findall(out)]
    ends = [ss + float(x) for x in _SIL_END.findall(out)]
    pairs = []
    for i, a in enumerate(starts):
        b = ends[i] if i < len(ends) else t1
        if b > a and b >= t0 and a <= t1:
            pairs.append((round(max(a, t0), 3), round(min(b, t1), 3)))
    return pairs


def _find_input_video(workspace):
    """输入视频（排除 output_sub.mp4 等自产物）；找不到就返回 None。"""
    try:
        from core.utils.media_files import find_video_files
        p = find_video_files(workspace)
        return p if p and os.path.isfile(p) else None
    except Exception:                                              # noqa: BLE001
        return None


def _find_audio(workspace):
    p = os.path.join(workspace, "audio", "raw.mp3")
    return p if os.path.isfile(p) else None


# ---------------------------------------------------------------------------
# 边界定点（纯逻辑，ffmpeg 通过回调注入 → 可单测）
# ---------------------------------------------------------------------------

def refine_segments(ads, lines, cfg, *, scene_fn=None, silence_fn=None,
                    video=None, audio=None) -> list[dict]:
    """把「广告行号区间」变成「秒级裁剪区间」。

    `scene_cut`（默认，A 方案）：起点取窗口内最后一个画面切点 − pre_pad，
          终点取 [广告末句末词, 下一句正文首词] 内第一个切点 + post_pad；
          找不到切点时按下面两种退回。
    `prev_end`：起点 = 上一句正文末词 + pre_pad，终点 = 广告末句末词 + post_pad
          （取补集时最简单、不依赖视频文件）。
    `silence`：起点取窗口内最长静音段中点。
    所有结果都夹在「不切进正文、不漏掉广告语音」的安全范围内。
    """
    mode = cfg.get("boundary_mode", "scene_cut")
    if mode not in BOUNDARY_MODES:
        mode = "scene_cut"
    use_scene = mode == "scene_cut"
    pre = cfg["pre_roll_pad"]
    post = cfg["post_roll_pad"]
    min_gap = cfg["min_gap_seconds"]
    thr = cfg["scene_threshold"]
    scene = scene_fn or (lambda m, a, b, t: find_scene_cuts(m, a, b, t))
    silence = silence_fn or (lambda m, a, b: find_silences(m, a, b))
    segs = []

    for i, ad in enumerate(ads, 1):
        si, ei = ad["start_line"] - 1, ad["end_line"] - 1
        first, last = lines[si]["start"], lines[ei]["end"]
        prev = lines[si - 1]["end"] if si > 0 else None
        nxt = lines[ei + 1]["start"] if ei + 1 < len(lines) else None
        gap = round(first - prev, 3) if prev is not None else 0.0
        cuts = []

        # ---- 起点 ----
        start = None
        basis = "prev_end" if prev is not None else "fallback"
        if prev is not None and gap >= min_gap and mode != "prev_end":
            if use_scene:
                cuts = scene(video, prev, first, thr) if video else []
                usable = [c for c in cuts if prev + 0.05 <= c <= first - 0.05]
                if usable:
                    start = max(prev + 0.05, max(usable) - pre)
                    basis = "scene_cut"
            if start is None:
                sil = silence(audio or video, prev, first) if (audio or video) else []
                if sil:
                    a, b = max(sil, key=lambda p: p[1] - p[0])
                    start = max(prev + 0.05, (a + b) / 2)
                    basis = "silence"
        if start is None:
            start = (prev + pre) if prev is not None else max(0.0, first - pre)
        lo = (prev + 0.02) if prev is not None else 0.0
        # 广告就在第 1 行时 first≈0，hi 可能小于 lo —— 夹到 lo，别产出负时间
        start = min(max(start, lo), max(lo, first - 0.05))

        # ---- 终点 ----
        end = last + post
        end_basis = "prev_end"
        end_gap = (nxt - last) if nxt is not None else 0.0
        if use_scene and video and nxt is not None and end_gap >= min_gap:
            cuts2 = scene(video, last, nxt, thr)
            usable2 = [c for c in cuts2 if last + 0.05 <= c <= nxt - 0.05]
            if usable2:
                end = min(usable2) + post
                end_basis = "scene_cut"
        if nxt is not None:
            end = min(end, nxt - 0.05)
        end = max(end, start + 0.2)

        segs.append({
            "id": i, "brand": ad["brand"], "note": ad["note"],
            "confidence": ad["confidence"], "enabled": True,
            "start": round(start, 3), "end": round(end, 3),
            "transcript_start": round(first, 3), "transcript_end": round(last, 3),
            "prev_end": round(prev, 3) if prev is not None else None,
            "next_start": round(nxt, 3) if nxt is not None else None,
            "start_line": ad["start_line"], "end_line": ad["end_line"],
            "start_gap": gap, "start_basis": basis, "end_basis": end_basis,
            "scene_cuts": [round(c, 3) for c in cuts],
        })
    return segs


# ---------------------------------------------------------------------------
# SponsorBlock 交叉校验（可选，默认关）
# ---------------------------------------------------------------------------

def sponsorblock_segments(workspace="output", timeout=4) -> list[dict]:
    """查众包库；网络不可用 / 404 无覆盖一律返回空列表，绝不影响主流程。"""
    p = os.path.join(workspace, META_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            url = str(json.load(f).get("webpage_url") or "")
        m = re.search(r"(?:v=|/shorts/|/embed/)([\w-]{11})", url)
        if not m:
            return []
        import urllib.request
        api = f"https://sponsor.ajay.app/api/skipSegments?videoID={m.group(1)}"
        req = urllib.request.Request(api, headers={"User-Agent": "VideoLingo/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = []
        for d in data if isinstance(data, list) else []:
            seg = d.get("segment") if isinstance(d, dict) else None
            if isinstance(seg, list) and len(seg) == 2:
                out.append({"start": round(float(seg[0]), 3),
                            "end": round(float(seg[1]), 3),
                            "category": str(d.get("category") or "sponsor"),
                            "votes": int(d.get("votes") or 0)})
        return sorted(out, key=lambda x: x["start"])
    except Exception:                                              # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# 产物：Markdown 表格 + EDL
# ---------------------------------------------------------------------------

_BASIS_LABEL = {
    "prev_end": "上一句正文结束", "scene_cut": "画面切点",
    "silence": "静音中点", "word_gap": "词级空隙+padding",
    "fallback": "保守兜底",
}


def _is_manual(s: dict) -> bool:
    """手动切点（FR-30）：没有转录开口/画面切点这些语义，MD 里要分开渲染。"""
    return (s.get("confidence") == "manual"
            or s.get("start_basis") == "manual"
            or s.get("source") == "manual")


def _slot_overlap(a, b) -> float:
    """两段区间的重合比例（重合长度 / 较短者），用于认出「同一段广告」。

    用户可能先微调起止秒再删除，重新识别回来的又是原始秒数 —— 所以不能
    按精确时间或 id 比较，用重叠比例才不会被微调绕过去（FR-30）。
    """
    try:
        a0, a1 = float(a["start"]), float(a["end"])
        b0, b1 = float(b["start"]), float(b["end"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    base = min(a1 - a0, b1 - b0)
    if base <= 0:
        return 0.0
    return max(0.0, min(a1, b1) - max(a0, b0)) / base


def is_dismissed(seg, dismissed) -> bool:
    """是否被用户手动删除过（重新识别时不再自动加回来）。"""
    for d in dismissed or []:
        if not isinstance(d, dict) or _slot_overlap(seg, d) < 0.6:
            continue
        ba, bb = str(d.get("brand") or ""), str(seg.get("brand") or "")
        if not ba or not bb or ba == bb:
            return True
    return False


def _merge_dismissed(*groups) -> list[dict]:
    """按区间去重合并「已删除」记录；不留 id（恢复时会重新编号）。"""
    out = []
    for group in groups:
        for d in group or []:
            if isinstance(d, dict) and not any(
                    _slot_overlap(d, x) >= 0.6 for x in out):
                out.append({k: v for k, v in d.items() if k != "id"})
    return out


def render_md(segments, meta, dismissed_n=0) -> str:
    n = len(segments)
    ads_n = sum(1 for s in segments if not _is_manual(s))
    manual_n = n - ads_n
    head = [
        f"# ⚠️ 待裁剪区间（{ads_n} 段赞助中插 + {manual_n} 段手动切点，发布前务必裁剪）"
        if n else "# ✅ 待裁剪区间（暂无）", "",
        f"> 素材：{meta.get('label', '—')} · {meta.get('lines', 0)} 行 / "
        f"{meta.get('chars', 0)} 字 / 约 {_fmt_clock(meta.get('duration', 0))}",
        f"> 定点：{meta.get('strategy', '—')} · 起点前移 "
        f"{meta.get('pre_roll_pad', 0)}s / 终点后延 {meta.get('post_roll_pad', 0)}s",
        f"> 生成：{meta.get('generated_at', '—')}（任务链 ad_detect，模板可在「术语 & 提示词」页编辑）",
        "",
        "赞助段的起点不是转录里广告开口的那一秒：那时已经进广告了。区间落在"
        "「上一句正文结束 → 广告首词」的 2~3 秒过场空隙里（默认取空隙里"
        "最后一个画面切点再往前留一点）。手动切点则由用户直接指定秒数。"
        "成片按这些区间**取补集**（只保留正常段再拼接），原视频不动。",
        "",
    ]
    if dismissed_n:
        head += [f"> 已手动删除 {dismissed_n} 段识别结果；重新识别不会自动加回，"
                 "页面「恢复已删除」可加回。", ""]
    if not n:
        return "\n".join(head + ["没有待裁剪区间。可点「识别广告位」找赞助中插，"
                                "或用「+ 添加切点 / 切掉片头 / 从当前切到片尾」手动指定。", ""])
    head += [
        "| # | 品牌 / 标记 | 建议裁剪区间 | 时长 | 说明 | 起点依据 | 比转录提前 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in segments:
        dur = s["end"] - s["start"]
        manual = _is_manual(s)
        basis = "手动指定" if manual else _BASIS_LABEL.get(
            s.get("start_basis", ""), s.get("start_basis", "—"))
        shift = ((s.get("transcript_start", s["start"]) - s["start"])
                 if not manual else 0.0)
        shift_text = "—" if manual else f"{shift:.2f}s"
        head.append(
            f"| {s['id']} | {s.get('brand', '—')} | "
            f"{_fmt_clock(s['start'], True)}–{_fmt_clock(s['end'], True)} | "
            f"{dur:.1f}s | {s.get('note') or '—'} | {basis} | "
            f"{shift_text} |")

    auto = [s for s in segments if not _is_manual(s)]
    if auto:
        head += [
            "", "## 边界对照（转录开口 vs 建议裁剪起点，仅赞助段）", "",
            "| # | 品牌 | 上一句正文结束 | 转录开口 | 建议起点 | 窗口内画面切点 |",
            "|---|---|---|---|---|---|",
        ]
        for s in auto:
            prev = (_fmt_clock(s["prev_end"], True)
                    if s.get("prev_end") is not None else "—")
            cuts = ", ".join(_fmt_clock(c, True)
                             for c in (s.get("scene_cuts") or [])) or "—"
            head.append(f"| {s['id']} | {s.get('brand', '—')} | {prev} | "
                        f"{_fmt_clock(s.get('transcript_start', s['start']), True)} | "
                        f"{_fmt_clock(s['start'], True)} | {cuts} |")
    head += [
        "", "> 机器可读区间见 `ad_segments.json`（EDL）；页面上可直接修改起止秒并保存，",
        "> 自动裁剪以该文件为唯一输入，原视频不会被改动。", "",
    ]
    return "\n".join(head)


def save_edl(workspace, payload) -> str:
    """原子落盘（tmp + replace），避免中断留下半个 EDL（G-06）。"""
    os.makedirs(workspace, exist_ok=True)
    path = os.path.join(workspace, EDL_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def load_edl(workspace="output") -> dict | None:
    p = os.path.join(workspace, EDL_FILE)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and isinstance(d.get("segments"), list) else None
    except (OSError, ValueError):
        return None


def update_edl_segments(workspace, segments, dismissed=None) -> dict:
    """页面手工改时间/勾选/删除后回写：只覆盖 segments，保留 meta 与生成信息。

    FR-30：EDL 还不存在时自动建骨架 —— 用户只想切片头/片尾不该被迫
    先跑一次要花钱的 LLM 广告识别。
    FR-32：`segments=[]` 是合法的「清空全部切点」，不再 400；但传了非空
    却全是非法区间时仍报错，避免一次坏请求把 EDL 悄悄清空。
    自动段从列表里消失 = 用户删掉了识别结果，记入 `dismissed`，重新识别
    时不再自动加回；显式传 `dismissed=[]` 表示「恢复已删除」。
    """
    edl = load_edl(workspace)
    if edl is None:
        edl = {"meta": {
            "version": EDL_VERSION, "source": "manual", "label": "手动切点",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "strategy": "人工指定区间",
        }, "segments": [], "sponsorblock": []}
    clean = []
    for s in segments or []:
        if not isinstance(s, dict):
            continue
        try:
            a, b = float(s.get("start")), float(s.get("end"))
        except (TypeError, ValueError):
            continue
        if b <= a or a < 0:
            continue
        base = next((x for x in edl["segments"] if x.get("id") == s.get("id")), {})
        clean.append({**base, **s, "start": round(a, 3), "end": round(b, 3),
                      "enabled": bool(s.get("enabled", True))})
    if not clean and (segments or []):
        raise ValueError("没有有效的裁剪区间")
    # 消失的自动段才是「删除」；取消勾选 / 改时间都还在 clean 里，不算删。
    gone = [s for s in edl["segments"]
            if not _is_manual(s)
            and not any(_slot_overlap(s, k) >= 0.6 for k in clean)]
    new_dismissed = (_merge_dismissed(edl.get("dismissed"), gone)
                     if dismissed is None else _merge_dismissed(dismissed))
    edl["segments"] = clean
    edl["dismissed"] = new_dismissed
    edl["edited_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(workspace, SLOTS_FILE), "w", encoding="utf-8") as f:
        f.write(render_md(clean, {**edl.get("meta", {}),
                                  "strategy": "人工微调 + " + str(edl.get("meta", {}).get("strategy", "—"))},
                          dismissed_n=len(new_dismissed)))
    save_edl(workspace, edl)
    return edl


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def context(workspace="output") -> dict:
    """卡片预填：转录状态 + 输入视频/音频是否可用于定点 + 是否已有 EDL。"""
    try:
        lines, source, label = read_transcript(workspace)
        brief = {"available": True, "source": source, "label": label,
                 **transcript_brief(lines)}
    except ValueError as e:
        brief = {"available": False, "label": "无字幕", "source": "none",
                 "lines": 0, "chars": 0, "duration": 0, "reason": str(e)}
    cfg = load_config()
    video = _find_input_video(workspace)
    edl = load_edl(workspace)
    return {"transcript": brief, "video": video or "",
            "audio": _find_audio(workspace) or "",
            # 默认 scene_cut 需要输入视频；没有视频会自动退回 prev_end 模式
            "boundary_ready": True,
            "scene_cut_ready": bool(video), "config": cfg,
            "has_edl": edl is not None,
            "edl": edl,
            "sponsorblock": sponsorblock_segments(workspace)
            if cfg.get("sponsorblock") else []}


def detect_ads(workspace="output", extra_hint="", *,
               _ask=None, _scene_fn=None, _silence_fn=None) -> dict:
    """识别 + 定点 + 落盘，返回给页面的结构化结果。

    `_ask` / `_scene_fn` / `_silence_fn` 仅供单测注入，生产路径走真实实现。
    """
    lines, source, label = read_transcript(workspace)
    cfg = load_config()
    body = numbered_transcript(lines, cfg["transcript_chars"])
    brief = transcript_brief(lines)

    from core.prompts import get_ad_detect_prompt
    prompt = get_ad_detect_prompt(
        transcript=body, transcript_meta=f"{label}，共 {len(lines)} 行 / {brief['chars']} 字",
        line_count=len(lines), extra_hint=extra_hint)
    rprint(f"[cyan]🔎 识别广告位：{label} {len(lines)} 行 / {brief['chars']} 字，prompt {len(prompt)} 字符[/cyan]")
    ask = _ask
    if ask is None:
        from core.utils.ask_gpt import ask_gpt
        ask = ask_gpt
    result = ask(prompt, resp_type="json",
                 valid_def=lambda d: validate_ads(d, len(lines)),
                 log_title="ad_detect")
    ads = normalize_ads(result, len(lines))

    video = _find_input_video(workspace)
    audio = _find_audio(workspace)
    mode = cfg.get("boundary_mode", "scene_cut")
    if mode == "scene_cut" and not video:
        rprint("[yellow]⚠️ 未找到输入视频，边界定点退回「上一句正文结束 + padding」[/yellow]")
    segments = refine_segments(
        ads, lines, cfg, scene_fn=_scene_fn, silence_fn=_silence_fn,
        video=video, audio=audio)
    ads_n = len(segments)
    # FR-30：重新识别不能吞掉用户手动加的切点；FR-32：也不能把用户删掉的
    # 识别结果又加回来（页面「恢复已删除」才清掉这份记录）。统一重编号。
    prev_edl = load_edl(workspace) or {}
    dismissed = _merge_dismissed(prev_edl.get("dismissed"))
    kept = [s for s in segments if not is_dismissed(s, dismissed)]
    skipped_n = len(segments) - len(kept)
    if skipped_n:
        rprint(f"[yellow]⏭️ 跳过 {skipped_n} 段已手动删除的识别结果"
               "（页面「恢复已删除」可加回）[/yellow]")
    manual = [dict(s) for s in (prev_edl.get("segments") or []) if _is_manual(s)]
    segments = sorted(kept + manual, key=lambda s: float(s.get("start", 0)))
    for _i, _s in enumerate(segments):
        _s["id"] = _i

    strategy = {"prev_end": "上一句正文结束 + padding（取补集）",
                "scene_cut": "画面切点 + 词级空隙",
                "silence": "静音段 + 词级空隙"}.get(mode, "画面切点 + 词级空隙")
    meta = {
        "version": EDL_VERSION, "source": source, "label": label,
        **brief,
        "video": video or "", "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": strategy, "boundary_mode": mode,
        "pre_roll_pad": cfg["pre_roll_pad"], "post_roll_pad": cfg["post_roll_pad"],
        "scene_threshold": cfg["scene_threshold"],
    }
    edl = {"meta": meta, "segments": segments, "dismissed": dismissed,
           "sponsorblock": sponsorblock_segments(workspace)
           if cfg.get("sponsorblock") else []}
    md_path = os.path.join(workspace, SLOTS_FILE)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_md(segments, meta, dismissed_n=len(dismissed)))
    edl_path = save_edl(workspace, edl)
    rprint(f"[green]✅ 识别到 {ads_n} 段赞助（跳过 {skipped_n} 段已删除）、"
           f"保留 {len(manual)} 段手动切点 → {md_path} / {edl_path}[/green]")
    for s in segments:
        line = (f"   #{s['id']} {s.get('brand', '—')} "
                f"{_fmt_clock(s['start'], True)}–{_fmt_clock(s['end'], True)} ")
        if _is_manual(s):
            rprint(f"[green]{line}（手动指定，已保留）[/green]")
        else:
            rprint(f"[green]{line}"
                   f"（{_BASIS_LABEL.get(s['start_basis'], s['start_basis'])}，"
                   f"比转录提前 {s['transcript_start'] - s['start']:.2f}s）[/green]")
    return {"segments": segments, "meta": meta, "dismissed": dismissed,
            "saved_to": md_path, "edl_to": edl_path,
            "sponsorblock": edl["sponsorblock"]}
