"""History archive endpoints — 「保存历史记录」按钮的 HTTP 面。

典型的收尾动作：一条视频跑完 → 点一下 → output/ 里的成品/字幕/表格/日志
全部搬到 history/<视频名>/，output/ 目录保留但内容清空，可以接着跑下一个。

搬的可能有几百 MB 的 mp4，必须走任务线程（MANAGER.start_job）：
放在请求里同步做会让 HTTP 卡住甚至超时，而且页面也看不到任何进度。

薄适配器：校验 + HTTP 包装，真正的搬移在 core.services.archive_service。
"""

from __future__ import annotations

import threading

from fastapi import APIRouter, Body, HTTPException

from core.services.archive_service import archive_current, retry_locked
from server.logbus import BUS
from server.preview_stream import close_preview_streams
from server.task_manager import MANAGER

router = APIRouter(prefix="/api/history", tags=["history"])

_SAVE_PATH = "output"
_HISTORY_DIR = "history"


def _cleanup_locked(names: list[str]) -> None:
    """still-locked 残留的后台清出：不必让用户刷新页面或再点一次保存。"""
    left = retry_locked(_SAVE_PATH, names, on_retry=close_preview_streams)
    BUS.publish({"type": "history_cleaned", "locked": len(left)})
    BUS.publish({"type": "log", "line": (
        "残留文件已自动清出，output/ 已清空" if not left else
        f"仍有 {len(left)} 个文件被占用（多为外部播放器）：{', '.join(left)}；"
        "请关闭占用程序后再点一次「保存历史」")})


@router.post("/save")
def save_history(payload: dict | None = Body(None)):
    """把当前 output/ 归档到 history/<视频名>/（异步，日志走 WebSocket）。"""
    name = (payload or {}).get("name")
    if name is not None:
        name = str(name).strip() or None

    def job():
        # 归档必须自己把预览读句柄关掉，不能寄希望于浏览器「已经取消请求」：
        # Windows 上只要有一个句柄没关，output_sub.mp4 就删不掉。删源重试时
        # 再逐次关闭（on_retry），覆盖归档期间页面重新发起的 Range 请求。
        released = close_preview_streams()
        if released:
            BUS.publish({"type": "log",
                         "line": f"保存历史：已主动关闭 {released} 个视频预览读句柄"})
        stats = archive_current(workspace=_SAVE_PATH, history_dir=_HISTORY_DIR,
                                name=name, on_retry=close_preview_streams)
        locked = list(stats.get("locked") or [])
        BUS.publish({
            "type": "history_saved",
            "video": stats["video"],
            "dest": stats["dest"],
            "moved": stats["moved"],
            "locked": len(locked),
        })
        if locked:
            # 已经复制进归档，只剩把源清出 output/：后台继续等占用解除，
            # 用户不用刷新页面、也不用再点一次保存。
            threading.Thread(target=_cleanup_locked, args=(locked,),
                             daemon=True, name="history-locked-cleanup").start()

    ok, message = MANAGER.start_job("保存历史记录", job)
    if not ok:
        # 任务正在跑/暂停时不允许归档：产物还在被写，搬一半会两败俱伤
        raise HTTPException(409, f"当前有任务在运行，先停止再保存历史记录（{message}）")
    return {"ok": True, "message": message}
