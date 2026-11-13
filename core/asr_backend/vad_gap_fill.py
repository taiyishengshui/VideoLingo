"""补回被 VAD 漏掉的语音（通用机制，对所有视频生效）。

问题
----
whisperx 的 `transcribe()` 无条件先跑一遍 VAD（pyannote），只把 VAD 判为语音的
区间送进 whisper。VAD 偶发会把整段真实语音判成非语音 —— 实测一条 68 分钟播客漏了
14 处、合计 179.6 秒（4.4%），这些区间 whisper 根本没看到，字幕直接缺一整段。
（不是分句/翻译/对齐丢的：ASR checkpoint 里那些区间连一个 segment 都没有。）

对策
----
转写完一段后扫描「有声音但 VAD 没覆盖」的空档，临时绕开 VAD 单独转写这些区间，
再把结果插回去。把 `model.vad_model` 临时换成继承Vad的 `NoVad`，按30s分块。
补转清除领域提示词；对齐后检查原空档全范围（包括首尾）。仍有明显有声残留时，
用同一底层模型非批处理重试一次，失败明确告警，不能把非空文本当作补全成功。

纯逻辑部分（空档计算、有声占比、幻觉过滤）与本模块的可执行部分分开，前者可单测。
"""

import copy
import math
import re
import subprocess
from collections import Counter
from contextlib import contextmanager
from dataclasses import is_dataclass, replace
from difflib import SequenceMatcher

try:  # rprint 只为配色；没有 rich 时退化成 print，不影响补转逻辑
    from core.utils import rprint
except Exception:  # pragma: no cover
    rprint = print

__all__ = ["NoVad", "find_uncovered_gaps", "speech_ratio", "fill_gaps",
           "looks_like_hallucination", "gap_fill_settings", "DEFAULT_SETTINGS",
           "transcribe_unprompted", "prompt_echo"]

CHUNK_SECONDS = 30.0      # whisper 的训练窗口，也是 whisperx 默认 chunk_size

DEFAULT_SETTINGS = {
    "enabled": True,
    "min_gap": 3.0,        # 多大的空档才值得补（秒）
    "min_speech_ratio": 0.5,  # 有声占比阈值，有声不能直接等同人声
    "noise": "-35dB",      # silencedetect 的静音判定阈值
    "min_silence": 0.25,   # 多长才算一段静音
}


def _load_key(key, default):
    """load_key 缺键会抛；配置项允许缺失。"""
    try:
        from core.utils import load_key
        value = load_key(key)
    except Exception:
        return default
    return default if value is None else value


def gap_fill_settings():
    """从 config.yaml 读补转配置（whisper.vad_gap_fill.*），缺项用默认值。"""
    if not _load_key("whisper.vad_gap_fill", True):
        return dict(DEFAULT_SETTINGS, enabled=False)
    return {
        "enabled": True,
        "min_gap": float(_load_key("whisper.gap_min_seconds", DEFAULT_SETTINGS["min_gap"])),
        "min_speech_ratio": float(_load_key("whisper.gap_min_speech_ratio",
                                            DEFAULT_SETTINGS["min_speech_ratio"])),
        "noise": str(_load_key("whisper.gap_fill_noise", DEFAULT_SETTINGS["noise"])),
        "min_silence": float(_load_key("whisper.gap_fill_min_silence",
                                       DEFAULT_SETTINGS["min_silence"])),
    }


class _FullSpan:
    """whisperx `Vad.merge_chunks` 消费的元素：只需 start / end / speaker。"""

    __slots__ = ("start", "end", "speaker")

    def __init__(self, start, end, speaker="UNKNOWN"):
        self.start = float(start)
        self.end = float(end)
        self.speaker = speaker


def _vad_base():
    """whisperx 的 Vad 基类；取不到时用等价实现（保证 NoVad 可单测）。

    必须是 Vad 子类：whisperx 用 `issubclass(type(vad_model), Vad)` 决定走哪条
    分支 —— 子类走【我们自己的】preprocess_audio / merge_chunks，非子类走
    `Pyannote.*`，而 Pyannote.merge_chunks 会拿 pyannote 的 Binarize 处理返回值，
    要求它是 Annotation（有 .data）。第一版 NoVad 返回普通 list，于是每次补转都
    报 `'list' object has no attribute 'data'`。
    """
    try:
        from whisperx.vads import Vad
        return Vad
    except Exception:  # pragma: no cover - 无 whisperx 的环境（单测）
        return _FallbackVad


