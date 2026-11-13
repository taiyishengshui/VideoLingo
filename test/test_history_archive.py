"""「保存历史记录」：output/ → history/<视频名>/（core/services/archive_service.py）

规格来自实际使用场景：
- 一条视频跑完点一下，成品/字幕/表格/日志/LLM 缓存全部落到 history/<视频名>/
- **output/ 目录保留**，只清空内容（删目录会让下游找不到路径）
- 目录名 = 视频文件名（非法字符换成下划线）
- 同一视频重复保存 → 覆盖旧归档，不新建 xxx_2
- 没有产物时明确报错，不能"成功但什么都没发生"
- pipeline_latest.log 只能复制：它正被任务线程写着，移走日志句柄就失联了

全程在 tmp_path 里跑，不碰真实 output/ 与 history/。
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from core.services.archive_service import (
    archive_current, retry_locked, sanitize_name)


def _touch(path: str, data: str = "x") -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data)
    return path


def _make_output(tmp: str, name: str = "My Video") -> str:
    """造一个典型的跑完任务的 output/。"""
    ws = os.path.join(tmp, "output")
    _touch(os.path.join(ws, f"{name}.mp4"))
    _touch(os.path.join(ws, "src.srt"))
    _touch(os.path.join(ws, "trans.srt"))
    _touch(os.path.join(ws, "log", "cleaned_chunks.json"))
    _touch(os.path.join(ws, "log", "pipeline_latest.log"), "run log")
    _touch(os.path.join(ws, "gpt_log", "translate_faith.json"))
    _touch(os.path.join(ws, "audio", "raw.mp3"))
    return ws


def test_archive_moves_everything_under_video_name(tmp_path):
    ws = _make_output(str(tmp_path))
    stats = archive_current(workspace=ws, history_dir=str(tmp_path / "history"))

    dest = tmp_path / "history" / "My Video"
    assert stats["video"] == "My Video"
    assert dest.is_dir()
    assert (dest / "My Video.mp4").exists()
    assert (dest / "src.srt").exists()
    assert (dest / "trans.srt").exists()
    assert (dest / "log" / "cleaned_chunks.json").exists()
    assert (dest / "gpt_log" / "translate_faith.json").exists()
    assert (dest / "audio" / "raw.mp3").exists()
    assert stats["moved"] >= 6


def test_output_dir_survives_and_is_emptied(tmp_path):
    """目录保留、内容清空 —— 用户的硬要求。"""
    ws = _make_output(str(tmp_path))
    archive_current(workspace=ws, history_dir=str(tmp_path / "history"))

    assert os.path.isdir(ws), "output/ 目录必须还在"
    left = []
    for root, _dirs, files in os.walk(ws):
        left += [os.path.join(root, f) for f in files]
    # 只剩正在写的运行日志（它被复制而不是移走）
    assert [os.path.basename(p) for p in left] == ["pipeline_latest.log"]
    assert not os.path.exists(os.path.join(ws, "gpt_log"))
    assert not os.path.exists(os.path.join(ws, "My Video.mp4"))


def test_live_log_is_copied_not_moved(tmp_path):
    ws = _make_output(str(tmp_path))
    archive_current(workspace=ws, history_dir=str(tmp_path / "history"))

    live = os.path.join(ws, "log", "pipeline_latest.log")
    archived = tmp_path / "history" / "My Video" / "log" / "pipeline_latest.log"
    assert os.path.exists(live), "正在写的日志不能移走"
    assert archived.exists() and archived.read_text(encoding="utf-8") == "run log"


def test_same_video_overwrites_instead_of_suffixing(tmp_path):
    hist = str(tmp_path / "history")
    ws = _make_output(str(tmp_path))
    archive_current(workspace=ws, history_dir=hist)

    # 同一个视频重跑：媒体文件会再次出现在 output/（下载/拷入）
    _touch(os.path.join(ws, "My Video.mp4"))
    _touch(os.path.join(ws, "src.srt"), "new content")
    stats = archive_current(workspace=ws, history_dir=hist)

    dest = tmp_path / "history" / "My Video"
    assert stats["overwritten"] >= 1
    assert (dest / "src.srt").read_text(encoding="utf-8") == "new content"
    assert not (tmp_path / "history" / "My Video_2").exists()


def test_explicit_name_wins(tmp_path):
    ws = _make_output(str(tmp_path))
    stats = archive_current(workspace=ws, history_dir=str(tmp_path / "history"),
                            name="custom name")
    assert stats["video"] == "custom name"
    assert (tmp_path / "history" / "custom name" / "src.srt").exists()


def test_name_falls_back_to_manifest(tmp_path):
    """批量模式下媒体在 workspace 之外，只有 manifest 指向它 —— 名字也得对。"""
    ws = os.path.join(str(tmp_path), "output")
    media = _touch(os.path.join(str(tmp_path), "elsewhere", "Some Podcast.mp4"))
    _touch(os.path.join(ws, "input_manifest.json"),
           json.dumps({"path": media.replace("\\", "/"), "type": "video"}))
    _touch(os.path.join(ws, "src.srt"))

    stats = archive_current(workspace=ws, history_dir=str(tmp_path / "history"))
    assert stats["video"] == "Some Podcast"


def test_missing_output_raises(tmp_path):
    with pytest.raises(ValueError, match="不存在"):
        archive_current(workspace=str(tmp_path / "nope"))


def test_no_media_raises_with_clear_message(tmp_path):
    ws = os.path.join(str(tmp_path), "output")
    _touch(os.path.join(ws, "src.srt"))          # 有产物但没有输入媒体
    with pytest.raises(ValueError, match="无法确定历史目录名"):
        archive_current(workspace=ws, history_dir=str(tmp_path / "history"))


@pytest.mark.parametrize("raw,expected", [
    ('a/b:c*d?e"f<g>h|i', "a_b_c_d_e_f_g_h_i"),
    ("  trailing  ", "trailing"),
    ("", "untitled"),
])
def test_sanitize_name(raw, expected):
    assert sanitize_name(raw) == expected


def test_result_paths_use_forward_slashes(tmp_path):
    """前端直接展示 dest，Windows 反斜杠在提示里很难看。"""
    ws = _make_output(str(tmp_path))
    stats = archive_current(workspace=ws, history_dir=str(tmp_path / "history"))
    assert "\\" not in stats["dest"]


# ------------------------------------------------- 源文件被占用（v1.14）
# 页面 <video> 流式预览 output_sub.mp4 时，服务端 FileResponse 持读句柄，
# Windows 上 shutil.move(os.rename) 直接 PermissionError → 退化「复制+
# 删源」，删源还要等前端断开预览后重试。绝不能静默 pass（旧 bug：文件
# 复制进归档但永远留在 output/，用户只看到"清空不了，被 python 使用"）。

def _lock_one_file(monkeypatch, src_name, fail_removes=-1):
    """让 shutil.move 对指定文件抛 PermissionError；os.remove 前
    fail_removes 次也抛（-1 = 永远失败），模拟句柄一直没释放。"""
    from core.services import archive_service as svc
    real_move, real_remove = shutil.move, os.remove
    state = {"removes": 0}

    def fake_move(src, dst):
        if os.path.basename(src) == src_name:
            raise PermissionError(13, "Permission denied", src)
        return real_move(src, dst)

    def fake_remove(p):
        if os.path.basename(p) == src_name:
            if fail_removes < 0 or state["removes"] < fail_removes:
                state["removes"] += 1
                raise PermissionError(13, "Permission denied", p)
        return real_remove(p)

    monkeypatch.setattr(svc.shutil, "move", fake_move)
    monkeypatch.setattr(svc.os, "remove", fake_remove)
    monkeypatch.setattr(svc.time, "sleep", lambda *_a: None)  # 重试不真睡
    return state


def test_locked_source_recovered_after_retry(tmp_path, monkeypatch):
    """删源重试期间句柄释放 → 文件正常清出（copy 后删掉，moved 计数）。"""
    ws = _make_output(str(tmp_path), "My Video")
    _lock_one_file(monkeypatch, "My Video.mp4", fail_removes=2)

    stats = archive_current(workspace=ws, history_dir=str(tmp_path / "history"))

    dest = tmp_path / "history" / "My Video" / "My Video.mp4"
    assert dest.exists() and dest.read_text(encoding="utf-8") == "x"
    assert not os.path.exists(os.path.join(ws, "My Video.mp4"))
    assert stats["locked"] == []
    assert stats["moved"] >= 6          # 复制+删源成功也计入 moved


def test_locked_source_forever_is_reported_not_silent(tmp_path, monkeypatch):
    """句柄一直不释放 → 复制进归档、源留在 output/、locked 明确上报。"""
    ws = _make_output(str(tmp_path), "My Video")
    _lock_one_file(monkeypatch, "My Video.mp4", fail_removes=-1)

    stats = archive_current(workspace=ws, history_dir=str(tmp_path / "history"))

    dest = tmp_path / "history" / "My Video" / "My Video.mp4"
    assert dest.exists(), "被占用的文件也要先复制进归档"
    assert os.path.exists(os.path.join(ws, "My Video.mp4")), "删不掉的源留在原地"
    assert stats["locked"] == ["My Video.mp4"]
    # 其他文件不受牵连
    assert not os.path.exists(os.path.join(ws, "src.srt"))
    assert stats["errors"] == []


# ------------------------------------------------- v1.20 主动释放钩子
# 等浏览器自己断开 Range 请求不可靠：删源重试时由 server 层逐次调用
# close_preview_streams()，Windows 上的读句柄才能真正消失。

def _lock_until_hook(monkeypatch, src_name, state):
    from core.services import archive_service as svc
    real_move, real_remove = shutil.move, os.remove

    def fake_move(src, dst):
        if os.path.basename(src) == src_name:
            raise PermissionError(13, "Permission denied", src)
        return real_move(src, dst)

    def fake_remove(p):
        if os.path.basename(p) == src_name and not state["released"]:
            state["blocked"] += 1
            raise PermissionError(13, "Permission denied", p)
        return real_remove(p)

    monkeypatch.setattr(svc.shutil, "move", fake_move)
    monkeypatch.setattr(svc.os, "remove", fake_remove)
    monkeypatch.setattr(svc.time, "sleep", lambda *_a: None)


def test_on_retry_hook_releases_locked_source(tmp_path, monkeypatch):
    """钩子关掉句柄后，下一次重试就能把源清出 output/。"""
    ws = _make_output(str(tmp_path), "My Video")
    state = {"released": False, "blocked": 0, "hooks": 0}
    _lock_until_hook(monkeypatch, "My Video.mp4", state)

    def hook():
        state["hooks"] += 1
        state["released"] = True

    stats = archive_current(workspace=ws, history_dir=str(tmp_path / "history"),
                            on_retry=hook)

    assert state["blocked"] == 1 and state["hooks"] == 1
    assert stats["locked"] == [] and stats["errors"] == []
    assert not os.path.exists(os.path.join(ws, "My Video.mp4"))
    assert (tmp_path / "history" / "My Video" / "My Video.mp4").exists()


def test_on_retry_exhausted_still_reports_locked(tmp_path, monkeypatch):
    """钩子也救不了（外部播放器占用）→ 每次重试都调用钩子，最后明确上报。"""
    from core.services import archive_service as svc

    ws = _make_output(str(tmp_path), "My Video")
    state = {"released": False, "blocked": 0, "hooks": 0}
    _lock_until_hook(monkeypatch, "My Video.mp4", state)

    def hook():
        state["hooks"] += 1

    stats = archive_current(workspace=ws, history_dir=str(tmp_path / "history"),
                            on_retry=hook)

    assert state["hooks"] == svc._REMOVE_ATTEMPTS - 1
    assert stats["locked"] == ["My Video.mp4"]
    assert (tmp_path / "history" / "My Video" / "My Video.mp4").exists()


# ------------------------------------------------- 归档后残留的后台清出

def test_live_preview_stream_no_longer_blocks_archive(tmp_path, monkeypatch):
    """用户的真实场景：预览还在读 mp4 时点「保存历史」，必须一次清出。"""
    from core.services import archive_service as svc
    from server import preview_stream as ps

    ws = _make_output(str(tmp_path), "My Video")
    video = os.path.join(ws, "My Video.mp4")
    ps.close_preview_streams()
    f = ps.open_stream(video)             # 模拟浏览器正在流式读取
    assert f.read(1) == b"x"
    monkeypatch.setattr(svc.time, "sleep", lambda *_a: None)  # 重试不真睡
    state = {"released": 0}

    def hook():
        state["released"] += 1
        return ps.close_preview_streams()

    try:
        stats = archive_current(workspace=ws,
                                history_dir=str(tmp_path / "history"),
                                on_retry=hook)
    finally:
        ps.close_preview_streams()

    if os.name == "nt":
        assert state["released"] >= 1, "Windows 上确实走了「主动关句柄再删源」路径"
    assert stats["locked"] == [], "不能再出现让用户刷新页面重存的 locked 文件"
    assert not os.path.exists(video)
    assert (tmp_path / "history" / "My Video" / "My Video.mp4").read_text(
        encoding="utf-8") == "x"


def test_retry_locked_clears_file_after_release(tmp_path, monkeypatch):
    """占用解除后自动把残留清出，用户不必刷新或再点一次保存。"""
    from core.services import archive_service as svc

    p = tmp_path / "output_sub.mp4"
    p.write_bytes(b"old")
    real_remove = os.remove
    state = {"released": False, "hooks": 0}

    def fake_remove(path):
        if os.path.abspath(path) == os.path.abspath(str(p)) and not state["released"]:
            raise PermissionError(13, "Permission denied", path)
        return real_remove(path)

    def hook():
        state["hooks"] += 1
        state["released"] = True

    monkeypatch.setattr(svc.os, "remove", fake_remove)
    monkeypatch.setattr(svc.time, "sleep", lambda *_a: None)

    assert svc.retry_locked(str(tmp_path), ["output_sub.mp4"],
                            on_retry=hook, attempts=5) == []
    assert not p.exists() and 0 < state["hooks"] <= 3


def test_retry_locked_never_deletes_a_new_artifact(tmp_path, monkeypatch):
    """同名文件被新任务重写 → 签名变了，绝不能当成残留删掉。"""
    from core.services import archive_service as svc

    p = tmp_path / "output_sub.mp4"
    p.write_bytes(b"old")

    def new_task_writes(secs):
        p.write_bytes(b"brand-new-artifact")

    monkeypatch.setattr(svc.time, "sleep", new_task_writes)
    monkeypatch.setattr(svc.os, "remove",
                        lambda path: pytest.fail("不能删除新产物"))

    assert svc.retry_locked(str(tmp_path), ["output_sub.mp4"], attempts=3) == []
    assert p.read_bytes() == b"brand-new-artifact"


def test_retry_locked_still_occupied_reports_names(tmp_path, monkeypatch):
    """外部播放器一直占着 → 明确回报；释放钩子只在最初 3 轮调用。"""
    from core.services import archive_service as svc

    p = tmp_path / "output_sub.mp4"
    p.write_bytes(b"old")
    state = {"hooks": 0}

    def blocked(path):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(svc.os, "remove", blocked)
    monkeypatch.setattr(svc.time, "sleep", lambda *_a: None)

    left = svc.retry_locked(str(tmp_path), ["output_sub.mp4"],
                            on_retry=lambda: state.update(hooks=state["hooks"] + 1),
                            attempts=6)
    assert left == ["output_sub.mp4"]
    assert state["hooks"] == 3, "3 轮后不再主动关流，避免打断用户新的预览"
