"""只读 ASR 对照实验（FR-43）：同一段音频换参数重转，看错位能否从源头消除。

必须在装有 qwen-asr 的 qw 环境运行（与 qwen3_asr.python_exe 同一个）：

    D:/app/miniconda3/envs/qw/python.exe check_asr_probe.py
    D:/app/miniconda3/envs/qw/python.exe check_asr_probe.py --groups G0,G1,G4

约束：不 import 流水线模块、不写 output/、不改 config.yaml 与断点。
词级整形复用 core/asr_backend/qwen3_asr_runner.py（按路径加载，不复制逻辑）。
结果默认写入 <系统临时目录>/asr_probe/result.json，并打印到 stdout。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNNER_PATH = ROOT / "core" / "asr_backend" / "qwen3_asr_runner.py"
DEFAULT_AUDIO = ROOT / "output" / "audio" / "raw.mp3"
SAMPLE_RATE = 16000

# 观察区间（绝对秒）与判成功标准，见 PRD FR-43。
RANGE_LO, RANGE_HI = 476.0, 495.0
PROBABLY_OK = (487.0, 488.8)
THATS_MAX = 486.5
MAX_GAP_RUN = 2          # 连续 >=1.0s 间隙最多允许 2 个
BIG_GAP = 1.0
WORDS_TOLERANCE = 0.03   # 词数与基线相差 <=3%（至少放宽 1 个词）

DEFAULT_CONTEXT = ("Thrive Cosmetics. thrivecosmetics.com slash Jillian. "
                   "C-A-U-S-E-M-E-T-I-C-S.com")

# name: (窗口起点, 窗口长度, language, 是否带 context)
GROUPS = {
    "G0": (268.5, 270.0, None, False),       # 生产基线复现
    "G1": (268.5, 270.0, "English", False),
    "G2": (268.5, 270.0, "English", True),
    "G3": (465.0, 35.0, None, False),
    "G4": (465.0, 35.0, "English", False),
    "G5": (465.0, 35.0, "English", True),
    "G6": (440.0, 80.0, "English", False),
}


# ---------------------------------------------------------------- 纯函数 ---
def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def words_in_range(words: list[dict], lo: float = RANGE_LO,
                   hi: float = RANGE_HI) -> list[dict]:
    return [w for w in words if lo <= float(w["start"]) <= hi]


def max_gap_run(words: list[dict], big_gap: float = BIG_GAP) -> int:
    """连续出现的 >=big_gap 词间隙的最长个数（相邻词之间才算一个间隙）。"""
    best = run = 0
    for prev, cur in zip(words, words[1:]):
        if float(cur["start"]) - float(prev["end"]) >= big_gap:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def summarize_words(words: list[dict], text: str = "") -> dict:
    """观察区间摘要：Probably / That's 起点、大间隙统计、网址拼读痕迹。"""
    seen = words_in_range(words)
    probably = next((i for i, w in enumerate(seen)
                     if _norm(w["word"]) == "probably"), None)
    thats = None
    if probably is not None:
        for i in range(probably - 1, -1, -1):
            if _norm(seen[i]["word"]) == "thats":
                thats = i
                break
    bridge = ""
    m = re.search(r"first order(.*?)probably", text or "", re.I | re.S)
    if m:
        bridge = m.group(1).strip()
    low = (bridge or text or "").lower()
    return {
        "words_in_range": len(seen),
        "probably_start": seen[probably]["start"] if probably is not None else None,
        "thats_start": seen[thats]["start"] if thats is not None else None,
        "big_gaps": sum(1 for a, b in zip(seen, seen[1:])
                        if float(b["start"]) - float(a["end"]) >= BIG_GAP),
        "max_gap_run": max_gap_run(seen),
        "bridge_text": bridge,
        "has_url_spelling": any(k in low for k in ("cause", "slash", ".com")),
    }


def verdict(summary: dict, baseline_words: int | None = None) -> dict:
    """按 FR-43 判成功标准给出逐项 True/False 与总判定。"""
    p, t = summary["probably_start"], summary["thats_start"]
    checks = {
        "probably_ok": p is not None and PROBABLY_OK[0] <= p <= PROBABLY_OK[1],
        "thats_ok": t is not None and t <= THATS_MAX,
        "gap_run_ok": summary["max_gap_run"] <= MAX_GAP_RUN,
    }
    if baseline_words is None:
        checks["words_ok"] = None          # 无基线，留给人判断
    else:
        tol = max(1, round(baseline_words * WORDS_TOLERANCE))
        checks["words_ok"] = abs(summary["words_in_range"] - baseline_words) <= tol
    required = [v for v in checks.values() if v is not None]
    return {**checks, "pass": all(required)}


