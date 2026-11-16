"""步骤范围选择的唯一归属测试：core/services/run_service.py。

页面顶部「开始 / 结束步骤」两个下拉、强制重跑、阶段一按钮最终都落到
resolve_steps()。这里锁死三件事：
1. 范围是【闭区间】，step_id 单步优先；
2. 默认值：开始=转录，结束按烧录开关（关→align，开→merge_sub）；
3. 纯音频输入时视频步骤被过滤，结束步骤只能【往前】收敛，不能越界。
"""

from __future__ import annotations

import pytest

from conftest import CHAIN_CONFIG
from core.services import run_service

BURN_ON = CHAIN_CONFIG + "\nburn_subtitles: true\n"
BURN_OFF = CHAIN_CONFIG + "\nburn_subtitles: false\n"


# ------------------------------------------------------------ 范围选择


def test_no_range_runs_everything():
    ids = run_service.resolve_steps(None, None)
    assert ids[0] == "transcribe"
    assert ids[-1] == "dub_to_vid"


def test_from_step_alone_runs_to_the_end():
    ids = run_service.resolve_steps(None, "translate")
    assert ids[0] == "translate"
    assert ids[-1] == "dub_to_vid"


def test_range_is_inclusive_on_both_ends():
    assert run_service.resolve_steps(None, "asr_correct", to_step="align") == [
        "asr_correct", "split", "translate", "align",
    ]


def test_to_step_without_from_starts_at_the_first_step():
    assert run_service.resolve_steps(None, None, to_step="split") == [
        "transcribe", "asr_correct", "split",
    ]


def test_single_step_wins_over_range():
    assert run_service.resolve_steps("align", None, to_step="transcribe") == ["align"]


def test_unknown_steps_are_rejected():
    with pytest.raises(KeyError):
        run_service.resolve_steps(None, None, to_step="nope")
    with pytest.raises(KeyError):
        run_service.resolve_steps(None, "nope")
    with pytest.raises(KeyError):
        run_service.resolve_steps("nope", None)


def test_reversed_range_is_rejected():
    with pytest.raises(ValueError, match="之后"):
        run_service.resolve_steps(None, "merge_sub", to_step="align")


# --------------------------------------------------------- 纯音频输入


def test_audio_only_clamps_end_to_last_available_step():
    """merge_sub 是 needs_video，音频输入时不存在 → 收敛到它前一步 gen_sub。"""
    ids = run_service.resolve_steps(None, "transcribe", audio_only=True,
                                   to_step="merge_sub")
    assert "merge_sub" not in ids
    assert ids[-1] == "gen_sub"


def test_audio_only_rejects_a_from_step_that_was_filtered_out():
    with pytest.raises(KeyError):
        run_service.resolve_steps(None, "merge_sub", audio_only=True)


# ------------------------------------------------------------ 默认范围


def test_default_range_follows_burn_switch(cfg_path):
    cfg_path(BURN_ON)
    assert run_service.default_run_range() == {
        "from_step": "transcribe", "to_step": "merge_sub"}
    cfg_path(BURN_OFF)
    assert run_service.default_run_range() == {
        "from_step": "transcribe", "to_step": "gen_sub"}


def test_burn_switch_is_read_fresh_without_restart(cfg_path):
    """load_key 每次现开 config.yaml —— 设置页改开关后默认值要立刻变。"""
    cfg_path(BURN_OFF)
    assert run_service.burn_subtitles_enabled() is False
    cfg_path(BURN_ON)
    assert run_service.burn_subtitles_enabled() is True


def test_missing_burn_key_falls_back_to_project_default(cfg_path):
    cfg_path(CHAIN_CONFIG)
    assert run_service.burn_subtitles_enabled() is True
    assert run_service.default_run_range()["to_step"] == "merge_sub"


def test_default_range_for_audio_only_never_points_at_a_video_step(cfg_path):
    cfg_path(BURN_ON)
    assert run_service.default_run_range(audio_only=True) == {
        "from_step": "transcribe", "to_step": "gen_sub"}