class _FallbackVad:
    """无 whisperx 时的等价基类：接口与行为对齐 whisperx.vads.Vad。"""

    def __init__(self, vad_onset=0.5):
        if not (0 < vad_onset < 1):
            raise ValueError("vad_onset is a decimal value between 0 and 1.")

    @staticmethod
    def preprocess_audio(audio):
        return audio

    @staticmethod
    def merge_chunks(segments, chunk_size, onset, offset):
        """与 whisperx Vad.merge_chunks 同构：超过 chunk_size 就切一块。"""
        merged, seg_idxs = [], []
        curr_start = segments[0].start
        curr_end = curr_start
        for seg in segments:
            if seg.end - curr_start > chunk_size and curr_end - curr_start > 0:
                merged.append({"start": curr_start, "end": curr_end,
                               "segments": seg_idxs})
                curr_start, seg_idxs = seg.start, []
            curr_end = seg.end
            seg_idxs.append((seg.start, seg.end))
        merged.append({"start": curr_start, "end": curr_end, "segments": seg_idxs})
        return merged


class NoVad(_vad_base()):
    """替换 whisperx 的 vad_model：把整段音频都当作语音。

    继承 Vad → whisperx 用本类的 preprocess_audio / merge_chunks，不再碰 pyannote
    的 Binarize。merge_chunks 把整段按 chunk_size（30s）切开，于是每一块都会被
    送进 whisper。
    """

    def __init__(self, vad_onset=0.5):
        super().__init__(vad_onset)

    @staticmethod
    def preprocess_audio(audio):
        return audio   # 我们只关心长度，不必转成 tensor

    def __call__(self, inputs):
        waveform = inputs.get("waveform") if isinstance(inputs, dict) else inputs
        try:
            n = int(waveform.shape[-1])
        except (AttributeError, IndexError, TypeError):
            try:
                n = int(len(waveform))
            except Exception:
                return []
        sr = int(inputs.get("sample_rate") or 16000) \
            if isinstance(inputs, dict) else 16000
        if sr <= 0 or n <= 0:
            return []
        # 按 30s 窗口切好再交出去：merge_chunks 只在【多个】span 之间切块，
        # 给一个"整段"span 它会原样返回（75s 一口喂给 whisper，超出其 30s
        # 训练窗口）。whisperx 的 transcribe 默认 chunk_size 就是 30。
        duration = n / sr
        spans, t = [], 0.0
        while duration - t > 1e-6:
            end = min(t + float(CHUNK_SECONDS), duration)
            spans.append(_FullSpan(t, end))
            t = end
        return spans


