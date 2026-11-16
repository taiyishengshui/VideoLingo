"""B站投稿文案（标题 / 简介 / 标签）生成服务。

公式源自 bili-video-meta skill（GracePower 风格，361 条投稿实测统计），
已提炼为项目内 prompt 模板 `bili_meta`（「术语 & 提示词」页可编辑），
运行时不依赖 WorkBuddy / skill 文件。

**素材主次（2026-11-10 改）**：视频的转录结果才是决定性一手素材 —— 标题
钩子、主题词、简介里的痛点/金句都要能在转录里找到出处。`video_meta.json`
的 YouTube 原标题/简介只作背景校准，含营销话术，不得主导选题。

转录可能很长（68 分钟播客 ≈ 9 万字符），整篇塞进 prompt 既贵又稀释重点，
所以按时间**均匀采样**成受控长度的节选，头尾加权（开头抛钩子、结尾给结论）；
若第 4.1 步已生成 `log/summary.txt`（LLM 全局摘要）则一并附上补全局视角。

入口层（server/bili_api.py）只做参数校验与 HTTP 包装，决策都在这里。
"""

from __future__ import annotations

import json
import os
import re
import time

from core.utils import rprint

# skill 硬红线人名池 + 本项目实际译介对象（config.yaml bili.speaker_names 可追加）
_SPEAKER_POOL = [
    "Matthew Hussey", "Mel Robbins", "Jay Shetty", "Jefferson Fisher",
    "Gabby Bernstein", "Natalie Dawson", "Anna Bey", "Pick Up Limes",
    "Esther Perel",
]

META_FILE = "video_meta.json"
OUTPUT_FILE = "bili_meta.md"

# 转录采样默认值（config.yaml bili.* 可覆盖；手改立即生效）
DEFAULT_CHARS = 12000          # 送进 prompt 的转录字符预算
DEFAULT_SAMPLES = 8            # 均匀采样段数
MIN_TRANSCRIPT_CHARS = 120     # 短于此视为没有可用转录
_SENTINEL = "<!-- videolingo:transcript -->"   # 自定义模板兜底注入的判据
_PUNCT = re.compile(r"^[^\w\s]+$", re.UNICODE)


def _cfg_int(key, default, lo, hi):
    """读 config 的整数项，缺失/非法一律退回默认值。"""
    try:
        from core.utils.config_utils import load_key
        v = load_key(key)
        if v is None or v == "":
            return default
        return max(lo, min(hi, int(v)))
    except Exception:                                              # noqa: BLE001
        return default


def _fmt_ts(sec):
    try:
        sec = max(0, int(float(sec)))
    except (TypeError, ValueError):
        return "?:??"
    return f"{sec // 60:02d}:{sec % 60:02d}"


def load_video_context(workspace="output") -> dict:
    """【次要参考】原视频信息：video_meta.json 优先，本地文件从文件名猜标题。"""
    meta_path = os.path.join(workspace, META_FILE)
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("title"):
                return {"title": str(d["title"]), "source": "downloaded",
                        "description": str(d.get("description") or ""),
                        "uploader": str(d.get("uploader") or "")}
        except (OSError, ValueError):
            pass  # 损坏的 meta 当没有处理
    try:
        from core.utils.media_files import find_media_file
        path, _ = find_media_file(workspace)
        return {"title": os.path.splitext(os.path.basename(path))[0],
                "description": "", "uploader": "", "source": "filename"}
    except Exception:                                              # noqa: BLE001
        return {"title": "", "description": "", "uploader": "", "source": "none"}


# ---------------------------------------------------------------------------
# 转录结果：多来源读取 → 统一成 [(start_sec|None, text)] → 均匀采样
# ---------------------------------------------------------------------------

def _group_words(words, block_chars=400):
    """词级表聚成可读段落；纯标点词贴回前一个词，空行（被吞词）丢弃。"""
    blocks, buf, start, size = [], [], None, 0
    for w in words:
        if not isinstance(w, dict):
            continue
        t = str(w.get("text") or "").strip()
        if not t:
            continue
        try:
            st = float(w["start"]) if w.get("start") is not None else None
        except (TypeError, ValueError):
            st = None
        if start is None:
            start = st
        if buf and _PUNCT.fullmatch(t):
            buf[-1] += t
            size += len(t)
            continue
        buf.append(t)
        size += len(t) + 1
        if size >= block_chars:
            blocks.append((start, " ".join(buf)))
            buf, start, size = [], None, 0
    if buf:
        blocks.append((start, " ".join(buf)))
    return blocks


