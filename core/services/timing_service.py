"""Pipeline step timing — the only place that records/parses step durations.

``server/task_manager.py`` records one line per finished step into
``output/log/step_timings.json`` (atomic replace, same discipline as the
other checkpoints). Historical ``pipeline_*.log`` files that predate the
structured marker are replayed by :func:`backfill`.

Log contract::

    20:47:45 --- 步骤开始：合并配音到视频 (dub_to_vid)
    20:53:10 --- 步骤结束：合并配音到视频 (dub_to_vid) 耗时 325.4s 状态=ok
    20:53:10 === 任务结束 状态=completed 2026-11-13 20:53:10 ===

Old logs have no ``步骤结束`` line; their duration is inferred from the next
step-start / task-end / last-log-line timestamp (1-second precision).

Pure stdlib, no project imports: importable from tests, batch workspaces and
the server alike.
"""
from __future__ import annotations

import glob
import json
import os
import re
import tempfile
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(_PROJECT_ROOT, "output", "log")
TIMING_FILE = os.path.join(LOG_DIR, "step_timings.json")
MAX_RECORDS = 2000  # one line per step run; keep the file small forever

_START_RE = re.compile(r"--- 步骤开始：(.+?) \(([\w-]+)\)")
_END_RE = re.compile(r"--- 步骤结束：(.+?) \(([\w-]+)\) 耗时 ([\d.]+)s 状态=(\w+)")
_TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")
_TASK_END_RE = re.compile(r"=== 任务结束 状态=(\w+)")


def _seconds(ts: str) -> int | None:
    m = _TS_RE.match(ts)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


def parse_log(text: str, source: str = "") -> list[dict]:
    """Extract one record per step. Prefers the explicit end marker; falls
    back to the next boundary for logs written before this service existed."""
    # Recover an absolute date from the log file name (pipeline_YYYY-MM-DD_…)
    # so historical steps also show a real start/end time, not just HH:MM:SS.
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", source)
    date_part = date_match.group(1) if date_match else ""
    lines = text.splitlines()
    starts, ends, task_status = [], {}, "completed"
    for i, line in enumerate(lines):
        m = _START_RE.search(line)
        if m:
            starts.append({"line": i, "title": m.group(1), "id": m.group(2),
                           "start": _seconds(line), "ts": line[:8]})
            continue
        m = _END_RE.search(line)
        if m:
            ends[m.group(2)] = (float(m.group(3)), m.group(4))
            continue
        m = _TASK_END_RE.search(line)
        if m:
            task_status = m.group(1)

    boundaries = [s["line"] for s in starts[1:]]
    # A task-end line (or EOF) closes the last step.
    for i, line in enumerate(lines):
        if _TASK_END_RE.search(line):
            boundaries.append(i)
            break
    else:
        boundaries.append(len(lines) - 1)

    out = []
    for idx, s in enumerate(starts):
        end_line = boundaries[idx] if idx < len(boundaries) else len(lines) - 1
        explicit = ends.get(s["id"])
        if explicit:
            seconds, status = explicit
        else:
            a, b = s["start"], _seconds(lines[min(end_line, len(lines) - 1)])
            seconds = float((b - a) % 86400) if (a is not None and b is not None) else 0.0
            # Only the final step of a failed task is the failing one.
            status = task_status if (idx == len(starts) - 1 and task_status != "completed") else "ok"
        start_ts = s["ts"]
        started_at = f"{date_part} {start_ts}" if date_part else start_ts
        try:
            ended_dt = (datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                        + timedelta(seconds=round(float(seconds), 1)))
            ended_at = ended_dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            ended_at = ""
        out.append({
            "id": s["id"], "title": s["title"], "seconds": round(float(seconds), 1),
            "status": status, "at": s["ts"], "source": source,
            "exact": explicit is not None,
            "started_at": started_at, "ended_at": ended_at,
        })
    return out


def _read() -> dict:
    try:
        with open(TIMING_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "updated_at": "", "records": [], "last_run": []}


