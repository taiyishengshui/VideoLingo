"""Single decision point for which subtitles each video version burns.

Step 7 (subtitled) used to hard-code ``src.srt`` + ``trans.srt`` (bilingual,
source ASR axis) while step 12 (dubbed) hard-coded only ``dub.srt`` (Chinese,
dubbing axis). Side by side the two versions showed different text at 19.6% of
sampled subtitle times (FR-36 / v1.38).

This module owns that decision. It deliberately does NOT unify the two time
axes: the subtitled version stays on the original ASR axis and the dubbed
version stays on ``new_sub_times`` — merging them would desync the dubbing
audio and force a full 483-segment atempo re-run. What is unified is the
subtitle *set and text*: ``subtitle.source=unified`` adds an English original
line to the dubbed version by pairing ``src.srt`` text with ``dub.srt``
windows by index.
"""
from __future__ import annotations

import os
import re
from typing import Literal

LEGACY = "legacy"
UNIFIED = "unified"
Role = Literal["src", "trans"]

_TIMECODE = re.compile(
    r"^\s*(\d{1,2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2}),(\d{3})\s*$"
)


def _join(workspace: str, name: str) -> str:
    """Forward slashes only: ffmpeg's subtitles= treats a backslash as escape."""
    return workspace.replace("\\", "/").rstrip("/") + "/" + name


def subtitle_source() -> str:
    """Read ``subtitle.source``; a missing key or bad value falls back to legacy.

    ``load_key`` raises KeyError when the key is absent (config.yaml is
    gitignored, so a fresh clone will not have it) — never let that break a
    pipeline step.
    """
    from core.utils.config_utils import load_key
    try:
        value = str(load_key("subtitle.source")).strip().lower()
    except (KeyError, OSError, TypeError, ValueError):
        return LEGACY
    return value if value in (LEGACY, UNIFIED) else LEGACY


def parse_srt_blocks(path: str) -> list[tuple[float, float, str]]:
    """Parse SRT into ``[(start_sec, end_sec, text)]`` — stdlib only, strict.

    A malformed timecode raises ValueError instead of being skipped, so a
    broken upstream file can never silently produce a shifted subtitle track.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    blocks: list[tuple[float, float, str]] = []
    for chunk in re.split(r"\n\s*\n", raw.strip()):
        lines = chunk.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        m = _TIMECODE.match(lines[1])
        if not m:
            raise ValueError(f"bad SRT timecode in {path}: {lines[1]!r}")
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x) for x in m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
        blocks.append((start, end, "\n".join(lines[2:]).strip()))
    return blocks


def format_srt_time(seconds: float) -> str:
    """Millisecond-stable SRT timecode (round, not truncate)."""
    total_ms = int(round(float(seconds) * 1000))
    h, total_ms = divmod(total_ms, 3_600_000)
    m, total_ms = divmod(total_ms, 60_000)
    s, total_ms = divmod(total_ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{total_ms:03d}"


def build_dub_src_srt(workspace: str = "output") -> str:
    """Pair ``src.srt`` text with ``dub.srt`` windows by index -> dub_src.srt.

    The dubbing audio is aligned to ``dub.srt``'s ``new_sub_times`` axis, so
    the English line must reuse those exact windows. No LLM / GPU / audio work.
    """
    src_path = _join(workspace, "src.srt")
    dub_path = _join(workspace, "dub.srt")
    out_path = _join(workspace, "dub_src.srt")
    for p in (src_path, dub_path):
        if not os.path.isfile(p):
            raise FileNotFoundError(f"subtitle source missing: {p}")
    src_blocks = parse_srt_blocks(src_path)
    dub_blocks = parse_srt_blocks(dub_path)
    if not src_blocks or not dub_blocks:
        raise ValueError(f"empty subtitle file: {src_path} or {dub_path}")
    if len(src_blocks) != len(dub_blocks):
        raise ValueError(
            f"subtitle count mismatch: {src_path}={len(src_blocks)} vs "
            f"{dub_path}={len(dub_blocks)}; refusing to pair by index"
        )
    parts = []
    for i, ((start, end, _), (_, _, text)) in enumerate(zip(dub_blocks, src_blocks), 1):
        parts.append(
            f"{i}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}\n\n"
        )
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    os.replace(tmp, out_path)
    return out_path


def subtitle_plan(version: str, workspace: str = "output") -> list[tuple[str, Role]]:
    """THE single decision: ordered ``[(srt_path, role)]`` for one version.

    ``legacy`` reproduces the old step-12 filter byte-for-byte (one Chinese
    line). ``unified`` prepends an English original line on the dubbing axis.
    """
    if version not in ("sub", "dub"):
        raise ValueError(f"unknown subtitle version: {version!r}")
    if version == "sub":
        return [(_join(workspace, "src.srt"), "src"),
                (_join(workspace, "trans.srt"), "trans")]
    if subtitle_source() != UNIFIED:
        return [(_join(workspace, "dub.srt"), "trans")]
    return [(build_dub_src_srt(workspace), "src"),
            (_join(workspace, "dub.srt"), "trans")]
