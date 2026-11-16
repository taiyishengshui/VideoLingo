"""numpy>=2 scalar repr leaking into tts_tasks.xlsx (step 10 -> step 11).

Regression for: ``NameError: name 'np' is not defined`` in
``core/_11_merge_audio.py:load_and_flatten_data``. See PRD FR-34 / v1.27.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pytest

from core.services.dub_timeline_service import parse_excel_list, plain_time_pairs


def test_speed_executor_cancels_queued_jobs_and_waits_for_running_job(monkeypatch):
    source = pathlib.Path("core/_10_gen_audio.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_shutdown_speed_executor"
    )
    namespace = {"ThreadPoolExecutor": ThreadPoolExecutor}
    exec(compile(ast.Module(body=[function], type_ignores=[]),
                 "core/_10_gen_audio.py", "exec"), namespace)
    _shutdown_speed_executor = namespace["_shutdown_speed_executor"]

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)

    def running_job():
        started.set()
        release.wait(timeout=2)
        finished.set()

    active = executor.submit(running_job)
    queued = executor.submit(lambda: None)
    assert started.wait(timeout=1)

    closer = threading.Thread(target=_shutdown_speed_executor, args=(executor,))
    closer.start()
    try:
        deadline = time.monotonic() + 1
        while not queued.cancelled() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert queued.cancelled()
        assert closer.is_alive()
        assert not finished.is_set()
    finally:
        release.set()
        closer.join(timeout=2)

    assert not closer.is_alive()
    assert finished.is_set()
    assert active.done()


def _load_merge_module(monkeypatch):
    """Import step 11 without pydub (absent in the lightweight test env)."""
    stub = types.ModuleType("pydub")
    stub.AudioSegment = object
    monkeypatch.setitem(sys.modules, "pydub", stub)
    sys.modules.pop("core._11_merge_audio", None)
    import core._11_merge_audio as merge
    return merge


def test_parse_plain_literal():
    assert parse_excel_list("[[5.825, 8.555]]") == [[5.825, 8.555]]


def test_parse_numpy2_repr():
    raw = "[[np.float64(90.693), np.float64(96.803)]]"
    got = parse_excel_list(raw)
    assert got == [[90.693, 96.803]]
    assert all(type(x) is float for pair in got for x in pair)


def test_parse_mixed_numpy_scalars():
    raw = "[[np.float64(1.5), np.int64(3)], [np.float32(2.25), 4.0]]"
    assert parse_excel_list(raw) == [[1.5, 3], [2.25, 4.0]]


def test_parse_text_lines_unchanged():
    assert parse_excel_list("['Yes', \"it's fine\", '中文']") == ["Yes", "it's fine", "中文"]


def test_parse_non_string_passthrough():
    value = [[1.0, 2.0]]
    assert parse_excel_list(value) is value


def test_parse_malformed_raises():
    with pytest.raises((ValueError, SyntaxError)):
        parse_excel_list("[[np.float64(nan)]]")


def test_plain_time_pairs_drops_numpy_scalars():
    cur = np.float64(1.5)
    dur = np.float64(2.25)
    pairs = plain_time_pairs([[cur, cur + dur]])
    assert pairs == [[1.5, 3.75]]
    assert "np." not in str(pairs)
    assert all(type(x) is float for pair in pairs for x in pair)


def test_written_cell_is_plain_literal(tmp_path):
    """Step 10 writes numpy scalars; the persisted cell must stay eval-safe."""
    pairs = plain_time_pairs([[np.float64(90.693), np.float64(96.803)]])
    path = tmp_path / "t.xlsx"
    pd.DataFrame({"new_sub_times": [pairs]}).to_excel(path, index=False)
    cell = pd.read_excel(path)["new_sub_times"].iloc[0]
    assert "np." not in str(cell)
    assert parse_excel_list(cell) == [[90.693, 96.803]]


def test_step11_loads_legacy_numpy_cells(tmp_path, monkeypatch):
    """The exact production failure: 83/1015 rows carried np.float64 tokens."""
    merge = _load_merge_module(monkeypatch)
    path = tmp_path / "tts_tasks.xlsx"
    pd.DataFrame({
        "number": [1, 2],
        "lines": ["['a']", "['b', 'c']"],
        "new_sub_times": [
            "[[5.8, 8.5]]",
            "[[np.float64(90.693), np.float64(96.803)], "
            "[np.float64(96.803), np.float64(99.1)]]",
        ],
    }).to_excel(path, index=False)

    df, lines, times = merge.load_and_flatten_data(str(path))
    assert lines == ["a", "b", "c"]
    assert times == [[5.8, 8.5], [90.693, 96.803], [96.803, 99.1]]
    assert all(type(x) is float for pair in times for x in pair)
    assert len(merge.get_audio_files(df)) == 3