def _srt_lines(text):
    """解析 srt → [(start_sec, 字幕文本)]；时间戳解析不了就退成 None。"""
    lines, blocks = [], re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for b in blocks:
        rows = [r for r in b.strip().splitlines() if r.strip()]
        if not rows:
            continue
        start, body = None, []
        for r in rows:
            m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->", r.strip())
            if m:
                start = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000
                continue
            if r.strip().isdigit() and not body:
                continue
            body.append(r.strip())
        if body:
            lines.append((start, " ".join(body)))
    return lines


def _read_transcript_lines(workspace):
    """按「离成品文案最近」依次尝试，返回 (lines, source, label, lang)。

    1. trans.srt —— **中文译文**，用户确认的主素材：已过 ASR 纠错 + LLM
       翻译润色 + 分句对齐，中文可直接做钩子/金句，且信息密度最高（同一
       33 分钟视频 6301 中文字 vs 英文 19763 字符）→ 往往能全文直送
    2. src.srt   —— 英文原文分句字幕（与 trans 逐条对齐）
    3. log/split_by_meaning.txt / split_by_nlp.txt —— 英文完整句（未切碎）
    4. log/cleaned_chunks.json —— 第 2 步词级转录（第 3 步纠错后覆盖）
    5. log/asr_checkpoint/seg_*.json —— 第 2 步断点，转录未跑完时可用
    """
    log = os.path.join(workspace, "log")

    for name, source, label, lang in (
            ("trans.srt", "trans_srt", "中文译文字幕（trans.srt）", "中文译文"),
            ("src.srt", "src_srt", "英文原文分句字幕（src.srt）", "英文原文")):
        p = os.path.join(workspace, name)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8-sig") as f:
                    lines = _srt_lines(f.read())
                if lines:
                    return lines, source, label, lang
            except OSError:
                pass

    for name, label in (("split_by_meaning.txt", "语义分句（split_by_meaning.txt）"),
                        ("split_by_nlp.txt", "NLP 分句（split_by_nlp.txt）")):
        p = os.path.join(log, name)
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    lines = [(None, r.strip()) for r in f if r.strip()]
                if lines:
                    return lines, name.split(".")[0], label, "英文原文"
            except OSError:
                pass

    p = os.path.join(log, "cleaned_chunks.json")
    if os.path.isfile(p):
        try:
            from core.utils.words_store import load_words_json
            lines = _group_words(load_words_json(p) or [])
            if lines:
                return lines, "cleaned_chunks", "词级转录（cleaned_chunks.json）", "英文原文"
        except Exception:                                          # noqa: BLE001
            pass

    ck = os.path.join(log, "asr_checkpoint")
    if os.path.isdir(ck):
        segs = []
        try:
            for fn in sorted(os.listdir(ck)):
                if not (fn.startswith("seg_") and fn.endswith(".json")):
                    continue
                with open(os.path.join(ck, fn), encoding="utf-8") as f:
                    d = json.load(f)
                one = d.get("segments", []) if isinstance(d, dict) else d
                for s in one or []:
                    if isinstance(s, dict) and str(s.get("text") or "").strip():
                        segs.append((s.get("start"), str(s["text"]).strip()))
        except (OSError, ValueError):
            segs = []
        if segs:
            segs.sort(key=lambda x: float(x[0]) if x[0] is not None else 0)
            return segs, "asr_checkpoint", "ASR 分段断点（转录可能尚未完成）", "英文原文"

    return [], "none", "无转录结果", ""


def full_text(lines, mark_every=50):
    """未超预算时全文直送：只在每 mark_every 行插一个时间标记（不逐行加，
    否则字幕条数多时时间戳本身就能把体积撑大一倍）。"""
    out = []
    for i, (st, text) in enumerate(lines):
        if st is not None and i % mark_every == 0:
            out.append(f"[{_fmt_ts(st)}]")
        out.append(text)
    return "\n".join(out)


