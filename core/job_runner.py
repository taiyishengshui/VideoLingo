"""Per-job pipeline runner — the subprocess entry point for batch jobs.

Runs INSIDE the job workspace (cwd = workspace, set by core/batch_engine.py).
All legacy `output/...` relative paths in the step files then resolve inside
the workspace automatically.

Protocol with the parent (batch engine):
- stdout line `{"vl_step": "<id>", "status": "running"|"done"|"failed"}` is
  parsed by the engine as a step transition; everything else is forwarded
  as a plain log line.
- Prints are flushed immediately (PYTHONUNBUFFERED=1).
- Exit code 0 = every requested step finished; non-zero = the failing step
  is recorded in job_state.json by the engine.

The input manifest (output/input_manifest.json) is written by Job.prepare()
BEFORE this process starts, so find_media_file() finds the source video on
the first call.
"""

from __future__ import annotations

import json
import sys
import traceback


def _emit(sid: str, status: str, error: str | None = None):
    payload = {"vl_step": sid, "status": status}
    if error:
        payload["error"] = error
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def main(argv: list) -> int:
    if len(argv) < 2:
        print("usage: python -m core.job_runner <step_id> [step_id ...]",
              file=sys.stderr)
        return 2

    from core.pipeline import get_step

    for sid in argv[1:]:
        step = get_step(sid)
        _emit(sid, "running")
        try:
            step.run()
        except Exception as e:
            _emit(sid, "failed", f"{e}")
            traceback.print_exc()
            return 1
        _emit(sid, "done")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
