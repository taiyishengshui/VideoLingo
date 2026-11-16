"""Voice assignment review API - the human-in-the-loop control plane.

The contract at output/audio/voice_map.json is the single source of truth.
This router only edits it; the dubbing consumer (core/tts_backend/index_tts.py)
keeps reading it unchanged. Every write invalidates that consumer's in-process
cache, so a saved edit takes effect on the very next line without a restart.

Endpoints
---------
GET    /api/voice/map            contract + per-line rows + status
GET    /api/voice/audio          wav/mp3 streaming (whitelisted to output/audio)
PUT    /api/voice/segments       assign lines / mark unclear / release locks
POST   /api/voice/rebuild        regenerate the automatic draft (keeps manual)
POST   /api/voice/propagate      semi-supervised assignment from labelled seeds
PUT    /api/voice/chunks         human speaker override per sub-line
POST   /api/voice/chunks/rebuild auto-assign a speaker per sub-line (CPU only)
POST   /api/voice/chunks/split   split a one-subtitle row into two sub-lines
POST   /api/voice/revoice        delete a line's synthesised wavs
POST   /api/voice/reload         drop the consumer cache
POST   /api/voice/speakers       add a speaker from one original-vocal line
PUT    /api/voice/speakers/ref   replace a speaker's reference clip
DELETE /api/voice/speakers/{name}
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.utils.models import (_AUDIO_REFERS_DIR, _AUDIO_SEGS_DIR,
                              _AUDIO_TMP_DIR, _8_1_AUDIO_TASK)

router = APIRouter(prefix="/api/voice", tags=["voice"])

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO_ROOT = os.path.abspath(os.path.join(PROJECT_ROOT, "output", "audio"))


def _vm():
    from core.tts_backend import voice_map as vm
    return vm


def _edit(fn, *args, **kwargs):
    """Surface domain errors as clean 400s instead of a 500 traceback."""
    try:
        return fn(*args, **kwargs)
    except (KeyError, ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))


def _rel(path: str) -> str | None:
    """Absolute contract paths -> project-relative path for the audio endpoint."""
    if not path:
        return None
    p = os.path.abspath(path)
    if not (p == AUDIO_ROOT or p.startswith(AUDIO_ROOT + os.sep)):
        return None
    return os.path.relpath(p, PROJECT_ROOT).replace("\\", "/")


def _audio_url(path: str) -> str | None:
    rel = _rel(path)
    return f"/api/voice/audio?path={rel}" if rel else None


def _status(vm, data: dict) -> tuple[str, list[str]]:
    """A stale contract is still editable - only synthesis must refuse it."""
    reasons = []
    if data.get("version") != vm.VERSION:
        reasons.append(f"契约版本 {data.get('version')} != {vm.VERSION}")
    if data.get("producer") != vm.PRODUCER_ID:
        reasons.append(f"producer {data.get('producer')} != {vm.PRODUCER_ID}")
    try:
        df = vm._read_task_df()
        if data.get("fingerprint") != vm._fingerprint(df):
            reasons.append("任务表 fingerprint 已变化（换视频或重新切句）")
    except Exception as e:
        reasons.append(f"任务表不可读：{type(e).__name__}")
    return ("stale" if reasons else "ok"), reasons


class SegmentsBody(BaseModel):
    changes: dict[str, str] = {}
    marks: dict[str, bool] = {}
    unassign: list[int] = []


class RebuildBody(BaseModel):
    force: bool = True
    clear_overrides: bool = False


class SeedsBody(BaseModel):
    seeds: dict[str, list[int]]
    protect_manual: bool = False


class ChunkBody(BaseModel):
    # {number: {line_index: speaker}} — only changed sub-lines are sent.
    changes: dict[str, dict[str, str]] = {}


class RebuildChunksBody(BaseModel):
    # None = all merged rows (legacy); a list = only those rows, and
    # single-subtitle rows are re-judged too (v1.26).
    numbers: list[int] | None = None
    force: bool = False


class SplitChunksBody(BaseModel):
    # {number: at_seconds | None}; None = auto-detect the speaker switch.
    changes: dict[str, float | None]


class RevoiceBody(BaseModel):
    numbers: list[int]


class SpeakersBody(BaseModel):
    name: str
    number: int


class RefBody(BaseModel):
    name: str
    number: int


@router.get("/map")
def get_map():
    import pandas as pd

    vm = _vm()
    if not os.path.isfile(vm.VOICE_MAP_FILE):
        return {"status": "missing", "rows": [], "speakers": [],
                "reasons": ["尚未生成 voice_map.json"]}
    try:
        data = vm._read_contract_raw()
    except Exception as e:
        raise HTTPException(400, f"契约不可读：{e}")

    status, reasons = _status(vm, data)
    try:
        df = pd.read_excel(_8_1_AUDIO_TASK)
    except Exception as e:
        raise HTTPException(400, f"配音任务表不可读：{e}")
    # Old contracts (built before FR-34) have no "mixed" flags yet. Mark them
    # in memory only — no rewrite on a GET.
    n_mixed = vm.mark_mixed(data, df)
    windows = vm.subline_windows(df)

    segments = data["segments"]
    meta = data["segment_meta"]
    rows = []
    for r in df.to_dict("records"):
        n = int(r["number"])
        key = str(n)
        m = meta.get(key, {})
        n_chunks = len(windows.get(key, {}).get("lines", []))
        has_ref = os.path.isfile(os.path.join(_AUDIO_REFERS_DIR, f"{n}.wav"))
        has_dub = (os.path.isfile(os.path.join(_AUDIO_TMP_DIR, f"{n}_0_temp.wav"))
                   or os.path.isfile(os.path.join(_AUDIO_SEGS_DIR, f"{n}_0.wav")))
        rows.append({
            "number": n,
            "start_time": str(r.get("start_time", "")),
            "end_time": str(r.get("end_time", "")),
            "duration": float(r.get("duration", 0) or 0),
            "text": str(r.get("text", "")),
            "origin": str(r.get("origin", "")),
            "speaker": segments.get(key),
            "confidence": float(m.get("confidence", 1.0)),
            "flags": list(m.get("flags", [])),
            "manual": bool(m.get("manual", False)),
            "n_chunks": n_chunks,
            "mixed": n_chunks > 1,
            "has_ref": has_ref,
            "has_dub": has_dub,
            "ref_url": f"/api/voice/audio?path=output/audio/refers/{n}.wav" if has_ref else None,
            "dub_url": f"/api/voice/audio?path=output/audio/segs/{n}_0.wav"
                       if os.path.isfile(os.path.join(_AUDIO_SEGS_DIR, f"{n}_0.wav")) else None,
        })

    speakers = []
    for name, info in data["speakers"].items():
        speakers.append({
            "name": name,
            "n_clips": int(info.get("n_clips", 0)),
            "ref_duration": info.get("ref_duration"),
            "ref_source": info.get("ref_source"),
            "confidence": info.get("confidence"),
            "ref_manual": bool(info.get("ref_manual", False)),
            "ref_url": _audio_url(info.get("ref", "")),
            "ref_rel": _rel(info.get("ref", "")),
        })

    counts = {}
    for row in rows:
        counts[row["speaker"]] = counts.get(row["speaker"], 0) + 1
    return {
        "status": status,
        "reasons": reasons,
        "version": data.get("version"),
        "producer": data.get("producer"),
        "expected_producer": vm.PRODUCER_ID,
        "fingerprint": data.get("fingerprint"),
        "created_at": data.get("created_at"),
        "n_speakers": data.get("n_speakers", len(speakers)),
        "silhouette": data.get("silhouette"),
        "degraded": bool(data.get("degraded", False)),
        "diagnostics": data.get("diagnostics", {}),
        "task_rows": len(df),
        "overrides": data["overrides"],
        "seeds": data.get("seeds", {}),
        "counts": counts,
        "speakers": speakers,
        "rows": rows,
        "n_mixed": n_mixed,
        "chunks": vm.chunk_rows(data, windows=windows),
        "chunk_built_at": data.get("chunk_built_at"),
    }


@router.get("/audio")
def get_audio(path: str):
    """Stream one wav/mp3. Whitelisted to output/audio to avoid path traversal."""
    abs_path = os.path.abspath(os.path.join(PROJECT_ROOT, path))
    if not (abs_path.startswith(AUDIO_ROOT + os.sep) and os.path.isfile(abs_path)):
        raise HTTPException(404, "音频不存在或不在 output/audio 下")
    if os.path.splitext(abs_path)[1].lower() not in (".wav", ".mp3"):
        raise HTTPException(400, "只支持 wav / mp3")
    media = "audio/mpeg" if abs_path.endswith(".mp3") else "audio/wav"
    return FileResponse(abs_path, media_type=media)


@router.put("/segments")
def put_segments(body: SegmentsBody):
    vm = _vm()
    data = _edit(vm.apply_overrides, body.changes, body.marks, body.unassign)
    return {"ok": True, "overrides": len(data["overrides"]),
            "changed": len(body.changes), "reloaded": True}


@router.post("/rebuild")
def post_rebuild(body: RebuildBody):
    vm = _vm()
    if body.clear_overrides and os.path.isfile(vm.VOICE_MAP_FILE):
        data = vm._read_contract_raw()
        data["overrides"] = {}
        data["seeds"] = {}
        for m in data["segment_meta"].values():
            m["manual"] = False
        vm._write_contract_raw(data)
    data = _edit(vm.build_voice_map, force=body.force)
    # Seeds are the user's ground truth (e.g. "this is a two-person
    # interview"). Re-apply them so rebuilding the automatic draft never
    # silently reverts a supervised assignment back to a single voice.
    seeds = data.get("seeds") or {}
    restored = None
    if sum(1 for v in seeds.values() if v) >= 2:
        restored = _edit(vm.propagate_from_seeds, seeds)
        data = vm._read_contract_raw()
    try:
        from core.tts_backend.index_tts import reload_voice_map
        reload_voice_map()
    except Exception:
        pass
    return {"ok": True, "producer": data["producer"],
            "n_speakers": data["n_speakers"],
            "degraded": data["degraded"],
            "seed_restored": restored,
            "overrides": len(data["overrides"])}


@router.post("/propagate")
def post_propagate(body: SeedsBody):
    """Label a few certain lines per speaker, then propagate to the whole video."""
    vm = _vm()
    stat = _edit(vm.propagate_from_seeds, body.seeds, body.protect_manual)
    return {"ok": True, "stat": stat}


@router.put("/chunks")
def put_chunks(body: ChunkBody):
    """Human final say on the sub-lines of a merged dubbing task."""
    vm = _vm()
    out = _edit(vm.apply_chunk_overrides, body.changes)
    out["hint"] = ("子行音色已保存并热更新。要生效请对受影响句子点「重配音」"
                   "清掉旧 wav，再重跑第 10 步『生成配音』。")
    return out


@router.post("/chunks/rebuild")
def post_rebuild_chunks(body: RebuildChunksBody | None = None):
    """Auto-assign one speaker per sub-line (CAMPPlus, CPU only, no GPT).

    With ``numbers`` only those rows are re-judged; a single-subtitle row is
    first offered an automatic split so a two-speaker line gets two voices.
    """
    vm = _vm()
    numbers = list(body.numbers) if body and body.numbers else None
    force = bool(body.force) if body else False
    auto = None
    if numbers:
        try:
            windows = vm.subline_windows(vm._read_task_df())
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(400, str(e))
        singles = [n for n in numbers
                   if len(windows.get(str(n), {}).get("lines", [])) == 1]
        if singles:
            auto = _edit(vm.split_rows, {str(n): None for n in singles})
        judged = set(auto.get("split", [])) if auto else set()
        numbers = [n for n in numbers if int(n) not in judged]
    stat = _edit(vm.build_chunk_speakers, force=force, numbers=numbers)
    if auto:
        stat["auto_split"] = len(auto.get("split", []))
        stat["auto_skipped"] = auto.get("skipped", {})
    return {"ok": True, "stat": stat}


@router.post("/chunks/split")
def post_split_chunks(body: SplitChunksBody):
    """Split a one-subtitle dubbing row at a chosen (or detected) time point."""
    vm = _vm()
    out = _edit(vm.split_rows, body.changes)
    judge = out.pop("judge", {}) or {}
    out["chunks"] = int(judge.get("chunks", 0))
    out["hint"] = ("拆分改了配音任务表的子行，请对受影响句子点「重配选中句」"
                   "清掉旧 wav，再到流水线重跑第 10 步『生成配音』。")
    return {"ok": True, "stat": out}


@router.post("/revoice")
def post_revoice(body: RevoiceBody):
    vm = _vm()
    if not body.numbers:
        raise HTTPException(400, "没有选中任何句子")
    out = _edit(vm.clear_dubbed, body.numbers)
    out["hint"] = ("已删除这些句子的合成音频。到「流水线」页重跑第 10 步"
                   "『生成配音』即可只重配它们，其余句子会秒跳过。")
    return out


@router.post("/reload")
def post_reload():
    try:
        from core.tts_backend.index_tts import reload_voice_map
        reload_voice_map()
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/speakers")
def post_speaker(body: SpeakersBody):
    vm = _vm()
    data = _edit(vm.add_speaker, body.name, body.number)
    return {"ok": True, "n_speakers": data["n_speakers"]}


@router.put("/speakers/ref")
def put_speaker_ref(body: RefBody):
    vm = _vm()
    _edit(vm.set_speaker_ref, body.name, body.number)
    return {"ok": True}


@router.delete("/speakers/{name}")
def delete_speaker(name: str):
    vm = _vm()
    data = _edit(vm.remove_speaker, name)
    return {"ok": True, "n_speakers": data["n_speakers"]}