def sample_lines(lines, budget, n_samples):
    """按位置（≈ 时间）均匀取 n 段，头尾加权，总长受 budget 约束。

    每段的文本带 `[mm:ss]` 起点，方便 LLM 引用具体语境；无时间戳的来源退
    化成 `#序号`。返回纯文本，调用方直接塞进 prompt。
    """
    if not lines:
        return ""
    total = len(lines)
    n = max(1, min(int(n_samples or DEFAULT_SAMPLES), total))
    per = max(200, int(budget) // n)
    parts = []
    for i in range(n):
        lo, hi = i * total // n, (i + 1) * total // n
        window = lines[lo:hi] or [lines[min(lo, total - 1)]]
        quota = int(per * (1.5 if i in (0, n - 1) and n > 1 else 1.0))
        picked, used = [], 0
        for st, text in window:
            if picked and used + len(text) > quota:
                break
            picked.append((st, text))
            used += len(text) + 1
        a = picked[0][0]
        head = _fmt_ts(a) if a is not None else f"#{lo + 1}"
        tag = "（开头）" if i == 0 and n > 1 else ("（结尾）" if i == n - 1 and n > 1 else "")
        parts.append(f"### 转录节选 {i + 1}/{n} [{head}]{tag}\n"
                     + "\n".join(t for _, t in picked))
    text = "\n\n".join(parts)
    cap = int(budget * 1.25)
    if len(text) > cap:
        text = text[:cap] + "\n…（节选到此，以下省略）"
    return text


def load_transcript(workspace="output") -> dict:
    """读转录：未超预算全文直送，超了才均匀采样。

    中文译文信息密度高（33 分钟 ≈ 6300 字），绝大多数视频压根不会触发
    采样；把 bili.transcript_chars 调大即可退化为「永远全文」。
    """
    budget = _cfg_int("bili.transcript_chars", DEFAULT_CHARS, 1000, 60000)
    samples = _cfg_int("bili.transcript_samples", DEFAULT_SAMPLES, 2, 30)
    lines, source, label, lang = _read_transcript_lines(workspace)
    total_chars = sum(len(t) for _, t in lines)
    starts = [float(st) for st, _ in lines if st is not None]
    duration = (max(starts) - min(starts)) if starts else 0.0

    summary = ""
    p = os.path.join(workspace, "log", "summary.txt")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                summary = f.read().strip()[:1200]
        except OSError:
            pass

    available = bool(lines) and total_chars >= MIN_TRANSCRIPT_CHARS
    sampled = available and total_chars > budget
    body = (sample_lines(lines, budget, samples) if sampled
            else full_text(lines) if available else "")
    text = ""
    if body:
        scope = (f"均匀节选 {samples} 段（超预算）" if sampled else "全文")
        text = (_SENTINEL + f"\n语言：{lang}；范围：{scope}；"
                f"正文 {total_chars} 字\n\n" + body)
    if body and summary:
        text += ("\n\n### 全局内容摘要（第 4.1 步基于完整转录生成，"
                 "用于补足节选未覆盖的部分）\n" + summary)

    return {"available": available, "source": source, "label": label,
            "lang": lang, "sampled": sampled, "text": text,
            "summary": summary, "has_summary": bool(summary),
            "chars": len(text), "total_chars": total_chars, "lines": len(lines),
            "duration": duration, "samples": samples if sampled else 0,
            "budget": budget}


def transcript_brief(tr) -> dict:
    """给 UI / 落盘用的精简状态（不带全文，避免大响应）。"""
    return {k: tr.get(k) for k in
            ("available", "source", "label", "lang", "sampled", "chars",
             "total_chars", "lines", "duration", "samples", "budget",
             "has_summary")}


def suggest_speaker(ctx, transcript_text="") -> str:
    """讲者名建议：参考文本 + 转录开头里能认出的人名池成员，否则退回频道名。"""
    from core.utils.config_utils import load_key
    pool = list(_SPEAKER_POOL)
    try:
        pool += [str(s) for s in (load_key("bili.speaker_names") or []) if s]
    except Exception:                                              # noqa: BLE001
        pass
    hay = " ".join([ctx.get("title") or "", ctx.get("uploader") or "",
                    ctx.get("description") or "",
                    (transcript_text or "")[:3000]]).lower()
    for name in pool:
        if name.lower() in hay:
            return name
    return ctx.get("uploader") or ""


def _valid_bili_meta(data, ep_tag):
    """shape + 公式硬红线校验（ask_gpt valid_def 协议）。

    失败 message 直接写给 LLM 看重试方向：全角｜四段、EP 前缀、恰好 10 个
    标签都是实测公式，不是可选项。
    """
    if not isinstance(data, dict):
        return {"status": "error", "message": "result must be a JSON object"}
    titles, desc, tags = data.get("titles"), data.get("desc"), data.get("tags")
    if not isinstance(titles, list) or len(titles) != 3:
        return {"status": "error",
                "message": "titles must be an array of exactly 3 strings"}
    for t in titles:
        if not isinstance(t, str) or not t.strip():
            return {"status": "error", "message": "each title must be a non-empty string"}
        if not t.strip().startswith(ep_tag):
            return {"status": "error",
                    "message": f"every title must start with {ep_tag}"}
        if t.count("｜") != 3:
            return {"status": "error",
                    "message": "every title must be 4 segments joined by full-width ｜ (U+FF5C), e.g. "
                               f"{ep_tag}｜钩子｜主题词｜讲者"}
    if not isinstance(desc, str) or not (200 <= len(desc.strip()) <= 2000):
        return {"status": "error",
                "message": "desc must be a string of 200-2000 characters (newlines as \\n)"}
    if not isinstance(tags, list) or len(tags) != 10:
        return {"status": "error", "message": "tags must be an array of exactly 10 strings"}
    for g in tags:
        if not isinstance(g, str) or not g.strip() or len(g.strip()) > 10:
            return {"status": "error",
                    "message": "each tag must be a non-empty string of at most 10 characters, no symbols"}
    return {"status": "success", "message": "ok"}


def _render_md(result, ep_tag, speaker, video_title, tr) -> str:
    src = tr.get("label", "无")
    lines = [
        f"# B站投稿文案（{ep_tag}｜{speaker}）",
        "",
        f"> 一手素材：{src} · {'均匀节选 ' + str(tr.get('samples', 0)) + ' 段' if tr.get('sampled') else '全文直送'}"
        f" · 正文 {tr.get('total_chars', 0)} 字",
        f"> 原视频信息（仅参考，不主导选题）：{video_title}",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}（任务链 bili_meta，模板可在「术语 & 提示词」页编辑）",
        "",
        "## 标题候选",
    ]
    lines += [f"{i}. {t}" for i, t in enumerate(result["titles"], 1)]
    lines += ["", "## 简介", "", result["desc"], "",
              "## 标签", "", "，".join(result["tags"]), ""]
    return "\n".join(lines)


