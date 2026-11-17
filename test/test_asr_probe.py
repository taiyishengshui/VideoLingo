"""FR-43：ASR 对照实验脚本的纯函数测试（不加载模型、不依赖 GPU）。"""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "check_asr_probe.py"
_spec = importlib.util.spec_from_file_location("check_asr_probe", _PATH)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def w(word, start, end=None):
    return {"word": word, "start": start, "end": start + 0.2 if end is None else end}


BAD = [  # 复刻生产现象：That's/Probably 起点都落在读网址区间，连续 4 个大间隙
    w("order", 478.322), w("That's", 478.722), w("Probably", 478.962),
    w("the", 479.042), w("biggest", 479.842), w("problem", 479.922, 480.632),
    w("that", 482.962), w("I", 486.002), w("see", 489.042), w("is", 490.722),
]
GOOD = [
    w("order", 478.3), w("That's", 479.0), w("Thrive", 479.6), w("Cosmetics", 480.2),
    w("com", 485.0), w("slash", 485.6), w("Jillian", 486.1),
    w("Probably", 487.8), w("the", 488.2), w("biggest", 488.5), w("problem", 489.0),
]


def test_max_gap_run_counts_consecutive_big_gaps():
    assert probe.max_gap_run(BAD) >= 3
    assert probe.max_gap_run(GOOD) <= 2


def test_summary_picks_probably_and_preceding_thats():
    s = probe.summarize_words(BAD)
    assert s["probably_start"] == 478.962
    assert s["thats_start"] == 478.722
    s = probe.summarize_words(GOOD)
    assert s["probably_start"] == 487.8
    assert s["thats_start"] == 479.0


def test_verdict_fails_on_baseline_and_passes_on_aligned():
    bad = probe.verdict(probe.summarize_words(BAD))
    assert bad["pass"] is False and bad["probably_ok"] is False
    good = probe.verdict(probe.summarize_words(GOOD))
    assert good["pass"] is True and good["words_ok"] is None


def test_verdict_word_count_tolerance_against_baseline():
    s = probe.summarize_words(GOOD)
    assert probe.verdict(s, baseline_words=len(GOOD))["words_ok"] is True
    assert probe.verdict(s, baseline_words=len(GOOD) + 10)["words_ok"] is False
    assert probe.verdict(s, baseline_words=len(GOOD) + 10)["pass"] is False


def test_url_spelling_detected_in_bridge_text():
    text = "off your first order. That's Thrive. cause.com slash Jillian. Probably the"
    s = probe.summarize_words(GOOD, text)
    assert s["has_url_spelling"] is True
    assert "slash" in s["bridge_text"]
    assert probe.summarize_words(GOOD, "first order. Probably")["has_url_spelling"] is False


def test_summary_without_probably_is_safe():
    s = probe.summarize_words([w("hello", 480.0)])
    assert s["probably_start"] is None and s["thats_start"] is None
    assert probe.verdict(s)["pass"] is False


def test_groups_defined_and_script_is_read_only():
    assert set(probe.GROUPS) == {"G0", "G1", "G2", "G3", "G4", "G5", "G6"}
    src = _PATH.read_text(encoding="utf-8")
    assert "config.yaml" not in src.split('"""', 2)[2].replace("不改 config.yaml", "")
    assert "from core" not in src and "import core" not in src
