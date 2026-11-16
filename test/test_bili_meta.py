"""B站投稿文案三件套：元数据保存、上下文兜底、讲者建议、公式硬校验、端到端。

公式（标题四段式全角｜、标签恰好 10 个、简介七段式）提炼自
bili-video-meta skill，运行时只依赖项目内 prompt 模板 bili_meta。
"""
from __future__ import annotations

import json
import os

import pytest

from conftest import CHAIN_CONFIG, resp_ok

GOOD = {
    "titles": [
        "EP042｜为什么他越喜欢你，越不主动？｜男性心理｜Matthew Hussey",
        "EP042｜8个信号说明他早已动情｜心动信号｜Matthew Hussey",
        "EP042｜别再猜他的心思了，先看这3件事｜恋爱心理学｜Matthew Hussey",
    ],
    "desc": "你" * 300,
    "tags": ["亲密关系", "恋爱心理", "两性关系", "男性心理", "女生必看",
             "忽冷忽热", "他不回消息怎么办", "断联", "怎么让男人主动", "情绪价值"],
}

_SENTENCE = "为什么我们总在关系里重复同样的模式却以为这次会不一样"


def _srt(rows):
    out = []
    for i, (a, b, text) in enumerate(rows, 1):
        out += [str(i),
                f"00:{a // 60:02d}:{a % 60:02d},000 --> 00:{b // 60:02d}:{b % 60:02d},000",
                text, ""]
    return "\n".join(out)


def _write_srt(workspace, name, n=20, text=_SENTENCE):
    os.makedirs(workspace, exist_ok=True)
    rows = [(i * 3, i * 3 + 3, f"{text}（第{i + 1}句）") for i in range(n)]
    with open(os.path.join(workspace, name), "w", encoding="utf-8") as f:
        f.write(_srt(rows))


# ---------------- 第 1 步：下载时保存元数据 ----------------

def test_save_video_meta_writes_json(tmp_path):
    from core._1_ytdlp import _save_video_meta
    _save_video_meta(str(tmp_path), {
        "title": "How to Fix It", "description": "abc",
        "uploader": "Matthew Hussey", "channel": "MH",
        "webpage_url": "https://youtu.be/x", "duration": 61})
    d = json.loads((tmp_path / "video_meta.json").read_text(encoding="utf-8"))
    assert d["title"] == "How to Fix It"
    assert d["uploader"] == "Matthew Hussey"
    assert d["description"] == "abc"


def test_save_video_meta_tolerates_none(tmp_path):
    from core._1_ytdlp import _save_video_meta
    _save_video_meta(str(tmp_path), None)
    assert not (tmp_path / "video_meta.json").exists()


# ---------------- 上下文与讲者建议 ----------------

def test_context_prefers_meta_file(tmp_path):
    (tmp_path / "video_meta.json").write_text(json.dumps(
        {"title": "T", "description": "D", "uploader": "U"}), encoding="utf-8")
    from core.services.bili_meta_service import load_video_context
    ctx = load_video_context(str(tmp_path))
    assert ctx == {"title": "T", "description": "D", "uploader": "U",
                   "source": "downloaded"}


def test_context_falls_back_to_filename(tmp_path):
    (tmp_path / "My Video.mp4").write_bytes(b"x")
    from core.services.bili_meta_service import load_video_context
    ctx = load_video_context(str(tmp_path))
    assert ctx["source"] == "filename"
    assert ctx["title"] == "My Video"


def test_context_none_when_empty(tmp_path):
    from core.services.bili_meta_service import load_video_context
    assert load_video_context(str(tmp_path))["source"] == "none"


