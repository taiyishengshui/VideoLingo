"""把当前任务的产物归档到 history/<视频名>/（页面「保存历史记录」）。

语义是 **移动**，不是复制 —— 归档后 output/ 只剩运行日志，可以直接跑下一个
视频。目录结构与 history/ 里已有的归档保持一致：

    history/<视频名>/
        <成品>.mp4  src.srt  trans.srt  *.xlsx ...
        log/        本次运行的日志与断点
        gpt_log/    LLM 原始响应缓存
        audio/      切分后的音频

`pipeline_latest.log` 是唯一的例外：它正被任务线程写着，移走会让日志句柄
失联（之后的行全丢）。所以复制一份过去，源文件留在 output/log/ —— 下次
运行时它会被自动归档成 pipeline_<时间戳>.log。

**output/ 目录本身永远保留**（只清空内容）：删掉它在 Windows 上很容易让
下游代码 / 资源管理器找不到目录，而清空内容已经足够跑下一个视频。

名字来源优先级：显式传入 > output/input_manifest.json > output/ 里的媒体文件。
"""

from __future__ import annotations

import os
import shutil
import time

try:
    from core.utils import rprint
except Exception:  # pragma: no cover
    rprint = print

LOG_DIR = "log"
GPT_LOG_DIR = "gpt_log"
LIVE_LOG = "pipeline_latest.log"   # 正在写的运行日志，只复制不移走
_INVALID = '<>:"/\\|?*'

# 源文件被读句柄占用（页面 <video> 流式预览 output_sub.mp4）时的删源重试。
# on_retry 由 HTTP 适配层传入「主动关闭预览句柄」的钩子：等浏览器自己断开
# 是不可靠的（Range 请求被取消时服务端句柄可能根本没关），每次重试前都
# 自己关一次才是 Windows 上唯一确定的做法。
_REMOVE_ATTEMPTS = 10
_REMOVE_DELAY = 0.5                # 总等待 ≈ 5s


def _remove_with_retry(src: str, on_retry=None) -> bool:
    """删除可能被读句柄占住的文件；每次失败前先调 on_retry 释放句柄。"""
    for i in range(_REMOVE_ATTEMPTS):
        try:
            os.remove(src)
            return True
        except PermissionError:
            if i < _REMOVE_ATTEMPTS - 1:
                if on_retry is not None:
                    try:
                        on_retry()
                    except Exception:                      # noqa: BLE001
                        pass                            # 释放钩子失败不改变归档主流程
                time.sleep(_REMOVE_DELAY)
    return False


def sanitize_name(name: str) -> str:
    """Windows 非法字符换成下划线（历史目录名要能直接双击打开）。"""
    for ch in _INVALID:
        name = name.replace(ch, "_")
    return name.strip(" .") or "untitled"


def _guess_name(workspace: str) -> str:
    from core.utils.media_files import find_media_file

    try:
        media, _ = find_media_file(workspace)
    except Exception as e:
        raise ValueError(
            f"{workspace}/ 里没有输入媒体，无法确定历史目录名（可显式指定 name）：{e}"
        ) from e
    return os.path.splitext(os.path.basename(str(media)))[0]


def _transfer(src: str, dst_dir: str, stats: dict, on_retry=None) -> None:
    """把 src（文件或目录）搬到 dst_dir，同名覆盖。"""
    target = os.path.join(dst_dir, sanitize_name(os.path.basename(src)))

    if os.path.isdir(src):
        os.makedirs(target, exist_ok=True)
        for sub in sorted(os.listdir(src)):
            _transfer(os.path.join(src, sub), target, stats, on_retry)
        try:
            os.rmdir(src)          # 只在搬空后才删得掉
        except OSError:
            pass
        return

    try:
        if os.path.exists(target):
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            else:
                os.remove(target)
            stats["overwritten"] += 1
        shutil.move(src, target)
        stats["moved"] += 1
    except PermissionError:
        # 被占用时的退化路径「复制 + 删源」。占用来源两类：
        # * 目标被占（杀软/播放器开着旧归档）→ 复制多半也失败，记 errors；
        # * 源被占（页面 <video> 正在流式预览 output_sub.mp4，服务端持读
        #   句柄，Windows 不许删/改名被占用的文件）→ 复制成功，删源重试
        #   时反复主动关闭预览句柄；仍失败则记入 locked 明确报告，绝不
        #   静默 pass（用户只看到"清空不了"）。
        try:
            shutil.copy2(src, target)
        except OSError as e:
            stats["errors"].append(f"{os.path.basename(src)}: {e}")
            return
        if _remove_with_retry(src, on_retry):
            stats["moved"] += 1
        else:
            stats["locked"].append(os.path.basename(src))
    except Exception as e:                                  # noqa: BLE001
        stats["errors"].append(f"{os.path.basename(src)}: {e}")