def load_saved_bili_meta(workspace="output") -> dict | None:
    """读已落盘的 `bili_meta.md` → 结构化三件套（与 `_render_md` 配对的唯一解析）。

    FR-27 v1.37：刷新页面后前端靠 `GET /api/bili/context` 的 `existing` 回显
    上次结果。**md 是唯一真相源** —— 不另写 JSON、不塞 localStorage；归档把
    md 搬进 history/ 后这里自然读不到，正是「新视频不显示旧文案」的正确行为。

    文件不存在 / 编码坏 / 段落缺失 / 公式校验不过一律返回 `None`：用户手改坏
    一个 md 不能让 `/context` 500，更不能把半截文案当成成功结果回显。
    """
    path = os.path.join(workspace, OUTPUT_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeError):
        return None

    prefix = "# B站投稿文案（"
    if not lines or not lines[0].startswith(prefix):
        return None
    head = lines[0][len(prefix):].rstrip("）")
    if "｜" not in head:
        return None
    ep_tag, speaker = (s.strip() for s in head.rsplit("｜", 1))
    if not re.fullmatch(r"EP\d{3}", ep_tag) or not speaker:
        return None

    def _section(name):
        """取 `## name` 到下一个 `## ` 之间的原始行；缺节返回 None。"""
        try:
            start = lines.index("## " + name)
        except ValueError:
            return None
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith("## ")), len(lines))
        return lines[start + 1:end]

    title_rows, desc_rows, tag_rows = (_section("标题候选"), _section("简介"),
                                       _section("标签"))
    if title_rows is None or desc_rows is None or tag_rows is None:
        return None

    titles = [m.group(1).strip() for row in title_rows
              if (m := re.match(r"^\s*\d+\.\s+(.+?)\s*$", row))]
    desc = "\n".join(desc_rows).strip()
    tags = [t.strip() for row in tag_rows if row.strip()
            for t in row.split("，") if t.strip()]
    result = {"titles": titles, "desc": desc, "tags": tags}
    # 唯一校验仍是 _valid_bili_meta：回显与生成走同一套公式硬红线
    if _valid_bili_meta(result, ep_tag)["status"] != "success":
        return None

    video_title, generated_at = "", ""
    for row in lines:
        if row.startswith("> 原视频信息（仅参考，不主导选题）："):
            video_title = row.split("：", 1)[-1].strip()
        elif row.startswith("> 生成时间："):
            generated_at = row.split("：", 1)[-1].split("（", 1)[0].strip()
    return {**result, "ep_tag": ep_tag, "speaker": speaker,
            "video_title": video_title, "saved_to": path,
            "generated_at": generated_at}