def test_context_survives_corrupt_meta(tmp_path):
    (tmp_path / "video_meta.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "v.mp4").write_bytes(b"x")
    from core.services.bili_meta_service import load_video_context
    assert load_video_context(str(tmp_path))["source"] == "filename"


def test_suggest_speaker_pool_and_config(cfg_path):
    cfg_path(CHAIN_CONFIG + "\nbili:\n  speaker_names: [Some Body]\n")
    from core.services.bili_meta_service import suggest_speaker
    # 内置池：标题里认出已知讲者
    assert suggest_speaker(
        {"title": "Talk with Esther Perel", "uploader": "x", "description": ""}) \
        == "Esther Perel"
    # config 追加的人名也能认出
    assert suggest_speaker(
        {"title": "Interview with Some Body", "uploader": "x", "description": ""}) \
        == "Some Body"
    # 认不出 → 退回频道名
    assert suggest_speaker(
        {"title": "random", "uploader": "Chan", "description": ""}) == "Chan"


# ---------------- 公式硬校验 ----------------

@pytest.fixture
def valid():
    from core.services.bili_meta_service import _valid_bili_meta
    return lambda d: _valid_bili_meta(d, "EP042")


def test_valid_accepts_good_shape(valid):
    assert valid(GOOD)["status"] == "success"


def test_valid_rejects_bad_shapes(valid):
    cases = [
        "not-a-dict",
        {**GOOD, "titles": GOOD["titles"][:2]},                        # 只 2 个候选
        {**GOOD, "titles": ["EP042|a|b|c"] * 3},                       # 半角 |
        {**GOOD, "titles": ["EP043｜a｜b｜c"] * 3},                    # 序号错
        {**GOOD, "titles": ["EP042｜a｜b"] * 3},                       # 只有 3 段
        {**GOOD, "desc": "短"},                                         # 简介太短
        {**GOOD, "tags": GOOD["tags"][:9]},                            # 标签 9 个
        {**GOOD, "tags": GOOD["tags"][:9] + ["这是一个超过十个字的超长标签"]},
    ]
    for case in cases:
        assert valid(case)["status"] == "error", case


# ---------------- 端到端生成（fake LLM） ----------------

def test_generate_end_to_end(tmp_cwd, cfg_path, fake_llm):
    cfg_path()
    calls, _script = fake_llm([resp_ok(json.dumps(GOOD, ensure_ascii=False))])
    _write_srt("output", "trans.srt")

    from core.services.bili_meta_service import generate_bili_meta
    res = generate_bili_meta(ep=42, speaker="Matthew Hussey",
                             title="Original Title", description="YT desc",
                             workspace="output")

    assert res["ep_tag"] == "EP042"
    assert len(res["titles"]) == 3 and len(res["tags"]) == 10
    assert res["transcript"]["source"] == "trans_srt"
    assert res["transcript"]["sampled"] is False
    md = open("output/bili_meta.md", encoding="utf-8").read()
    assert "EP042" in md and "Matthew Hussey" in md and "Original Title" in md
    assert "一手素材" in md and "全文直送" in md
    # prompt 里转录是主素材，原视频信息只是次要参考
    prompt = [c[1] for c in calls if c[0] != "__key__"][0][0]["content"]
    assert _SENTENCE in prompt and "取材铁律" in prompt
    assert "【一手素材 · 决定性】" in prompt and "【次要参考" in prompt
    assert "Original Title" in prompt and "Matthew Hussey" in prompt


def test_generate_rejects_bad_ep(tmp_cwd, cfg_path, fake_llm):
    cfg_path()
    fake_llm([])
    from core.services.bili_meta_service import generate_bili_meta
    with pytest.raises(ValueError, match="EP"):
        generate_bili_meta(ep=0, speaker="X", title="T", workspace="output")
    with pytest.raises(ValueError, match="EP"):
        generate_bili_meta(ep="abc", speaker="X", title="T", workspace="output")


def test_generate_requires_transcript(tmp_cwd, cfg_path, fake_llm):
    cfg_path()
    fake_llm([])
    os.makedirs("output", exist_ok=True)
    open("output/video_meta.json", "w", encoding="utf-8").write(
        json.dumps({"title": "只有元数据", "description": "D"}))
    from core.services.bili_meta_service import generate_bili_meta
    # 光有 YouTube 元数据不能生成：转录才是决定性素材
    with pytest.raises(ValueError, match="转录"):
        generate_bili_meta(ep=5, speaker="X", workspace="output")


def test_generate_without_original_title(tmp_cwd, cfg_path, fake_llm):
    """原视频信息降级为参考：没有标题/简介，只要有转录也能生成。"""
    cfg_path()
    fake_llm([resp_ok(json.dumps(GOOD, ensure_ascii=False))])
    _write_srt("output", "trans.srt")
    from core.services.bili_meta_service import generate_bili_meta
    res = generate_bili_meta(ep=42, speaker="X", workspace="output")
    assert res["video_title"] == "（无原视频标题）"
    assert res["ep_tag"] == "EP042"


# ---------------- HTTP 接口 ----------------

@pytest.fixture
def bili_client(tmp_cwd):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import bili_api
    app = FastAPI()
    app.include_router(bili_api.router)
    return TestClient(app)


def test_api_context_from_filename(bili_client):
    os.makedirs("output", exist_ok=True)
    open("output/My Video.mp4", "wb").write(b"x")
    d = bili_client.get("/api/bili/context").json()
    assert d["source"] == "filename"
    assert d["title"] == "My Video"
    assert d["transcript"]["available"] is False


def test_api_context_reports_transcript(bili_client):
    _write_srt("output", "trans.srt")
    d = bili_client.get("/api/bili/context").json()
    t = d["transcript"]
    assert t["available"] is True
    assert t["source"] == "trans_srt" and t["lang"] == "中文译文"
    assert t["sampled"] is False and t["total_chars"] > 0
    assert "text" not in t          # 只回统计，不把几万字塞进页面请求


def test_api_generate_ok(bili_client, cfg_path, fake_llm):
    cfg_path()
    fake_llm([resp_ok(json.dumps(GOOD, ensure_ascii=False))])
    _write_srt("output", "trans.srt")
    r = bili_client.post("/api/bili/generate", json={
        "ep": 42, "speaker": "Matthew Hussey",
        "title": "T", "description": "D"})
    assert r.status_code == 200
    assert r.json()["result"]["ep_tag"] == "EP042"


def test_api_generate_maps_valueerror_to_400(bili_client):
    r = bili_client.post("/api/bili/generate", json={"ep": 0, "speaker": "x"})
    assert r.status_code == 400
    assert "EP" in r.json()["detail"]


def test_api_generate_without_transcript_is_400(bili_client, cfg_path, fake_llm):
    cfg_path()
    fake_llm([])
    os.makedirs("output", exist_ok=True)
    r = bili_client.post("/api/bili/generate",
                         json={"ep": 42, "speaker": "x", "title": "只有元数据"})
    assert r.status_code == 400
    assert "转录" in r.json()["detail"]


# ---------------- 转录素材：来源优先级 / 解析 / 全文与采样 ----------------

def test_priority_trans_srt_is_primary(tmp_cwd):
    """用户确认的主素材：中文译文 trans.srt 压过其它一切。"""
    from core.services.bili_meta_service import load_transcript
    _write_srt("output", "trans.srt", text="中文译文内容")
    _write_srt("output", "src.srt", text="English source")
    os.makedirs("output/log", exist_ok=True)
    open("output/log/split_by_nlp.txt", "w", encoding="utf-8").write("English sentence\n")
    open("output/log/cleaned_chunks.json", "w", encoding="utf-8").write("{}")
    tr = load_transcript("output")
    assert tr["available"] and tr["source"] == "trans_srt"
    assert tr["lang"] == "中文译文"
    assert "中文译文内容" in tr["text"] and "English source" not in tr["text"]


@pytest.mark.parametrize("name,expected", [
    ("src.srt", "src_srt"),
    ("log/split_by_nlp.txt", "split_by_nlp"),
    ("log/split_by_meaning.txt", "split_by_meaning"),
])
def test_fallback_to_other_text_sources(tmp_cwd, name, expected):
    from core.services.bili_meta_service import load_transcript
    os.makedirs("output/log", exist_ok=True)
    p = os.path.join("output", name)
    body = _srt([(0, 3, "English words " * 20), (3, 6, "More English words " * 20)]) \
        if name.endswith(".srt") else ("English words " * 20 + "\n") * 2
    open(p, "w", encoding="utf-8").write(body)
    tr = load_transcript("output")
    assert tr["available"] and tr["source"] == expected
    assert tr["lang"] == "英文原文"


def test_fallback_to_word_store_and_checkpoint(tmp_cwd):
    from core.services.bili_meta_service import _group_words, load_transcript
    os.makedirs("output/log", exist_ok=True)
    words = [{"text": "Why", "start": 0.1}, {"text": "", "start": 0.2},
             {"text": ".", "start": 0.3}, {"text": "am", "start": 0.4}]
    assert _group_words(words)[0][1] == "Why. am"     # 空行跳过、标点贴前词

    long_words = [{"text": f"word{i}", "start": i} for i in range(60)]
    open("output/log/cleaned_chunks.json", "w", encoding="utf-8").write(
        json.dumps({"version": 1, "words": long_words}))
    assert load_transcript("output")["source"] == "cleaned_chunks"

    os.remove("output/log/cleaned_chunks.json")
    os.makedirs("output/log/asr_checkpoint", exist_ok=True)
    with open("output/log/asr_checkpoint/seg_0001.json", "w", encoding="utf-8") as f:
        json.dump({"segments": [{"start": 20, "text": "late " * 40},
                                {"start": 1, "text": "early " * 40}]}, f)
    tr = load_transcript("output")
    assert tr["source"] == "asr_checkpoint"
    assert tr["text"].index("early") < tr["text"].index("late")   # 按时间排序


def test_no_transcript_and_corrupt_file(tmp_cwd):
    from core.services.bili_meta_service import load_transcript
    assert load_transcript("output")["available"] is False
    _write_srt("output", "trans.srt", n=1, text="太短")
    assert load_transcript("output")["available"] is False   # 低于最小可用字数


def test_srt_parser_skips_index_and_timecode(tmp_cwd):
    from core.services.bili_meta_service import _srt_lines
    lines = _srt_lines(_srt([(0, 3, "第一句"), (3, 6, "第二句")]))
    assert lines == [(0.0, "第一句"), (3.0, "第二句")]


def test_full_text_under_budget(tmp_cwd, cfg_path):
    """中文译文没超预算 → 全文直送，一条不丢，时间标记稀疏插入。"""
    cfg_path(CHAIN_CONFIG + """
bili:
  transcript_chars: 20000
""")
    from core.services.bili_meta_service import load_transcript
    _write_srt("output", "trans.srt", n=20)
    tr = load_transcript("output")
    assert tr["sampled"] is False and tr["samples"] == 0
    assert "范围：全文" in tr["text"]
    for i in range(1, 21):
        assert f"第{i}句" in tr["text"]
    assert tr["text"].count("[00:") <= 2     # 不是每行都打时间戳


def test_uniform_sampling_over_budget(tmp_cwd, cfg_path):
    """超预算才采样：按时间均匀 4 段、头尾加权、总长受控、带时间标记。"""
    cfg_path(CHAIN_CONFIG + """
bili:
  transcript_chars: 1000
  transcript_samples: 4
""")
    from core.services.bili_meta_service import load_transcript, sample_lines
    _write_srt("output", "trans.srt", n=120)
    tr = load_transcript("output")
    assert tr["sampled"] is True and tr["samples"] == 4
    assert tr["chars"] < 1800
    assert "均匀节选 4 段" in tr["text"]
    assert all(f"转录节选 {i}/4" in tr["text"] for i in range(1, 5))
    assert "[00:00]" in tr["text"] and "（开头）" in tr["text"] and "（结尾）" in tr["text"]

    lines = [(i * 3, _SENTENCE * 3 + str(i)) for i in range(200)]
    parts = sample_lines(lines, budget=1200, n_samples=4).split("### 转录节选 ")
    assert len(parts) == 5
    assert len(parts[1]) > len(parts[2])      # 头段多取
    assert len(parts[4]) > len(parts[3])      # 尾段多取


def test_summary_is_appended_for_global_context(tmp_cwd):
    from core.services.bili_meta_service import load_transcript
    _write_srt("output", "trans.srt")
    os.makedirs("output/log", exist_ok=True)
    open("output/log/summary.txt", "w", encoding="utf-8").write(
        "A young woman consults Esther Perel about unavailable partners.")
    tr = load_transcript("output")
    assert tr["has_summary"] is True
    assert "全局内容摘要" in tr["text"] and "Esther Perel" in tr["text"]


# ---------------- 模板：主次关系 + 自定义模板兜底 ----------------

def test_prompt_template_puts_transcript_first():
    from core.prompts import DEFAULT_TEMPLATES, get_bili_meta_prompt
    tpl = DEFAULT_TEMPLATES["bili_meta"]
    assert tpl.index("{{transcript}}") < tpl.index("{{video_title}}")
    assert "取材铁律" in tpl and "不得主导选题" in tpl
    p = get_bili_meta_prompt("T", "D", "S", 42, transcript="转录正文" * 30,
                             transcript_meta="来源 trans.srt，全文")
    assert "转录正文" in p and "来源 trans.srt，全文" in p
    assert "{{transcript}}" not in p
    assert "EP{序号}｜{钩子}｜{主题词}｜{讲者}" in p


def test_custom_template_without_placeholder_still_gets_transcript():
    """用户在提示词页存过旧模板（没有 {{transcript}}）时，主素材不能被吞掉。"""
    from core.prompts import get_bili_meta_prompt
    custom = "只写标题 {{ep}} / {{speaker}}，原视频 {{video_title}}"
    body = "<!-- videolingo:transcript -->\n语言：中文译文\n\n" + _SENTENCE * 10
    p = get_bili_meta_prompt("T", "D", "S", 42, transcript=body, template=custom)
    assert _SENTENCE in p
    assert "一手素材（视频转录" in p


def test_speaker_can_be_recognized_from_transcript(tmp_cwd):
    from core.services.bili_meta_service import suggest_speaker
    ctx = {"title": "", "uploader": "", "description": ""}
    assert suggest_speaker(ctx, "今天我们请到 Matthew Hussey 谈谈亲密关系") \
        == "Matthew Hussey"


# ---------------- 刷新回显（FR-27 v1.37）：md 是唯一真相源 ----------------

def _write_good_md(workspace="output", *, mutate=None):
    """用唯一的 _render_md 造一份真实格式的 md，可按 mutate 改坏。"""
    from core.services.bili_meta_service import _render_md
    tr = {"label": "中文译文字幕（trans.srt）", "sampled": False,
          "samples": 0, "total_chars": 7882}
    md = _render_md(GOOD, "EP042", "Matthew Hussey", "How to Fix It", tr)
    if mutate:
        md = mutate(md)
    os.makedirs(workspace, exist_ok=True)
    with open(os.path.join(workspace, "bili_meta.md"), "w", encoding="utf-8") as f:
        f.write(md)
    return md


def test_load_saved_bili_meta_roundtrip(tmp_cwd):
    from core.services.bili_meta_service import load_saved_bili_meta
    _write_good_md()
    got = load_saved_bili_meta("output")
    assert got is not None
    assert got["titles"] == GOOD["titles"]
    assert got["desc"] == GOOD["desc"]
    assert got["tags"] == GOOD["tags"]
    assert got["ep_tag"] == "EP042" and got["speaker"] == "Matthew Hussey"
    assert got["video_title"] == "How to Fix It"
    assert got["saved_to"].endswith("bili_meta.md")
    assert got["generated_at"]


def test_generate_then_reload_survives_refresh(tmp_cwd, cfg_path, fake_llm):
    """生成 → 重新读盘 == 刷新页面后 /context 的 existing。"""
    cfg_path()
    fake_llm([resp_ok(json.dumps(GOOD, ensure_ascii=False))])
    _write_srt("output", "trans.srt")
    from core.services.bili_meta_service import generate_bili_meta, load_saved_bili_meta
    res = generate_bili_meta(ep=42, speaker="Matthew Hussey", title="T",
                             description="D", workspace="output")
    got = load_saved_bili_meta("output")
    assert got is not None
    assert got["titles"] == res["titles"] == GOOD["titles"]
    assert got["desc"] == res["desc"] and got["tags"] == res["tags"]


def test_load_saved_bili_meta_missing(tmp_cwd):
    from core.services.bili_meta_service import load_saved_bili_meta
    assert load_saved_bili_meta("output") is None


@pytest.mark.parametrize("mutate", [
    lambda md: md.replace("## 标签", "## 没有标签"),       # 缺标签节
    lambda md: md.replace("，断联", ""),                   # 只剩 9 个标签
    lambda md: md.replace("3. EP042", "3. XXX"),          # 标题丢 EP 前缀
    lambda md: md[:md.index("## 简介")],                  # 缺简介 + 标签
    lambda md: md.replace("# B站投稿文案（", "# 随便写点什么（"),
])
def test_load_saved_bili_meta_corrupt_returns_none(tmp_cwd, mutate):
    from core.services.bili_meta_service import load_saved_bili_meta
    _write_good_md(mutate=mutate)
    assert load_saved_bili_meta("output") is None


def test_load_saved_bili_meta_bad_encoding(tmp_cwd):
    from core.services.bili_meta_service import load_saved_bili_meta
    os.makedirs("output", exist_ok=True)
    with open("output/bili_meta.md", "wb") as f:
        f.write(b"\xff\xfe\x00bad")
    assert load_saved_bili_meta("output") is None


def test_api_context_exposes_existing(bili_client):
    _write_good_md()
    d = bili_client.get("/api/bili/context").json()
    assert d["existing"]["titles"] == GOOD["titles"]
    assert d["existing"]["tags"] == GOOD["tags"]
    assert d["existing"]["ep_tag"] == "EP042"
    # 归档清空 output 后 md 被搬走 → existing 自然为 null（新视频不显示旧文案）
    os.remove("output/bili_meta.md")
    assert bili_client.get("/api/bili/context").json()["existing"] is None


def test_api_context_survives_corrupt_md(bili_client):
    _write_good_md(mutate=lambda md: md.replace("## 简介", "## 坏掉的节"))
    r = bili_client.get("/api/bili/context")
    assert r.status_code == 200 and r.json()["existing"] is None
