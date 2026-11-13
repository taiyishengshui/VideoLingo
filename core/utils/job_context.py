"""Job workspace + job_state.json — per-video state, shared tool (not a step).

Batch model (agreed with user, 2026-09-08):
- The user picks a ROOT directory (videos may sit in subdirectories).
- A mirror workspace is created NEXT TO the root:  <root>_log/
- Inside, the directory structure mirrors the root:
      root/a1/001.mp4   ->  <root>_log/a1/001/          (this video's workspace)
- Everything a video produces lives under its workspace; the pipeline runs
  with cwd = the workspace, so every legacy `output/...` relative path in
  core/ lands inside it with ZERO code changes.
- Deliverable: trans_src.srt (中文在上) is copied next to the video as
  <video_stem>_trans_src.srt when all steps are done.

job_state.json (one per video, lives IN the workspace):
- Atomic writes (tmp + rename). Kill/power-off safe.
- Input fingerprint (size + mtime): if the source video changed, the job
  restarts from scratch.
- Per-step status: pending | running | done | failed, plus error text.
- Step-range rerun: reset_steps(m, n) marks steps m..n (and everything
  after n, unless told otherwise) as pending; earlier steps stay done and
  their artifacts are reused (e.g. re-translate only: reset 4..end).

Design notes:
- This module must stay import-light (no torch/pandas/etc): it is imported
  by the batch engine AND by core.pipeline (both server-side).
- Paths are stored absolute and forward-slashed on Windows for JSON sanity.
"""

from __future__ import annotations

import json
import os
import shutil
import time

STATE_FILENAME = "job_state.json"
DELIVER_NAME = "trans_src.srt"          # inside workspace output/
LOG_SUFFIX = "_log"                     # root_log, a_log, ...

# Per-step status values
PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"


def _norm(path: str) -> str:
    """Absolute, forward-slashed path (stable across JSON + Windows)."""
    return os.path.abspath(path).replace("\\", "/")


def sanitize_dir_name(name: str) -> str:
    """Make a video stem safe as a single directory component."""
    cleaned = "".join(c if c not in '<>:"/\\|?*' else "_" for c in name)
    cleaned = cleaned.strip(". ")
    return cleaned[:80] or "video"


def workspace_for(root: str, video_path: str) -> str:
    """root/a1/001.mp4 -> <root>_log/a1/001/  (absolute, forward slashes)."""
    root = _norm(root)
    video_path = _norm(video_path)
    rel = os.path.relpath(video_path, root)
    rel_dir, filename = os.path.split(rel)
    stem = sanitize_dir_name(os.path.splitext(filename)[0])
    parts = [p for p in rel_dir.replace("\\", "/").split("/") if p]
    return "/".join([root.rstrip("/") + LOG_SUFFIX] + parts + [stem])


def batch_log_root(root: str) -> str:
    """The batch workspace root: <root>_log/"""
    return _norm(root).rstrip("/") + LOG_SUFFIX


def scan_videos(root: str, video_exts=None, audio_exts=None) -> list:
    """Recursively find media files under root, skipping any *_log dirs.

    Returns a list of absolute forward-slashed paths. Videos come first,
    audio-only files after (they are valid pipeline inputs too)."""
    root = _norm(root)
    if video_exts is None or audio_exts is None:
        try:
            from core.utils.config_utils import load_key
            video_exts = video_exts or load_key("allowed_video_formats")
            audio_exts = audio_exts or load_key("allowed_audio_formats")
        except Exception:
            video_exts = video_exts or ["mp4", "mkv", "mov", "avi", "webm", "flv", "m4v"]
            audio_exts = audio_exts or ["mp3", "wav", "m4a", "aac", "flac", "ogg"]

    def _skip(name: str) -> bool:
        return name.endswith(LOG_SUFFIX) or not name

    videos, audios = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip(d)]
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1][1:].lower()
            full = _norm(os.path.join(dirpath, fn))
            if ext in video_exts and not fn.startswith("."):
                videos.append(full)
            elif ext in audio_exts and not fn.startswith("."):
                audios.append(full)
    return videos + audios


def is_delivered(video_path: str) -> bool:
    """True when the deliverable subtitle already sits next to the video."""
    stem = os.path.splitext(os.path.basename(video_path))[0]
    return os.path.exists(
        os.path.join(os.path.dirname(video_path), f"{stem}_trans_src.srt")
    )


