"""步骤 2/3 的词级存储单测：core/utils/words_store.py

JSON 是主存（无损），xlsx 只是给人看的导出。这里锁死：
- 毫秒级时间戳 JSON 往返不变（xlsx 数值往返会漂，曾经踩过）
- 引号由 JSON 语义保证，落盘不再包一层 "
- 缺失/损坏 → None（而不是抛异常打断流水线）
- load_words 的优先级：JSON 优先 → xlsx 兜底 → 空 DataFrame

xlsx 相关用例需要 pandas/openpyxl，缺失则自动 skip。
"""

from __future__ import annotations

import json

import pytest

from core.utils import words_store as ws


def _words():
    return [
        {"text": "Hello", "start": 0.0, "end": 0.52},
        {"text": "world.", "start": 0.52, "end": 1.031},
        {"text": "Hi", "start": 1.1, "end": 1.4, "speaker_id": "SPEAKER_01"},
    ]


# ------------------------------------------------------------------ JSON

def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "log" / "cleaned_chunks.json"
    ws.save_words_json(_words(), str(p))
    assert ws.load_words_json(str(p)) == _words()


def test_timestamps_survive_exactly(tmp_path):
    """1.031 这类毫秒值必须原样回来（xlsx 数值往返会漂）。"""
    p = str(tmp_path / "w.json")
    ws.save_words_json([{"text": "a", "start": 1.031, "end": 2.999}], p)
    got = ws.load_words_json(p)[0]
    assert got["start"] == 1.031 and got["end"] == 2.999


def test_schema_version_on_disk(tmp_path):
    p = tmp_path / "w.json"
    ws.save_words_json(_words(), str(p))
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["version"] == ws.SCHEMA_VERSION
    assert isinstance(raw["words"], list)


def test_missing_file_returns_none(tmp_path):
    assert ws.load_words_json(str(tmp_path / "nope.json")) is None


def test_corrupt_file_returns_none(tmp_path):
    p = tmp_path / "w.json"
    p.write_text("{oops", encoding="utf-8")
    assert ws.load_words_json(str(p)) is None


def test_non_dict_json_returns_none(tmp_path):
    p = tmp_path / "w.json"
    p.write_text('"just a string"', encoding="utf-8")
    assert ws.load_words_json(str(p)) is None


def test_bare_list_is_accepted(tmp_path):
    """兼容旧文件：顶层直接是 list 也能读。"""
    p = tmp_path / "w.json"
    p.write_text(json.dumps(_words()), encoding="utf-8")
    assert ws.load_words_json(str(p)) == _words()


# ------------------------------------------------------------------ xlsx

@pytest.fixture
def needs_xlsx():
    pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")


def test_export_xlsx_keeps_legacy_shape(tmp_path, needs_xlsx):
    """导出必须兼容旧 save_results：text 带引号、列序 text/start/end。"""
    import pandas as pd

    p = tmp_path / "log" / "cleaned_chunks.xlsx"
    ws.export_words_xlsx(_words(), str(p))
    df = pd.read_excel(p)
    assert list(df.columns)[:3] == ["text", "start", "end"]
    assert df["text"].tolist() == ['"Hello"', '"world."', '"Hi"']
    assert "speaker_id" in df.columns


def test_export_skips_empty_text(tmp_path, needs_xlsx):
    import pandas as pd

    p = tmp_path / "c.xlsx"
    words = [{"text": "a", "start": 0, "end": 1},
             {"text": "   ", "start": 1, "end": 2},
             {"text": "b", "start": 2, "end": 3}]
    ws.export_words_xlsx(words, str(p))
    assert len(pd.read_excel(p)) == 2


def test_load_words_prefers_json(tmp_path, needs_xlsx):
    j = str(tmp_path / "w.json")
    x = str(tmp_path / "w.xlsx")
    ws.save_words_json([{"text": "from-json", "start": 0, "end": 1}], j)
    ws.export_words_xlsx([{"text": "from-xlsx", "start": 0, "end": 1}], x)
    df = ws.load_words(xlsx_path=x, json_path=j)
    assert df["text"].tolist() == ["from-json"]


def test_load_words_xlsx_fallback_strips_quotes(tmp_path, needs_xlsx):
    """老工程只有 xlsx：读进来必须自动去引号去空格。"""
    x = str(tmp_path / "w.xlsx")
    ws.export_words_xlsx(_words(), x)
    df = ws.load_words(xlsx_path=x, json_path=str(tmp_path / "missing.json"))
    assert df["text"].tolist() == ["Hello", "world.", "Hi"]


def test_load_words_from_json_unquoted(tmp_path, needs_xlsx):
    j = str(tmp_path / "w.json")
    ws.save_words_json(_words(), j)
    df = ws.load_words(json_path=j)
    assert df["text"].tolist() == ["Hello", "world.", "Hi"]
    assert df["start"].tolist() == [0.0, 0.52, 1.1]


def test_load_words_empty_when_nothing(tmp_path, needs_xlsx):
    df = ws.load_words(xlsx_path=str(tmp_path / "no.xlsx"),
                       json_path=str(tmp_path / "no.json"))
    assert len(df) == 0
    assert list(df.columns)[:3] == ["text", "start", "end"]