# ----------------------------------------------------------- 加载与推理 ---
def load_runner():
    spec = importlib.util.spec_from_file_location("qwen3_asr_runner_probe", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_model(model_dir: str, aligner_dir: str, device: str, dtype_name: str):
    import torch
    from qwen_asr import Qwen3ASRModel

    dtype = getattr(torch, dtype_name)
    return Qwen3ASRModel.from_pretrained(
        model_dir, dtype=dtype, device_map=device,
        max_inference_batch_size=8, max_new_tokens=1024,
        forced_aligner=aligner_dir,
        forced_aligner_kwargs={"dtype": dtype, "device_map": device},
    )


def run_group(runner, model, audio_file: str, name: str, context: str) -> dict:
    start, length, language, use_ctx = GROUPS[name]
    audio = runner.decode_window(audio_file, start, length)
    kwargs = {"audio": (audio, SAMPLE_RATE), "language": language,
              "return_time_stamps": True}
    if use_ctx:
        kwargs["context"] = context
    t0 = time.time()
    results = model.transcribe(**kwargs)
    cost = round(time.time() - t0, 1)
    result = results[0]
    raw = runner.parse_stamps(result)
    words, stats = runner.normalize_words(raw)
    absolute = [{"word": w["word"], "start": round(start + w["start"], 3),
                 "end": round(start + w["end"], 3)} for w in words]
    text = str(getattr(result, "text", "") or "")
    return {"group": name, "window": [start, start + length],
            "language": language, "context": context if use_ctx else "",
            "seconds": cost, "normalize_stats": stats,
            "text_len": len(text), "text": text,
            "words_in_range": words_in_range(absolute),
            "summary": summarize_words(absolute, text)}


def print_report(rows: list[dict]) -> None:
    base = next((r for r in rows if r["group"] == "G0"), None)
    base_n = base["summary"]["words_in_range"] if base else None
    print("\n==== FR-43 对照结果（真实开口 Probably≈487.8；That's 应在广告尾句）====")
    for r in rows:
        s = r["summary"]
        v = verdict(s, base_n)
        r["verdict"] = v
        print(f"\n[{r['group']}] window={r['window']} lang={r['language']} "
              f"ctx={'Y' if r['context'] else 'N'} {r['seconds']}s")
        print(f"  Probably={s['probably_start']}  That's={s['thats_start']}  "
              f"区间词数={s['words_in_range']}  大间隙={s['big_gaps']}  "
              f"最长连续大间隙={s['max_gap_run']}  网址拼读={s['has_url_spelling']}")
        print(f"  桥接文本: {s['bridge_text'][:200]!r}")
        print(f"  判定: {'PASS' if v['pass'] else 'FAIL'}  {v}")
        print("  区间词: " + " ".join(
            f"{w['word']}@{w['start']:.2f}" for w in r["words_in_range"]))


def main() -> int:
    ap = argparse.ArgumentParser(description="FR-43 只读 ASR 对照实验")
    ap.add_argument("--audio", default=str(DEFAULT_AUDIO))
    ap.add_argument("--groups", default=",".join(GROUPS))
    ap.add_argument("--context", default=DEFAULT_CONTEXT)
    ap.add_argument("--model", default="D:/models/Qwen3-ASR-1.7B")
    ap.add_argument("--aligner", default="D:/models/Qwen3-ForcedAligner-0.6B")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--dtype", default="bfloat16")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "asr_probe" / "result.json"))
    args = ap.parse_args()

    names = [g.strip() for g in args.groups.split(",") if g.strip()]
    unknown = [g for g in names if g not in GROUPS]
    if unknown:
        print(f"未知分组: {unknown}，可用 {list(GROUPS)}", file=sys.stderr)
        return 2
    if "G0" not in names:
        print("提示：未含 G0，词数对照将缺基线。", file=sys.stderr)

    runner = load_runner()
    print(f"[probe] loading model on {args.device} ...", file=sys.stderr)
    model = load_model(args.model, args.aligner, args.device, args.dtype)

    rows = []
    for name in names:  # 串行：12GB 显存只放一个模型
        print(f"[probe] running {name} ...", file=sys.stderr, flush=True)
        rows.append(run_group(runner, model, args.audio, name, args.context))

    print_report(rows)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[probe] 结果已写入 {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