def delete_step_artifacts(sid: str, workspace: str) -> None:
    """Remove the artifact files that prove `sid` done + its LLM caches
    + its step-local checkpoints (`clean_extra`).

    Shared by the batch engine (step-range rerun, workspace = job dir) and
    the single-task server flow (force rerun, workspace = project root /
    cwd). Without this, steps guard themselves with @check_file_exists and
    silently skip on a rerun.

    Also removes the step's LLM response caches (output/gpt_log/<title>
    .json for each llm_title): ask_gpt caches are keyed by prompt only
    (model-agnostic), so a rerun with a DIFFERENT model/config would
    silently replay the old cached responses and appear to do nothing."""
    try:
        from core.pipeline import get_step
        step = get_step(sid)
        # markers（完成判据）+ clean_extra（断点/缓存，不算判据但重跑要清）
        for marker in tuple(step.markers) + tuple(getattr(step, "clean_extra", ())):
            path = os.path.join(workspace, marker)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.exists(path):
                os.remove(path)
        for title in step.llm_titles:
            cache = os.path.join(workspace, "output", "gpt_log",
                                 f"{title}.json")
            if os.path.exists(cache):
                os.remove(cache)
    except Exception:
        pass  # pipeline registry unavailable (shouldn't happen server-side)


class JobState:
    """Reader/writer for one video's job_state.json (workspace-local)."""

    def __init__(self, workspace: str):
        self.workspace = _norm(workspace)
        self.path = os.path.join(self.workspace, STATE_FILENAME)
        self._data = None

    # ---------- load / save ----------

    def load(self) -> dict:
        if self._data is not None:
            return self._data
        default = {"version": 1, "input": {}, "steps": {}, "deliver": None, "error": None}
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                    self._data.setdefault("steps", {})
                    self._data.setdefault("input", {})
                    self._data.setdefault("deliver", None)
                    self._data.setdefault("error", None)
                    return self._data
            except (json.JSONDecodeError, OSError):
                pass  # corrupt state = treat as fresh (resume via artifacts)
        self._data = default
        return self._data

    def save(self) -> None:
        """Atomic write: tmp + rename. Safe against power-off."""
        os.makedirs(self.workspace, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ---------- input fingerprint ----------

    def input_fingerprint(self, video_path: str) -> dict:
        st = os.stat(video_path)
        return {
            "path": _norm(video_path),
            "size": st.st_size,
            "mtime": int(st.st_mtime),
        }

    def check_input_changed(self, video_path: str) -> bool:
        """True when the recorded fingerprint no longer matches the file
        (never processed, or the file was replaced). Caller should restart
        from scratch."""
        recorded = self.load().get("input", {}).get("fingerprint")
        return recorded != self.input_fingerprint(video_path)

    def record_input(self, video_path: str, only_if_missing: bool = False) -> None:
        d = self.load()
        if only_if_missing and d.get("input", {}).get("fingerprint"):
            return
        d["input"] = {"fingerprint": self.input_fingerprint(video_path),
                      "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.save()

    # ---------- step states ----------

    def init_steps(self, step_ids: list) -> None:
        """Ensure every step has a state; existing states are preserved."""
        d = self.load()
        for sid in step_ids:
            d["steps"].setdefault(sid, {"status": PENDING})
        self.save()

    def set_step(self, sid: str, status: str, error: str | None = None) -> None:
        d = self.load()
        entry = d["steps"].setdefault(sid, {})
        entry["status"] = status
        entry["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if error is not None:
            entry["error"] = error[:2000]
        elif status != FAILED:
            entry.pop("error", None)
        if status == DONE:
            entry["finished"] = entry["updated"]
        self.save()

    def step_status(self, sid: str) -> str:
        entry = self.load()["steps"].get(sid)
        return entry["status"] if entry else PENDING

    def next_step(self, step_ids: list) -> str | None:
        """First non-done step in registry order (None = all done)."""
        for sid in step_ids:
            if self.step_status(sid) != DONE:
                return sid
        return None

    def reset_steps(self, start: str, end: str | None, step_ids: list) -> None:
        """Mark steps from `start` to `end` (inclusive, registry order) as
        pending. end=None means 'to the last step'. Steps before `start`
        keep their done state and artifacts get reused.

        The reset steps' marker artifacts are DELETED: several steps guard
        themselves with @check_file_exists(marker) and would silently skip
        if a stale artifact is present."""
        ids = step_ids
        i = ids.index(start)
        j = ids.index(end) if end else len(ids) - 1
        d = self.load()
        for sid in ids[i:j + 1]:
            entry = d["steps"].setdefault(sid, {})
            entry["status"] = PENDING
            entry["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            entry.pop("error", None)
            self._delete_step_markers(sid)
        # a rerun invalidates the delivery record
        d["deliver"] = None
        d["error"] = None
        self.save()

    def _delete_step_markers(self, sid: str) -> None:
        delete_step_artifacts(sid, self.workspace)

    def reset_all(self, step_ids: list) -> None:
        self.reset_steps(step_ids[0], None, step_ids)

    # ---------- summary for the API ----------

    def summary(self, step_ids: list) -> dict:
        d = self.load()
        steps = []
        for sid in step_ids:
            entry = d["steps"].get(sid, {})
            steps.append({
                "id": sid,
                "status": entry.get("status", PENDING),
                "error": entry.get("error"),
            })
        return {
            "workspace": self.workspace,
            "input": d.get("input", {}),
            "steps": steps,
            "deliver": d.get("deliver"),
            "error": d.get("error"),
        }

    # ---------- delivery ----------

    def mark_delivered(self, video_path: str, dest: str) -> None:
        d = self.load()
        d["deliver"] = {
            "dest": _norm(dest),
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.save()


class Job:
    """One video + its workspace + its state. Used by the batch engine."""

    def __init__(self, root: str, video_path: str):
        self.video_path = _norm(video_path)
        self.root = _norm(root)
        self.workspace = workspace_for(self.root, self.video_path)
        self.state = JobState(self.workspace)

    def prepare(self, step_ids: list, force_restart: bool = False) -> dict:
        """Create the workspace, write the input manifest the pipeline
        expects (output/input_manifest.json pointing at the source video),
        and initialize step states.

        Returns a summary dict. When the input fingerprint changed, the
        job is fully reset (old artifacts are stale for a different file)."""
        os.makedirs(self.workspace, exist_ok=True)

        if force_restart or self.state.check_input_changed(self.video_path):
            # wipe artifacts but keep the directory
            for name in os.listdir(self.workspace):
                p = os.path.join(self.workspace, name)
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)
            self.state._data = None  # force fresh load
            self.state.record_input(self.video_path)
            self.state.init_steps(step_ids)
            action = "fresh" if force_restart else "input-changed"
        else:
            self.state.record_input(self.video_path, only_if_missing=True)
            self.state.init_steps(step_ids)
            action = "resume"

        self._write_input_manifest()
        return {"action": action, "workspace": self.workspace}

    def _write_input_manifest(self):
        """The pipeline's find_media_file() reads output/input_manifest.json
        relative to cwd. cwd = workspace, so write it there."""
        manifest_dir = os.path.join(self.workspace, "output")
        os.makedirs(manifest_dir, exist_ok=True)
        manifest = {
            "path": self.video_path,
            "type": "video" if self._is_video() else "audio",
        }
        with open(os.path.join(manifest_dir, "input_manifest.json"), "w",
                  encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    def _is_video(self) -> bool:
        try:
            from core.utils.config_utils import load_key
            exts = load_key("allowed_video_formats")
        except Exception:
            exts = ["mp4", "mkv", "mov", "avi", "webm", "flv", "m4v"]
        ext = os.path.splitext(self.video_path)[1][1:].lower()
        return ext in exts

    def deliver(self) -> str | None:
        """Copy the bilingual subtitle (trans_src.srt) next to the video.
        Returns the destination path, or None when there is nothing to
        deliver yet."""
        src = os.path.join(self.workspace, "output", DELIVER_NAME)
        if not os.path.exists(src):
            return None
        stem = os.path.splitext(os.path.basename(self.video_path))[0]
        dest = os.path.join(os.path.dirname(self.video_path),
                            f"{stem}_trans_src.srt")
        shutil.copy2(src, dest)
        self.state.mark_delivered(self.video_path, dest)
        return dest
