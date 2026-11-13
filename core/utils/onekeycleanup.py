"""一键归档（旧 Streamlit 时代的入口）：把 output/ 的产物搬到 history/。

真正的实现在 core/services/archive_service.py —— 这里只是薄包装，避免
第二份"搬文件 + 判名字"的逻辑漂移。

注意：**output/ 目录本身不会被删除**（只清空内容）。删掉它在 Windows 上
很容易让后续代码 / 资源管理器找不到目录，而"清空内容"已经足够跑下一个视频。
"""

from core.services.archive_service import (  # noqa: F401  (re-export)
    archive_current,
    sanitize_name as sanitize_filename,
)


def cleanup(history_dir="history"):
    """归档当前 output/ 到 <history_dir>/<视频名>/，返回统计信息。"""
    return archive_current(workspace="output", history_dir=history_dir)


if __name__ == "__main__":
    print(cleanup())