def _write(data: dict) -> None:
    """Atomic replace — never leave a half-written timing file behind."""
    os.makedirs(LOG_DIR, exist_ok=True)
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["records"] = data["records"][-MAX_RECORDS:]
    fd, tmp = tempfile.mkstemp(dir=LOG_DIR, prefix=".timings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, TIMING_FILE)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def record(step_id: str, title: str, seconds: float, status: str = "ok",
           no: int = 0, started_at: str | None = None,
           ended_at: str | None = None) -> None:
    """Append one finished step. Must never break the pipeline on IO errors.

    `ended_at` / `started_at` default to wall-clock now and now-seconds, so a
    single call records both timestamps; callers may pass explicit values if
    they tracked the start moment themselves.
    """
    try:
        end = (datetime.strptime(ended_at, "%Y-%m-%d %H:%M:%S")
               if ended_at else datetime.now())
        start = (datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
                 if started_at else end - timedelta(seconds=float(seconds)))
        data = _read()
        data["records"].append({
            "id": step_id, "no": no, "title": title,
            "seconds": round(float(seconds), 1), "status": status,
            "at": end.strftime("%H:%M:%S"), "source": "live", "exact": True,
            "started_at": start.strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": end.strftime("%Y-%m-%d %H:%M:%S"),
        })
        _write(data)
    except Exception:  # noqa: BLE001 — timing is diagnostic, never fatal
        pass