def generate_bili_meta(ep, speaker="", extra_hint="",
                       title=None, description=None, workspace="output") -> dict:
    """生成三件套并落盘 output/bili_meta.md，返回结构化结果。"""
    try:
        ep = int(ep)
    except (TypeError, ValueError):
        raise ValueError("EP 序号必须是数字") from None
    if not 0 < ep <= 999:
        raise ValueError("EP 序号需在 1–999 之间")

    tr = load_transcript(workspace)
    if not tr["available"]:
        raise ValueError(
            "没有可用的视频转录结果：标题必须以真实内容为依据。请先跑到第 6 步"
            "「生成字幕」产出 output/trans.srt（中文译文，最佳素材）；"
            "若只想早点生成，至少跑完第 1 步「转录 whisperX」"
            "产出 output/log/cleaned_chunks.json")

    ctx = load_video_context(workspace)
    # 原视频信息降级为次要参考：标题/简介缺失不再阻断生成（转录才是关键）
    video_title = (title or "").strip() or ctx["title"] or "（无原视频标题）"
    if description is None:
        description = ctx["description"]
    speaker = (speaker or "").strip() or suggest_speaker(ctx, tr["text"])
    if not speaker:
        raise ValueError("缺少讲者名（标题第 4 段）：自动识别失败，请在页面上填写")

    from core.prompts import get_bili_meta_prompt
    from core.utils.ask_gpt import ask_gpt
    ep_tag = f"EP{ep:03d}"
    meta_line = (f"来源 {tr['label']}（{tr['lang']}），"
                 f"{'均匀节选 ' + str(tr['samples']) + ' 段' if tr['sampled'] else '全文'}, "
                 f"正文 {tr['total_chars']} 字，视频全长约 {_fmt_ts(tr['duration'])}")
    prompt = get_bili_meta_prompt(video_title, description, speaker, ep,
                                  extra_hint, transcript=tr["text"],
                                  transcript_meta=meta_line)
    rprint(f"[cyan]📺 生成 B站投稿文案（{ep_tag}｜{speaker}）…[/cyan]")
    rprint(f"[cyan]   一手素材：{tr['label']}（{tr['lang']}），"
           f"{'节选 ' + str(tr['samples']) + ' 段' if tr['sampled'] else '全文直送'}"
           f" {tr['chars']}/{tr['total_chars']} 字"
           f"{' + 全局摘要' if tr['has_summary'] else ''}[/cyan]")
    result = ask_gpt(prompt, resp_type="json",
                     valid_def=lambda d: _valid_bili_meta(d, ep_tag),
                     log_title="bili_meta")

    md = _render_md(result, ep_tag, speaker, video_title, tr)
    saved_to = os.path.join(workspace, OUTPUT_FILE)
    with open(saved_to, "w", encoding="utf-8") as f:
        f.write(md)
    rprint(f"[green]✅ 投稿文案已生成 → {saved_to}[/green]")
    return {"titles": result["titles"], "desc": result["desc"],
            "tags": result["tags"], "ep_tag": ep_tag, "speaker": speaker,
            "video_title": video_title, "saved_to": saved_to,
            "transcript": transcript_brief(tr)}
