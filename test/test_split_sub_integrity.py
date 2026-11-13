"""Subtitles must never lose source words or emit empty translation parts."""
import importlib

import pytest

split = importlib.import_module("core._5_split_sub")


def test_target_parts_require_nonempty_strings():
    assert split.get_target_parts({"align": [{"target_part_1": "甲"}, {"target_part_2": "乙"}]}, 2) == ["甲", "乙"]
    for invalid in [None, [], {"align": []}, {"align": [1]},
                    {"align": [{"target_part_1": " "}]},
                    {"align": [{"target_part_1": None}]}]:
        assert split.get_target_parts(invalid, 1) is None


def test_invalid_source_split_keeps_original_without_translating(monkeypatch):
    monkeypatch.setattr(split, "load_key", lambda k:
                        {"max_length": 10, "target_multiplier": 1} if k == "subtitle" else 1)
    monkeypatch.setattr(split, "split_sentence", lambda *a, **kw: "one two\nmissing words")
    monkeypatch.setattr(split, "align_subs", lambda *a: pytest.fail("must not translate a lossy source split"))
    source = ["one two three four five six"]
    target = ["原译文"]
    assert split.split_align_subs(source.copy(), target.copy()) == (source, target, target)
