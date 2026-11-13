import os,sys
import json
import re
import subprocess
from core.utils import *
from core.utils.subprocess_log import run_logged
# 2026-09-08: media-file discovery moved to core/utils/media_files.py.
# This step file keeps only the download orchestration.
from core.utils.media_files import INPUT_MANIFEST, find_video_files

OUTPUT_DIR = "output"

def sanitize_filename(filename):
    # Remove or replace illegal characters
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # Ensure filename doesn't start or end with a dot or space
    filename = filename.strip('. ')
    # Use default name if filename is empty
    return filename if filename else 'video'

# Netscape cookie 文件里代表「已登录 Google 账号」的字段；只有匿名 cookie
# (PREF/YSC/VISITOR_INFO) 时 YouTube 依旧会弹 bot 挑战。
_LOGIN_COOKIE_KEYS = (
    "LOGIN_INFO", "__Secure-1PSID", "__Secure-3PSID",
    "SAPISID", "APISID", "HSID", "SSID", "SID",
)
# yt-dlp 触发 bot 挑战时的固定措辞（小写匹配）
_BOT_HINTS = ("sign in to confirm you", "not a bot")


def update_ytdlp():
    try:
        # 走 run_logged：pip 的输出原本只落在启动窗口，页面日志看不到
        run_logged([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"], check=True)
        if 'yt_dlp' in sys.modules:
            del sys.modules['yt_dlp']
        rprint("[green]yt-dlp updated[/green]")
    except subprocess.CalledProcessError as e:
        rprint(f"[yellow]Warning: Failed to update yt-dlp: {e}[/yellow]")
    from yt_dlp import YoutubeDL
    return YoutubeDL


def _cfg(key, default=""):
    """load_key 缺键会抛 KeyError；配置项允许缺失时走这里。"""
    try:
        value = load_key(key)
    except Exception:
        return default
    return default if value is None else value


def cookies_have_login(cookies_path) -> bool:
    """Cookie 文件是否带登录态（不含 -> 大概率仍被判为 bot）。"""
    if not cookies_path:
        return False
    try:
        with open(cookies_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return False
    return any(k in text for k in _LOGIN_COOKIE_KEYS)


def pick_js_runtime() -> list:
    """n-challenge 解密脚本来源。

    yt-dlp 的 remote_components 合法值只有 ejs:github（从 GitHub 拉
    yt-dlp-ejs 脚本，CLI 已验证可用）和 ejs:npm；ejs:deno / ejs:node 不是
    合法值——JS 运行时（deno/node/bun）yt-dlp 会自动探测，无需配置。
    """
    forced = str(_cfg("youtube.js_runtime") or "").strip().lower()
    if forced:
        return [f"ejs:{forced}"]
    return ["ejs:github"]


def build_ydl_opts(save_path='output', resolution='1080') -> dict:
    """组装 yt-dlp 参数（cookie 来源：文件 + 可选浏览器直取）。"""
    opts = {
        'format': 'bestvideo+bestaudio/best' if resolution == 'best' else f'bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]',
        'outtmpl': f'{save_path}/%(title)s.%(ext)s',
        'noplaylist': True,
        'writethumbnail': True,
        'merge_output_format': 'mp4',
        'postprocessors': [{'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'}],
        # 修复：正确启用远程组件来解决 YouTube n-challenge
        'remote_components': pick_js_runtime(),
    }

    # 1) 手工导出的 cookie 文件
    cookies_path = str(_cfg("youtube.cookies_path") or "")
    if cookies_path and os.path.exists(cookies_path):
        opts["cookiefile"] = cookies_path

    # 2) 直接从已登录的浏览器取 cookie（覆盖力最强，通常一次解决 bot 挑战）
    browser = str(_cfg("youtube.cookies_from_browser") or "").strip()
    if browser:
        opts["cookiesfrombrowser"] = (browser.lower(),)

    if opts.get("cookiefile") and not opts.get("cookiesfrombrowser"):
        if not cookies_have_login(opts["cookiefile"]):
            rprint(
                f"[yellow]⚠️ {opts['cookiefile']} 里没有登录态 cookie，YouTube 大概率仍会拒绝下载。"
                f"建议改用 youtube.cookies_from_browser，或用浏览器插件重新导出（需登录 YouTube 后再导）。[/yellow]"
            )
    return opts


def _bot_help(cookies_path: str) -> str:
    return (
        "❌ YouTube 要求登录验证（Sign in to confirm you're not a bot），匿名 cookie 过不了。\n"
        "解决办法二选一：\n"
        "  1) 从已登录浏览器直接取 cookie（推荐）：config.yaml 里设置\n"
        "       youtube:\n"
        "         cookies_from_browser: chrome     # 也可填 edge / firefox / brave\n"
        "     保存后重启服务再下载。\n"
        f"  2) 手动导出 cookie 文件覆盖 {cookies_path or 'cookies.txt'}：\n"
        "     浏览器登录 youtube.com → 用插件（如 Get cookies.txt LOCALLY）导出为 Netscape 格式。\n"
        "     导出后文件里应能看到 LOGIN_INFO / SID / SAPISID 等字段。"
    )


def probe_cookies() -> dict:
    """体检当前 cookie 来源（文件 + 浏览器），供 UI/接口提前暴露 bot 风险。

    只读，不联网；浏览器读取失败只记原因、不抛。
    """
    cookies_path = str(_cfg("youtube.cookies_path") or "")
    browser = str(_cfg("youtube.cookies_from_browser") or "").strip().lower()
    info = {
        "cookies_path": cookies_path or None,
        "cookies_file_exists": bool(cookies_path and os.path.exists(cookies_path)),
        "cookies_have_login": cookies_have_login(cookies_path),
        "browser": browser or None,
        "browser_ok": None,
        "browser_error": None,
        "browser_have_login": None,
        "verdict": "unknown",
        "hint": None,
    }

    if browser:
        try:
            from yt_dlp.cookies import extract_cookies_from_browser
            names = {c.name for c in extract_cookies_from_browser(browser)}
            info["browser_ok"] = True
            info["browser_have_login"] = bool(names & set(_LOGIN_COOKIE_KEYS))
        except Exception as e:                                    # noqa: BLE001
            info["browser_ok"] = False
            info["browser_error"] = f"{type(e).__name__}: {e}"

    if info["cookies_have_login"] or info["browser_have_login"]:
        info["verdict"] = "ok"
    elif info["browser_ok"] is False:
        info["verdict"] = "browser-unavailable"
        info["hint"] = (
            f"无法从 {browser} 读取 cookie（{info['browser_error']}）。"
            "浏览器未安装、正在运行被锁、或未在该浏览器登录 YouTube 都会导致这个结果。"
        )
    else:
        info["verdict"] = "anonymous"
        info["hint"] = "没有登录态 cookie，YouTube 大概率报 “Sign in to confirm you’re not a bot”。"
    return info


def _save_video_meta(save_path, info):
    """YouTube 元数据（标题/简介/频道）→ output/video_meta.json。

    B站投稿文案功能用它当参考输入；本地文件场景没有它（由服务层从
    文件名兜底）。保存失败绝不影响下载本身。
    """
    import json
    import time
    if not isinstance(info, dict):
        return
    meta = {
        "title": info.get("title"),
        "description": info.get("description") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "channel": info.get("channel") or "",
        "webpage_url": info.get("webpage_url") or "",
        "duration": info.get("duration"),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        with open(os.path.join(save_path, "video_meta.json"), "w",
                  encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        rprint("[green]📦 视频元数据已保存 → video_meta.json[/green]")
    except OSError as e:
        rprint(f"[yellow]⚠️ 元数据保存失败（不影响下载）: {e}[/yellow]")


def _download_once(YoutubeDL, ydl_opts, url):
    with YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=True)


def download_video_ytdlp(url, save_path='output', resolution='1080'):
    os.makedirs(save_path, exist_ok=True)
    ydl_opts = build_ydl_opts(save_path, resolution)

    # Get YoutubeDL class after updating
    YoutubeDL = update_ytdlp()
    from yt_dlp.utils import DownloadError

    try:
        info = _download_once(YoutubeDL, ydl_opts, url)
    except DownloadError as e:
        msg = str(e)
        low = msg.lower()
        if any(h in low for h in _BOT_HINTS):
            raise RuntimeError(_bot_help(ydl_opts.get("cookiefile", ""))) from e
        # 浏览器 cookie 取不到（未安装 / 运行被锁 / DPAPI 解密失败——Edge/Chrome
        # 127+ 的 app-bound 加密在普通权限下读不了）时，退回文件 cookie 再试一次。
        # 注意 DPAPI 错误信息里没有 "cookies" 字样，别只盯一个词。
        if ydl_opts.get("cookiesfrombrowser") and ("cookies" in low or "dpapi" in low):
            rprint(
                f"[yellow]⚠️ 从 {ydl_opts['cookiesfrombrowser'][0]} 读取 cookie 失败（浏览器未安装或正在运行？），"
                f"回退到 cookie 文件重试。原始错误：{msg}[/yellow]"
            )
            ydl_opts.pop("cookiesfrombrowser")
            info = _download_once(YoutubeDL, ydl_opts, url)
        else:
            raise

    # 顺手存下 YouTube 原标题/简介/频道，供 B站投稿文案等功能参考
    _save_video_meta(save_path, info)

    # Check and rename files after download
    for file in os.listdir(save_path):
        if os.path.isfile(os.path.join(save_path, file)):
            filename, ext = os.path.splitext(file)
            new_filename = sanitize_filename(filename)
            if new_filename != filename:
                os.rename(os.path.join(save_path, file), os.path.join(save_path, new_filename + ext))
    media_file = find_video_files(save_path)
    write_input_manifest(media_file, "video", save_path)

def write_input_manifest(media_file: str, media_type: str, save_path='output'):
    os.makedirs(save_path, exist_ok=True)
    manifest_path = os.path.join(save_path, INPUT_MANIFEST)
    media_path = media_file.replace("\\", "/") if sys.platform.startswith('win') else media_file
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"path": media_path, "type": media_type}, f, ensure_ascii=False, indent=2)

if __name__ == '__main__':
    # Example usage
    url = input('Please enter the URL of the video you want to download: ')
    resolution = input('Please enter the desired resolution (360/480/720/1080, default 1080): ')
    resolution = int(resolution) if resolution.isdigit() else 1080
    download_video_ytdlp(url, resolution=resolution)
    print(f"🎥 Video has been downloaded to {find_video_files()}")