def find_uncovered_gaps(segments, min_gap, span=None):
    """实际对齐未覆盖区间；默认不扫描片头/片尾，显式span则验收整个范围。

    优先用词级时间戳，避免一个长segment把内部无词空洞遮住；无words字段的
    未对齐输入仍可按segment时间检查。有words但全无有效时间则视为未覆盖。
    """
    spans = []
    for seg in segments or []:
        entries = (seg.get("words") or []) if "words" in seg else [seg]
        for entry in entries:
            try:
                s, e = float(entry["start"]), float(entry["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(s) and math.isfinite(e) and e > s:
                spans.append((s, e))
    if not spans:
        return [tuple(span)] if span and span[1] - span[0] >= min_gap else []
    spans.sort()
    lo, hi = span if span else (spans[0][0], max(e for _, e in spans))
    gaps, cursor = [], lo
    for s, e in spans:
        if e <= lo or s >= hi:
            continue
        s, e = max(s, lo), min(e, hi)
        if s - cursor >= min_gap:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if hi - cursor >= min_gap:
        gaps.append((cursor, hi))
    return gaps


def speech_ratio(audio_file, start, end, noise="-35dB", min_silence=0.25,
                 timeout=120):
    """估算 [start, end) 里的有声占比（0~1），用 ffmpeg silencedetect。

    探测失败时返回 1.0（当作有声）：宁可多转一段，也不能因为探测出错而漏内容。
    """
    duration = max(0.0, end - start)
    if duration <= 0.05:
        return 0.0
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats",
        "-ss", f"{start:.3f}", "-i", audio_file, "-t", f"{duration:.3f}",
        "-af", f"silencedetect=noise={noise}:d={min_silence}",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors="ignore", timeout=timeout)
        if proc.returncode != 0:
            return 1.0
        stderr = proc.stderr or ""
    except Exception:
        return 1.0
    silence = sum(float(x) for x in re.findall(r"silence_duration:\s*([\d.]+)", stderr))
    return max(0.0, min(1.0, (duration - silence) / duration))


@contextmanager
def _unprompted(model):
    """补转只听音频，临时清掉领域提示，退出时还原整个options对象。"""
    original = getattr(model, "options", None)
    changed = False
    if original is not None:
        changes = {k: None for k in ("initial_prompt", "hotwords", "prefix")
                   if hasattr(original, k) or isinstance(original, dict) and k in original}
        if changes:
            if is_dataclass(original):
                clean = replace(original, **changes)
            elif hasattr(original, "_replace"):
                clean = original._replace(**changes)
            elif isinstance(original, dict):
                clean = {**original, **changes}
            else:
                clean = copy.copy(original)
                for k, v in changes.items():
                    setattr(clean, k, v)
            model.options = clean
            changed = True
    try:
        yield
    finally:
        if changed:
            model.options = original


def transcribe_unprompted(model, audio, batch_size=1):
    """第一次补转：保留现有WhisperX引擎，仅切换VAD和提示词。"""
    original_vad = getattr(model, "vad_model", None)
    model.vad_model = NoVad()
    try:
        with _unprompted(model):
            return model.transcribe(audio, batch_size=batch_size)
    finally:
        model.vad_model = original_vad


def _sequential_retry(model, audio):
    """同一底层Whisper权重，无VAD/无提示词的非批处理解码，不再加载第二个模型。"""
    language = getattr(getattr(model, "tokenizer", None), "language_code", None)
    language = language or getattr(model, "preset_language", None)
    found, _ = model.model.transcribe(
        audio, language=language, beam_size=5, vad_filter=False,
        initial_prompt=None, hotwords=None, condition_on_previous_text=False,
        word_timestamps=True)
    return {"segments": [
        {"start": s.start, "end": s.end, "text": s.text,
         "avg_logprob": s.avg_logprob} for s in found]}


def prompt_echo(segment, initial_prompt):
    """长段复述领域提示且对齐分低时才标可疑，不按低分直接删真实语音。"""
    tokens = lambda t: re.findall(r"[\w']+", (t or "").lower())
    prompt, text = tokens(initial_prompt), tokens(segment.get("text"))
    scores = [float(w["score"]) for w in segment.get("words") or []
              if w.get("score") is not None]
    if len(prompt) < 6 or len(text) < 6 or not scores or sum(scores) / len(scores) >= 0.5:
        return False
    return SequenceMatcher(None, prompt, text, autojunk=False).find_longest_match().size >= 6


def fill_gaps(*, model, segments, raw_audio_segment, vocal_audio_segment,
              seg_start, sample_rate, raw_audio_file, model_a, metadata,
              align_device, batch_size, settings, align_fn=None,
              retry_fn=None, repair_spans=None):
    """补转漏词区间并返回新增段；可靠重转的候选旧段从segments原地移除。

    时间坐标与 `segments` 入参一致（本分片内的相对秒）—— 调用方合并后会统一
    加上分片起点。

    返回的 segment 与 whisperx 的 align 输出同构（带 words 词级时间戳），可直接
    并入主结果。任一步失败都不会抛出 —— 补转是增强，失败最多是不补，
    绝不能连累已经转好的 95%。
    """
    sr = int(sample_rate)
    out = []
    gaps = find_uncovered_gaps(segments, settings["min_gap"])
    repairs = list(repair_spans or [])
    # 内部无词空洞：重转整个所属segment，替换成功才移除旧段，避免新增段
    # 插进旧segment内部后，按segment排序仍把词表的时间顺序弄乱。
    external_gaps = []
    for a, b in gaps:
        owner = next((s for s in segments
                      if s.get("start", a) < a and s.get("end", b) > b), None)
        if owner is not None:
            if not any(s is owner for s in repairs):
                repairs.append(owner)
        else:
            external_gaps.append((a, b))
    gaps = external_gaps
    if gaps or repairs:
        rprint(f"[cyan]空档扫描：{len(gaps)} 处 ≥{settings['min_gap']}s，"
               f"提示词回声候选 {len(repairs)} 处[/cyan]")

    def audible_gaps(picked, a, b):
        residual = find_uncovered_gaps(picked, settings["min_gap"], span=(0.0, b - a))
        found = []
        for x, y in residual:
            ratio = speech_ratio(raw_audio_file, seg_start + a + x, seg_start + a + y,
                                 settings["noise"], settings["min_silence"])
            # 真实讲话夹多次停顿时，总有声占比可能略低于50%，但仍有数秒人声。
            if (ratio >= settings["min_speech_ratio"]
                    or ratio * (y - x) >= settings["min_gap"]):
                found.append((x, y))
        return found

    def align_result(res, vocal_slice):
        picked = [copy.deepcopy(s) for s in res.get("segments", [])
                  if not looks_like_hallucination(s.get("text", ""))]
        if not picked:
            return []
        aligned = (align_fn or _whisperx_align)(
            picked, model_a, metadata, vocal_slice, align_device,
            return_char_alignments=False)
        return aligned.get("segments", [])

    jobs = [(a, b, None) for a, b in gaps]
    jobs += [(s["start"], s["end"], s) for s in repairs]
    for a, b, original in jobs:
        absolute = f"{seg_start + a:.3f}-{seg_start + b:.3f}s"
        ratio = speech_ratio(raw_audio_file, seg_start + a, seg_start + b,
                             settings["noise"], settings["min_silence"])
        # 多次停顿的短句可能不到50%有声；仍有至少1s声音时也应复核。
        # 32:32这类3.3s/8%静音仅0.25s有声，不会因此触发。
        if (ratio < settings["min_speech_ratio"]
                and ratio * (b - a) < min(1.0, settings["min_gap"])):
            rprint(f"[dim]   · {absolute} 有声 {ratio:.0%} "
                   f"< {settings['min_speech_ratio']:.0%}，跳过[/dim]")
            continue
        i0, i1 = int(a * sr), int(b * sr)
        raw_slice, vocal_slice = raw_audio_segment[i0:i1], vocal_audio_segment[i0:i1]
        if raw_slice.size < sr * 0.2:
            continue
        picked = []
        try:
            picked = align_result(transcribe_unprompted(model, raw_slice, batch_size),
                                  vocal_slice)
        except Exception as e:
            rprint(f"[yellow]空档 {absolute} 首轮补转/对齐失败：{e}[/yellow]")
        residual = audible_gaps(picked, a, b)
        if residual:
            rprint(f"[yellow]空档 {absolute} 补转后仍有 "
                   f"{sum(y - x for x, y in residual):.1f}s 有声未覆盖，"
                   "改用非批处理解码重试一次[/yellow]")
            try:
                retried = align_result((retry_fn or _sequential_retry)(model, raw_slice),
                                       vocal_slice)
                retry_residual = audible_gaps(retried, a, b)
                if retried and sum(y - x for x, y in retry_residual) < sum(y - x for x, y in residual):
                    picked, residual = retried, retry_residual
            except Exception as e:
                rprint(f"[yellow]空档 {absolute} 重试失败，保留已有结果：{e}[/yellow]")
        if residual:
            remaining = ", ".join(f"{seg_start + a + x:.3f}-{seg_start + a + y:.3f}s"
                                  for x, y in residual)
            rprint(f"[yellow]未解决有声空档：{remaining}；需要复核，不能视为补转完成[/yellow]")
        if not picked:
            continue
        # 候选原记录只有恢复完整且对齐正常时才移除；失败不破坏已有转录。
        if original is not None:
            scores = [w.get("score") for s in picked for w in s.get("words") or []
                      if w.get("score") is not None]
            if residual or not scores or sum(scores) / len(scores) < 0.5:
                rprint(f"[yellow]提示词回声 {absolute} 尚未可靠恢复，保留原记录[/yellow]")
                continue
            segments[:] = [s for s in segments if s is not original]
        # 只加本分片偏移a；调用方再加分片start，避免双重偏移。
        for s in picked:
            s["start"] = float(s.get("start", 0.0)) + a
            s["end"] = float(s.get("end", 0.0)) + a
            for w in s.get("words") or []:
                if "start" in w:
                    w["start"] = float(w["start"]) + a
                if "end" in w:
                    w["end"] = float(w["end"]) + a
        state = "部分补回，仍待复核" if residual else "补回并通过空档复查"
        rprint(f"[dim]   · {absolute} {state} {len(picked)} 段："
               f"{' '.join(s.get('text', '').strip() for s in picked)[:70]}[/dim]")
        out.extend(picked)
    return out


def _whisperx_align(segments, model_a, metadata, audio, device, **kwargs):
    import whisperx
    return whisperx.align(segments, model_a, metadata, audio, device, **kwargs)


def looks_like_hallucination(text):
    """判断补转出来的文本是否该丢弃。

    关掉 VAD 后 whisper 面对音乐/噪声会产幻觉（空串、纯标点、同一个词反复重复）。
    这里只做保守过滤 —— 宁可留下一点可疑文本，也不要误杀真实内容。
    """
    t = (text or "").strip()
    if len(t) < 2 or not re.search(r"[\w\u4e00-\u9fff]", t):
        return True
    words = re.findall(r"[\w']+", t.lower())
    if len(words) >= 5:
        _, top_n = Counter(words).most_common(1)[0]
        if len(set(words)) <= 3 and top_n / len(words) > 0.6:
            return True
    return False
