"""Safe read/write operations for the two editable SRT tracks."""

from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile

from core.services.subtitle_source_service import format_srt_time

_ALLOWED = {"src.srt", "trans.srt"}
_ONE_TIME = r"(\d{2,}):(\d{2}):(\d{2}),(\d{3})"
_TIME = re.compile(rf"^{_ONE_TIME} --> {_ONE_TIME}$")
# Step 6 writes one extra empty line per block (single-column tracks), so the
# produced SRTs separate blocks with three CRLFs.  Align with the existing
# ``subtitle_source_service.parse_srt_blocks`` tolerance for consecutive blank
# lines instead of assuming exactly one blank line.
_BLOCK_SEPARATOR = re.compile(r"\n\s*\n")
_BLANK_LINE_IN_TEXT = re.compile(r"\n[ \t]*\n")


def _inside(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(parent)]) == os.path.realpath(parent)
    except (ValueError, OSError):
        return False


def subtitle_path(workspace: str, name: str) -> str:
    if name not in _ALLOWED:
        raise ValueError("only src.srt and trans.srt can be edited")
    root = os.path.abspath(workspace)
    out = os.path.join(root, "output")
    if os.path.islink(out) or not _inside(out, root):
        raise ValueError("workspace output directory is unsafe")
    path = os.path.join(out, name)
    if os.path.islink(path) or not _inside(path, out):
        raise ValueError("subtitle path is unsafe")
    return path


def _parse_time(value: str) -> float:
    match = re.fullmatch(_ONE_TIME, value.strip())
    if not match:
        raise ValueError(f"invalid SRT timecode: {value}")
    h, m, s, ms = map(int, match.groups())
    if m > 59 or s > 59:
        raise ValueError(f"invalid SRT timecode: {value}")
    return h * 3600 + m * 60 + s + ms / 1000


def parse_srt(text: str, duration: float | None = None) -> list[dict]:
    normalized = (text.replace("\r\n", "\n").replace("\r", "\n")
                  .strip().lstrip("\ufeff").strip())
    if not normalized:
        return []
    rows = []
    for block_no, chunk in enumerate(_BLOCK_SEPARATOR.split(normalized), 1):
        lines = chunk.split("\n")
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise ValueError(f"invalid SRT block structure in block {block_no}")
        pair = lines[1].split(" --> ")
        if len(pair) != 2:
            raise ValueError(f"invalid SRT time range in block {block_no}")
        try:
            start, end = _parse_time(pair[0]), _parse_time(pair[1])
        except ValueError as exc:
            raise ValueError(f"invalid SRT timecode in block {block_no}: {exc}") from exc
        body = "\n".join(lines[2:]).strip()
        if start < 0 or end <= start or not body:
            raise ValueError(
                f"each subtitle needs text and a positive time range (block {block_no})")
        if duration is not None and end > duration + 0.05:
            raise ValueError(f"subtitle ends after the source media duration (block {block_no})")
        rows.append({"start": start, "end": end, "text": body})
    return rows


def serialize_srt(rows: list[dict], duration: float | None = None) -> str:
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    chunks = []
    for index, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict) or not {"start", "end", "text"}.issubset(row):
                raise TypeError("row must contain start, end and text")
            start, end = float(row["start"]), float(row["end"])
            text = str(row["text"]).replace("\r\n", "\n").replace("\r", "\n").strip()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid subtitle row {index}") from exc
        if (not math.isfinite(start) or not math.isfinite(end) or start < 0
                or end <= start or not text):
            raise ValueError(f"invalid time or text in subtitle row {index}")
        if _BLANK_LINE_IN_TEXT.search(text):
            raise ValueError(f"blank line inside subtitle text in row {index}")
        if duration is not None and end > duration + 0.05:
            raise ValueError(f"subtitle row {index} ends after source media duration")
        chunks.append(f"{index}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}")
    return "\n\n".join(chunks) + "\n" if chunks else ""


def revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_track(workspace: str, name: str, duration: float | None = None) -> dict:
    path = subtitle_path(workspace, name)
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        text = handle.read()
    rows = parse_srt(text, duration)
    return {"name": name, "revision": revision(text), "rows": rows}


def save_track(workspace: str, name: str, expected_revision: str,
               rows: list[dict], duration: float | None = None) -> dict:
    path = subtitle_path(workspace, name)
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        current = handle.read()
    current_revision = revision(current)
    if current_revision != expected_revision:
        raise RuntimeError("subtitle file changed outside the editor; reload before saving")
    text = serialize_srt(rows, duration)
    directory = os.path.dirname(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.islink(path):
            raise ValueError("subtitle path is unsafe")
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            if revision(handle.read()) != expected_revision:
                raise RuntimeError("subtitle file changed while saving; reload before saving")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"name": name, "revision": revision(text), "rows": parse_srt(text, duration)}
