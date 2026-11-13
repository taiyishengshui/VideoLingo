"""Batch engine: run the registered pipeline over many videos.

Architecture (agreed with user, 2026-09-08):
- The user picks a root directory; scan_videos() finds every media file
  (skipping *_log dirs); each video gets a mirrored workspace under
  <root>_log/ (see core/utils/job_context.py).
- Each job runs as a SEPARATE PYTHON SUBPROCESS with cwd = its workspace.
  All legacy `output/...` relative paths in core/ then land inside the
  workspace with zero changes to the 30+ step files.
- The subprocess runs `python -m core.job_runner <step_ids...>`, which
  executes registry steps in order inside that workspace.
- Progress: the runner writes job_state.json transitions; this engine also
  tails the subprocess stdout line-by-line and republishes to the LogBus
  so the UI sees live logs per file.
- Resume: rerunning a batch only processes files whose next_step() is not
  None; completed files are skipped.
- Deliverable: when all steps are done, trans_src.srt is copied next to
  the video as <stem>_trans_src.srt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

from core.utils.job_context import (
    DONE, FAILED, RUNNING, Job, batch_log_root, is_delivered, scan_videos,
)


class BatchEngine:
    """Sequential batch runner. One file at a time (Phase 1); lane-based
    parallelism is Phase 2 and will live on top of this state machine."""

    def __init__(self, publish=None):
        self._publish = publish or (lambda event: None)
        self._lock = threading.Lock()
        self.state = "idle"  # idle | running | stopping | error | completed
        self.root: str | None = None
        self.jobs: list[Job] = []
        self.current_job: Job | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None

    # ---------- introspection ----------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "state": self.state,
                "root": self.root,
                "files": [
                    {
                        "video": j.video_path,
                        "workspace": j.workspace,
                        "summary": j.state.summary(self._step_ids()),
                    }
                    for j in self.jobs
                ],
                "current": self.current_job.video_path if self.current_job else None,
            }

    # ---------- control ----------

    def scan(self, root: str) -> list:
        """Scan a root directory; returns the per-file summary for the UI."""
        videos = scan_videos(root)
        jobs = [Job(root, v) for v in videos]
        with self._lock:
            self.root = root
            self.jobs = jobs
        for j in jobs:
            j.state.init_steps(self._step_ids())
        return [self._file_row(j) for j in jobs]

    def start(self, selected: list, force_restart: bool = False,
              reset_from: str | None = None, reset_to: str | None = None) -> tuple[bool, str]:
        """Run the batch over `selected` (list of video paths).

        force_restart: wipe each workspace and start from step 1.
        reset_from/reset_to: before running, mark steps [from..to] pending
        (step-range rerun; artifacts of earlier steps are reused)."""
        with self._lock:
            if self.state in ("running", "stopping"):
                return False, "batch already running"
            by_path = {j.video_path: j for j in self.jobs}
            picked = [by_path[p] for p in selected if p in by_path]
            if not picked:
                return False, "no selected files match the scan"
            self.state = "running"
            self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(picked, force_restart, reset_from, reset_to),
            daemon=True)
        self._thread.start()
        return True, f"batch started: {len(picked)} file(s)"

    def stop(self):
        self._stop.set()
        # kill the running job subprocess so the worker unblocks immediately;
        # a graceful flag alone would let a 30-min transcription finish first.
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    # ---------- worker ----------

    def _step_ids(self) -> list:
        from core.pipeline import get_steps
        return [s.id for s in get_steps()]

    def _run(self, jobs: list, force_restart: bool,
             reset_from: str | None, reset_to: str | None):
        step_ids = self._step_ids()
        failures = []
        try:
            self._publish({"type": "batch_state", "state": "running",
                           "total": len(jobs)})
            for job in jobs:
                if self._stop.is_set():
                    break
                try:
                    self._process_one(job, step_ids, force_restart,
                                      reset_from, reset_to)
                except StopIteration:
                    break  # user stopped mid-job; not a failure
                except Exception as e:
                    failures.append((job.video_path, str(e)))
                    failed_sid = job.state.next_step(step_ids)
                    if failed_sid:
                        job.state.set_step(failed_sid, FAILED, error=str(e))
                    self._publish({"type": "job_error",
                                   "video": job.video_path, "error": str(e)})
        finally:
            with self._lock:
                self.current_job = None
                if self._stop.is_set():
                    self.state = "stopped"
                elif failures:
                    self.state = "error"
                else:
                    self.state = "completed"
            self._publish({"type": "batch_state", "state": self.state,
                           "failures": [(v, e) for v, e in failures]})

    def _process_one(self, job: Job, step_ids: list, force_restart: bool,
                     reset_from: str | None, reset_to: str | None):
        with self._lock:
            self.current_job = job

        prep = job.prepare(step_ids, force_restart=force_restart)
        if reset_from:
            job.state.reset_steps(reset_from, reset_to, step_ids)

        self._publish({"type": "job_start", "video": job.video_path,
                       "workspace": job.workspace, "action": prep["action"]})

        # Which steps still need running?
        pending = [sid for sid in step_ids
                   if job.state.step_status(sid) != DONE]
        if not pending:
            dest = job.deliver()
            self._publish({"type": "job_done", "video": job.video_path,
                           "deliver": dest})
            return

        # Spawn the runner subprocess in the job workspace.
        cmd = [sys.executable, "-m", "core.job_runner", *pending]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env.setdefault("PYTHONUNBUFFERED", "1")
        # keep the project importable when cwd is the workspace
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")

        self._publish({"type": "job_steps", "video": job.video_path,
                       "steps": pending})
        proc = subprocess.Popen(
            cmd, cwd=job.workspace, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        with self._lock:
            self._proc = proc

        marker = f"[vljob:{os.path.basename(job.workspace)}]"
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            # the runner emits step transitions as JSON one-liners
            if line.startswith('{"vl_step"'):
                try:
                    evt = json.loads(line)
                    sid, status = evt["vl_step"], evt.get("status", "")
                    job.state.set_step(sid, status, evt.get("error"))
                    self._publish({"type": "job_step", "video": job.video_path,
                                   "step": sid, "status": status,
                                   "error": evt.get("error")})
                    continue
                except (json.JSONDecodeError, KeyError):
                    pass  # fall through: treat as a log line
            self._publish({"type": "log", "video": job.video_path,
                           "step": job.state.next_step(step_ids), "line": line,
                           "marker": marker})

        proc.wait()
        with self._lock:
            self._proc = None

        if self._stop.is_set():
            # user-stopped: the killed step stays non-done so a later run
            # resumes exactly there; do NOT mark anything done
            raise StopIteration()

        if proc.returncode != 0:
            err = f"runner exited with code {proc.returncode}"
            sid = job.state.next_step(step_ids)
            if sid:
                job.state.set_step(sid, FAILED, error=err)
            raise RuntimeError(err)

        # mark remaining pending steps done (runner finished cleanly; the
        # runner itself already emitted done events, this is a safety net)
        for sid in pending:
            if job.state.step_status(sid) != DONE:
                job.state.set_step(sid, DONE)

        dest = job.deliver()
        self._publish({"type": "job_done", "video": job.video_path,
                       "deliver": dest})

    def _file_row(self, job: Job) -> dict:
        step_ids = self._step_ids()
        summary = job.state.summary(step_ids)
        statuses = {s["id"]: s["status"] for s in summary["steps"]}

        done_count = sum(1 for s in statuses.values() if s == DONE)
        failed = [s for s, v in statuses.items() if v == FAILED]
        delivered = summary.get("deliver") is not None or is_delivered(job.video_path)
        if delivered and done_count == len(step_ids):
            overall = "delivered"
        elif failed:
            overall = "failed"
        elif done_count == len(step_ids):
            overall = "done"           # all steps done but no deliverable yet
        elif done_count > 0 or any(v == RUNNING for v in statuses.values()):
            overall = "partial"
        else:
            overall = "pending"
        return {
            "video": job.video_path,
            "workspace": job.workspace,
            "root": job.root,
            "overall": overall,
            "done_count": done_count,
            "total": len(step_ids),
            "steps": summary["steps"],
            "error": summary.get("error"),
        }


ENGINE = BatchEngine()
