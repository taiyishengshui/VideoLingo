"""Run service — the ONLY home for "which steps + what to wipe" decisions.

Single source of truth for run-request semantics, shared by every entry:
- server/app.py  (single-task pipeline page)
- batch engine   (already routes through job_state; uses resolve+force here)

The two historical bugs this module fixes:
1. single-task /run had no artifact cleanup -> guarded steps silently
   skipped on "rerun" (fixed ad-hoc in app.py; now this is THE code)
2. each entry hand-derived its step selection -> drifted apart

Step selection rules (one place):
- step_id given          -> exactly that step (from/to are ignored)
- from_step / to_step    -> inclusive slice in registry order
- neither                -> all steps
- force=True             -> delete the selected steps' marker artifacts +
                           LLM caches first, so @check_file_exists guards
                           actually rerun them. Semantics: a step-range run
                           is an EXPECTED rerun of those steps.

Default range rules (also here, so UI/CLI/batch cannot disagree):
- start: always `transcribe` (stage 1 begins at ASR)
- end:   `merge_sub` when burn_subtitles is on, otherwise `gen_sub` —
         with burn off, step 7 only writes a 0-second placeholder video,
         so "生成字幕" (src.srt / trans.srt, step 6) is the real end of the job.
"""

from __future__ import annotations

DEFAULT_START_STEP = "transcribe"
BURN_END_STEP = "merge_sub"        # 合并字幕到视频（真烧录）
NO_BURN_END_STEP = "gen_sub"       # 第 6 步「生成字幕」（产出 srt），不烧录时到此为止


def burn_subtitles_enabled() -> bool:
    """Read the switch fresh (load_key opens config.yaml on every call, so a
    manual edit / settings-page change takes effect without a restart)."""
    try:
        from core.utils.config_utils import load_key
        return bool(load_key("burn_subtitles"))
    except Exception:
        # config missing the key (tests / fresh install) -> project default
        return True


def _ids(audio_only: bool = False) -> list[str]:
    from core.pipeline import get_steps
    return [s.id for s in get_steps(audio_only=audio_only)]


def clamp_end_index(to_step: str, ids: list[str], full_ids: list[str]) -> int:
    """Index of `to_step` inside a possibly filtered `ids` list.

    Video-only steps (merge_sub / dub_to_vid) disappear for audio-only input.
    When the requested end step is filtered out, fall back to the LAST
    available step that still precedes it in registry order — never extend
    past what the user asked for.
    """
    if to_step in ids:
        return ids.index(to_step)
    limit = full_ids.index(to_step)
    allowed = [i for i, sid in enumerate(ids) if full_ids.index(sid) <= limit]
    return allowed[-1] if allowed else 0


def default_run_range(audio_only: bool = False) -> dict:
    """Defaults for the UI's two step selectors. See module docstring."""
    full_ids = _ids()
    ids = _ids(audio_only=audio_only)
    to_step = BURN_END_STEP if burn_subtitles_enabled() else NO_BURN_END_STEP
    if to_step not in ids:
        to_step = ids[clamp_end_index(to_step, ids, full_ids)]
    start = DEFAULT_START_STEP if DEFAULT_START_STEP in ids else ids[0]
    return {"from_step": start, "to_step": to_step}


def resolve_steps(step_id: str | None, from_step: str | None,
                  audio_only: bool = False,
                  to_step: str | None = None) -> list[str]:
    """Which steps does this run request mean? Inclusive range, validated.

    `to_step` is intentionally the LAST positional parameter: older callers
    passing `audio_only` positionally keep working.
    """
    full_ids = _ids()
    ids = _ids(audio_only=audio_only)
    if step_id:
        if step_id not in ids:
            raise KeyError(f"unknown step: {step_id}")
        return [step_id]
    if from_step and from_step not in ids:
        raise KeyError(f"unknown step: {from_step}")
    if to_step and to_step not in full_ids:
        raise KeyError(f"unknown step: {to_step}")

    start = ids.index(from_step) if from_step else 0
    end = clamp_end_index(to_step, ids, full_ids) if to_step else len(ids) - 1
    if start > end:
        raise ValueError(
            f"起始步骤「{ids[start]}」在结束步骤「{ids[end]}」之后，请重新选择")
    return ids[start:end + 1]


def force_clean(step_ids: list[str], workspace: str) -> list[str]:
    """Wipe the selected steps' markers + LLM caches in `workspace`.

    Returns the ids actually cleaned. This is THE implementation behind
    "强制重跑" everywhere — app.py and the batch engine both call it; the
    artifact/marker list itself lives in core.pipeline (per-step `markers`
    + `llm_titles`), consumed via job_context.delete_step_artifacts."""
    from core.utils.job_context import delete_step_artifacts
    for sid in step_ids:
        delete_step_artifacts(sid, workspace)
    return list(step_ids)