def archive_current(workspace: str = "output", history_dir: str = "history",
                    name: str | None = None, on_retry=None) -> dict:
    """归档当前任务。返回 {video, dest, moved, overwritten, locked, errors}。

    `on_retry` 是删源重试时调用的「释放文件占用」钩子（由 server 层传入
    `close_preview_streams`）；CLI / 批量路径不传也能正常工作。

    没有可归档的产物时抛 ValueError —— 页面上直接看到原因，而不是"成功了但
    什么都没发生"。
    """
    if not os.path.isdir(workspace):
        raise ValueError(f"没有可归档的产物：{workspace}/ 不存在")

    # 只剩运行日志 = 上次已经归档过，别让它报"找不到媒体"这种误导性错误
    if not [e for e in os.listdir(workspace) if e not in (LOG_DIR, GPT_LOG_DIR)]:
        raise ValueError(f"{workspace}/ 里没有可归档的产物（是不是已经保存过一次了？）")

    video = sanitize_name(name or _guess_name(workspace))
    dest = os.path.join(history_dir, video)
    log_dest = os.path.join(dest, LOG_DIR)
    gpt_dest = os.path.join(dest, GPT_LOG_DIR)

    # 同名目录已存在 = 同一个视频重跑，直接覆盖，不新建 xxx_2
    existed = os.path.isdir(dest) and bool(os.listdir(dest))
    for d in (dest, log_dest, gpt_dest):
        os.makedirs(d, exist_ok=True)

    stats = {"video": video, "dest": dest.replace("\\", "/"),
             "moved": 0, "overwritten": 0, "copied": 0,
             "locked": [], "errors": []}

    rprint(f"[cyan]📦 保存历史记录 → {stats['dest']}"
           f"{'（覆盖旧归档）' if existed else ''}[/cyan]")

    # 1) output/ 顶层：成品、字幕、表格、audio/ 等
    for entry in sorted(os.listdir(workspace)):
        if entry in (LOG_DIR, GPT_LOG_DIR):
            continue
        _transfer(os.path.join(workspace, entry), dest, stats, on_retry)

    # 2) 运行日志：pipeline_latest.log 只复制
    src_log = os.path.join(workspace, LOG_DIR)
    if os.path.isdir(src_log):
        for entry in sorted(os.listdir(src_log)):
            src = os.path.join(src_log, entry)
            if entry == LIVE_LOG:
                try:
                    shutil.copy2(src, os.path.join(log_dest, entry))
                    stats["copied"] += 1
                except OSError as e:
                    stats["errors"].append(f"{entry}: {e}")
                continue
            _transfer(src, log_dest, stats, on_retry)
        try:
            os.rmdir(src_log)          # pipeline_latest.log 还在 → 不会删成功
        except OSError:
            pass

    # 3) LLM 原始响应缓存
    src_gpt = os.path.join(workspace, GPT_LOG_DIR)
    if os.path.isdir(src_gpt):
        for entry in sorted(os.listdir(src_gpt)):
            _transfer(os.path.join(src_gpt, entry), gpt_dest, stats, on_retry)
        try:
            os.rmdir(src_gpt)
        except OSError:
            pass

    overwritten = f"（覆盖 {stats['overwritten']} 个旧文件）" if stats["overwritten"] else ""
    rprint(f"[green]✅ 已归档 {stats['moved']} 个文件{overwritten} → {stats['dest']}[/green]")

    # 目录保留、内容清空（pipeline_latest.log 正在写，只能留下它）
    os.makedirs(workspace, exist_ok=True)
    if os.path.isdir(os.path.join(workspace, LOG_DIR)) and os.listdir(os.path.join(workspace, LOG_DIR)):
        rprint(f"[dim]   {workspace}/{LOG_DIR}/{LIVE_LOG} 是本次运行日志，"
               f"已复制到归档目录；下次运行时会自动归档[/dim]")
    for err in stats["errors"]:
        rprint(f"[yellow]⚠️ 归档失败 {err}[/yellow]")
    if stats["locked"]:
        names = ", ".join(stats["locked"])
        rprint(f"[yellow]⚠️ {len(stats['locked'])} 个文件被占用，已复制到归档"
               f"但未能从 {workspace}/ 清出：{names}[/yellow]")
        rprint("[yellow]   预览读句柄已逐次主动关闭仍占用，多半是外部播放器/资源管理器"
               "打开了该文件；归档后会在后台继续尝试清出。[/yellow]")
    return stats


def retry_locked(workspace: str, names: list[str], on_retry=None,
                 attempts: int = 30, delay: float = 1.0,
                 release_rounds: int = 3) -> list[str]:
    """归档结束后在后台继续清出仍被占用的源文件，返回最后仍占用的文件名。

    两条安全线：
    * 只删 **归档时那份文件**（size + mtime_ns 签名一致）；用户在这期间
      下载/重跑产生的同名新产物绝不碰。
    * `on_retry`（主动关预览句柄）只在最初 `release_rounds` 轮调用，之后
      只试删除，避免打断用户随后正常打开的视频预览。
    """
    pending = {}
    for n in names:
        p = os.path.join(workspace, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        pending[p] = (st.st_size, st.st_mtime_ns)

    for i in range(max(0, attempts)):
        if not pending:
            return []
        time.sleep(delay)
        if on_retry is not None and i < release_rounds:
            try:
                on_retry()
            except Exception:                          # noqa: BLE001
                pass
        for p, sig in list(pending.items()):
            try:
                st = os.stat(p)
            except OSError:
                del pending[p]                        # 已被别处清出
                continue
            if (st.st_size, st.st_mtime_ns) != sig:
                del pending[p]                        # 新产物，不碰
                continue
            try:
                os.remove(p)
                del pending[p]
            except OSError:
                pass                                  # 还占着，下轮再试
    return [os.path.basename(p) for p in pending]