def backfill(log_dir: str | None = None) -> int:
    """Replay historical pipeline_*.log files into the timing store.

    Old logs are read-only; duplicates are keyed by (source, id, at) so this
    is idempotent and can be re-run after every upgrade.
    """
    directory = log_dir or LOG_DIR
    data = _read()
    seen = {(r.get("source"), r.get("id"), r.get("at"), r.get("seconds"))
            for r in data["records"]}
    from core.pipeline import STEP_NO  # local import: keeps this module stdlib-only

    added = 0
    for path in sorted(glob.glob(os.path.join(directory, "pipeline_20*.log"))):
        source = os.path.basename(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for rec in parse_log(text, source):
            rec["no"] = STEP_NO.get(rec["id"], 0)
            key = (rec["source"], rec["id"], rec["at"], rec["seconds"])
            if key in seen:
                continue
            seen.add(key)
            data["records"].append(rec)
            added += 1
    if added:
        _write(data)
    return added


def summary() -> dict:
    """Aggregate by step id: last / best / count / total seconds."""
    by_step: dict[str, dict] = {}
    for r in _read()["records"]:
        sid = r.get("id")
        if not sid:
            continue
        sec = float(r.get("seconds") or 0)
        cur = by_step.setdefault(sid, {
            "id": sid, "no": r.get("no", 0), "title": r.get("title", sid),
            "last": sec, "last_status": r.get("status", "ok"),
            "last_started_at": r.get("started_at"),
            "last_ended_at": r.get("ended_at"),
            "best": float("inf"), "count": 0, "total": 0.0,
        })
        status = r.get("status", "ok")
        cur["last"] = sec
        cur["last_status"] = status
        cur["title"] = r.get("title", sid)
        cur["last_started_at"] = r.get("started_at")
        cur["last_ended_at"] = r.get("ended_at")
        cur["no"] = cur["no"] or r.get("no", 0)
        # An aborted/failed run is not a speed record.
        if status == "ok" and sec > 0:
            cur["best"] = min(cur["best"], sec)
        cur["count"] += 1
        cur["total"] = round(cur["total"] + sec, 1)
    for cur in by_step.values():
        if cur["best"] == float("inf"):
            cur["best"] = 0.0
    return {"by_step": by_step,
            "updated_at": _read()["updated_at"],
            "runs": len({r.get("source") for r in _read()["records"]})}


def attach_last_seconds(steps: list[dict]) -> list[dict]:
    """Enrich ``pipeline_status()`` output in place for the UI."""
    by_step = summary()["by_step"]
    for s in steps:
        hit = by_step.get(s["id"])
        s["last_seconds"] = hit["last"] if hit else None
        s["last_status"] = hit["last_status"] if hit else None
        s["last_started_at"] = hit["last_started_at"] if hit else None
        s["last_ended_at"] = hit["last_ended_at"] if hit else None
        s["run_count"] = hit["count"] if hit else 0
    return steps


def dub_breakdown(text: str) -> dict:
    """Stage-level breakdown of step 10 from its own log lines.

    IndexTTS-2.5 prints gpt/s2mel/bigvgan timings per segment; summing them
    tells us exactly how much of the wall time is model inference vs. vl's
    own ffmpeg/file work.
    """
    def at(pattern: str) -> int | None:
        for line in text.splitlines():
            if pattern in line:
                t = _seconds(line)
                if t is not None:
                    return t
        return None

    def total(pattern: str) -> tuple[int, float]:
        vals = [float(x) for x in re.findall(pattern, text)]
        return len(vals), round(sum(vals), 1)

    t0 = at("Starting TTS audio generation")
    t1 = at("TTS audio generation completed")
    t2 = at("Audio chunks processing completed")
    n_g, gpt = total(r">> gpt_gen_time: ([\d.]+) seconds")
    _, s2mel = total(r">> s2mel_time: ([\d.]+) seconds")
    _, bigvgan = total(r">> bigvgan_time: ([\d.]+) seconds")
    _, infer = total(r">> Total inference time: ([\d.]+) seconds")
    return {
        "segments": n_g,
        "tts_seconds": float((t1 - t0) % 86400) if None not in (t0, t1) else None,
        "chunks_seconds": float((t2 - t1) % 86400) if None not in (t1, t2) else None,
        "gpt_seconds": gpt, "s2mel_seconds": s2mel,
        "bigvgan_seconds": bigvgan, "inference_seconds": infer,
        "overhead_seconds": round(infer - gpt - s2mel - bigvgan, 1),
    }


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(round(seconds))
    if s >= 3600:
        return f"{s // 3600}h{str((s % 3600) // 60).zfill(2)}m{str(s % 60).zfill(2)}s"
    if s >= 60:
        return f"{s // 60}m{str(s % 60).zfill(2)}s"
    return f"{s}s"


def format_report() -> str:
    """Markdown table of every step's timings, newest run first."""
    by_step = summary()["by_step"]
    if not by_step:
        return "（暂无耗时记录，先跑一次流水线或执行 backfill）"
    from core.pipeline import STEP_NO  # noqa: F401  (ordering only)

    lines = ["| # | 步骤 | 上次 | 最快 | 次数 | 累计 |",
             "|---:|---|---:|---:|---:|---:|"]
    for sid, v in sorted(by_step.items(), key=lambda kv: (kv[1].get("no") or 99)):
        lines.append(
            f"| {v.get('no') or '-'} | {v['title']} ({sid}) | "
            f"{format_duration(v['last'])} | {format_duration(v['best'])} | "
            f"{v['count']} | {format_duration(v['total'])} |")
    return "\n".join(lines)


def latest_breakdown(log_dir: str | None = None) -> dict:
    """Find the most recent log that actually contains a step-10 run."""
    directory = log_dir or LOG_DIR
    for path in sorted(glob.glob(os.path.join(directory, "pipeline_20*.log")),
                       reverse=True):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        if "Starting TTS audio generation" in text:
            out = dub_breakdown(text)
            out["source"] = os.path.basename(path)
            return out
    return {}


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if "--backfill" in args or not os.path.isfile(TIMING_FILE):
        print(f"backfill: +{backfill()} 条历史步骤记录")
    print("\n=== 各步骤耗时（v1.28） ===")
    print(format_report())
    bd = latest_breakdown()
    if bd:
        print(f"\n=== 第 10 步内部拆解（{bd['source']}）===")
        print(f"  合成段数        : {bd['segments']}")
        print(f"  TTS 推理阶段    : {format_duration(bd['tts_seconds'])}")
        print(f"  ├ GPT 自回归    : {format_duration(bd['gpt_seconds'])}")
        print(f"  ├ s2mel 扩散    : {format_duration(bd['s2mel_seconds'])}")
        print(f"  ├ BigVGAN 声码器: {format_duration(bd['bigvgan_seconds'])}")
        print(f"  └ 其余(参考音频/文本/存盘): {format_duration(bd['overhead_seconds'])}")
        print(f"  变速与拼轴阶段  : {format_duration(bd['chunks_seconds'])}")
