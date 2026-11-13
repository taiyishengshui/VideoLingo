"""第 1 步下载的 cookie 处理：浏览器直取、登录态检测、bot 挑战可读化。

不联网、不依赖真实 yt-dlp：`yt_dlp` 用假模块顶替（真包在 CI/本机未必装），
`update_ytdlp` 换成返回假 YoutubeDL 的工厂，下载行为由异常脚本驱动。
"""

from __future__ import annotations

import sys
import types

import pytest

from core import _1_ytdlp as yd

ANON_COOKIES = """# Netscape HTTP Cookie File
.youtube.com\tTRUE\t/\tFALSE\t0\tPREF\thl=en
.youtube.com\tTRUE\t/\tTRUE\t0\tYSC\t9KfGPOJklZo
.youtube.com\tTRUE\t/\tTRUE\t1808196080\tVISITOR_INFO1_LIVE\tD8cpMZkgtSM
"""

LOGIN_COOKIES = ANON_COOKIES + (
    ".youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN_INFO\tAFmmF2swRQIg\n"
    ".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\t1Pz/AABBCC\n"
)


def _install_fake_yt_dlp(monkeypatch):
    """注入 yt_dlp / yt_dlp.utils，让函数内的 `from yt_dlp.utils import
    DownloadError` 成立，并返回该 DownloadError 类供测试抛出。"""
    yt = types.ModuleType("yt_dlp")
    utils = types.ModuleType("yt_dlp.utils")

    class DownloadError(Exception):
        pass

    utils.DownloadError = DownloadError
    yt.utils = utils
    monkeypatch.setitem(sys.modules, "yt_dlp", yt)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", utils)
    return DownloadError


class FakeYDL:
    """按脚本依次抛出/成功的 YoutubeDL 替身；记录收到的 params。"""

    instances: list = []

    def __init__(self, params, script=None):
        # 必须拷贝：回退逻辑会就地 pop("cookiesfrombrowser")，共享同一个 dict
        # 会让第一次实例的 params 也"看不见"该键，断言就假通过了。
        self.params = dict(params)
        self.script = script or []
        FakeYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        if not self.script:
            return None
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def extract_info(self, url, download=True):
        # download_video_ytdlp 用 extract_info(download=True) 顺带拿视频元数据，
        # 脚本驱动逻辑与 download 一致
        return self.download([url])


@pytest.fixture(autouse=True)
def _reset_instances(tmp_path, monkeypatch):
    # Successful fake downloads also write input_manifest.json; keep them away
    # from the user's real output/ just like the fake cookie/config files.
    monkeypatch.chdir(tmp_path)
    FakeYDL.instances.clear()


@pytest.fixture
def fake_ydl(monkeypatch):
    """把 update_ytdlp 换成返回 FakeYDL 的工厂（跳过 pip 升级与真实下载）。"""
    def _install(script):
        monkeypatch.setattr(yd, "update_ytdlp", lambda: lambda params: FakeYDL(params, script))
        monkeypatch.setattr(yd, "find_video_files", lambda save_path="output": "output/x.mp4")
        return script

    return _install


@pytest.fixture
def cookie_file(tmp_path):
    def _write(text: str) -> str:
        p = tmp_path / "cookies.txt"
        p.write_text(text, encoding="utf-8")
        return str(p)

    return _write


@pytest.fixture
def ytb_cfg(cfg_path, cookie_file):
    def _set(browser: str | None = None, cookies: str | None = None):
        if cookies is None:
            cookies = cookie_file(ANON_COOKIES)
        text = f"youtube:\n  cookies_path: '{cookies}'\n"
        if browser:
            text += f"  cookies_from_browser: '{browser}'\n"
        cfg_path(text)
        return cookies

    return _set


# ------------------------------------------------------------ 登录态检测

def test_anon_cookies_have_no_login(cookie_file):
    assert yd.cookies_have_login(cookie_file(ANON_COOKIES)) is False


def test_login_cookies_detected(cookie_file):
    assert yd.cookies_have_login(cookie_file(LOGIN_COOKIES)) is True


@pytest.mark.parametrize("bad", ["", None, "no/such/file.txt"])
def test_cookies_have_login_safe_on_missing(bad):
    assert yd.cookies_have_login(bad) is False


# ------------------------------------------------------------ 参数构造

def test_cookiefile_used_when_exists(ytb_cfg):
    path = ytb_cfg()
    opts = yd.build_ydl_opts("output", "1080")
    assert opts["cookiefile"] == path
    assert "cookiesfrombrowser" not in opts


def test_missing_cookiefile_ignored(ytb_cfg, tmp_path):
    ytb_cfg(cookies=str(tmp_path / "nope.txt"))
    opts = yd.build_ydl_opts()
    assert "cookiefile" not in opts


def test_browser_cookies_enabled_and_normalized(ytb_cfg):
    ytb_cfg(browser="Chrome")
    opts = yd.build_ydl_opts()
    assert opts["cookiesfrombrowser"] == ("chrome",)


def test_browser_and_file_can_coexist(ytb_cfg):
    path = ytb_cfg(browser="edge")
    opts = yd.build_ydl_opts()
    assert opts["cookiefile"] == path
    assert opts["cookiesfrombrowser"] == ("edge",)


def test_js_runtime_defaults_to_github(ytb_cfg):
    """remote_components 合法值只有 ejs:github / ejs:npm；默认 github。

    ejs:deno / ejs:node 不是合法值（JS 运行时 yt-dlp 自动探测），曾因
    'WARNING: Ignoring unsupported remote component(s): ejs:deno' 被踩。
    """
    ytb_cfg()
    assert yd.pick_js_runtime() == ["ejs:github"]


def test_js_runtime_can_be_forced(ytb_cfg, cfg_path):
    cfg_path("youtube:\n  js_runtime: 'npm'\n")
    assert yd.pick_js_runtime() == ["ejs:npm"]


def test_js_runtime_wired_into_opts(ytb_cfg):
    ytb_cfg()
    assert yd.build_ydl_opts()["remote_components"] == ["ejs:github"]


def test_resolution_best_uses_best_format(ytb_cfg):
    ytb_cfg()
    assert yd.build_ydl_opts(resolution="best")["format"] == "bestvideo+bestaudio/best"
    assert "height<=720" in yd.build_ydl_opts(resolution="720")["format"]


# ------------------------------------------------------------ bot 挑战

def test_bot_challenge_raises_readable_hint(monkeypatch, fake_ydl, ytb_cfg):
    path = ytb_cfg()
    DownloadError = _install_fake_yt_dlp(monkeypatch)
    fake_ydl([DownloadError("ERROR: [youtube] abc: Sign in to confirm you’re not a bot.")])

    with pytest.raises(RuntimeError) as ei:
        yd.download_video_ytdlp("https://youtu.be/abc", save_path="output")

    msg = str(ei.value)
    assert "Sign in to confirm" in msg
    assert "cookies_from_browser" in msg
    assert path in msg            # 提示里带上当前 cookie 文件路径
    assert len(FakeYDL.instances) == 1   # bot 挑战不重试，直接给方案


def test_browser_load_failure_falls_back_to_cookiefile(monkeypatch, fake_ydl, ytb_cfg):
    ytb_cfg(browser="chrome")
    DownloadError = _install_fake_yt_dlp(monkeypatch)
    fake_ydl([
        DownloadError("ERROR: Could not load cookies from chrome: database is locked"),
        None,                      # 第二次（去掉 cookiesfrombrowser）成功
    ])

    yd.download_video_ytdlp("https://youtu.be/abc", save_path="output")

    assert len(FakeYDL.instances) == 2
    assert "cookiesfrombrowser" in FakeYDL.instances[0].params
    assert "cookiesfrombrowser" not in FakeYDL.instances[1].params
    assert FakeYDL.instances[1].params["cookiefile"]     # 回退后仍用 cookie 文件


def test_dpapi_failure_falls_back_to_cookiefile(monkeypatch, fake_ydl, ytb_cfg):
    """Edge/Chrome 127+ 的 app-bound 加密 -> 'Failed to decrypt with DPAPI'。

    该错误信息里没有 'cookies' 字样，曾经因此漏判、没走回退直接炸。
    """
    ytb_cfg(browser="edge")
    DownloadError = _install_fake_yt_dlp(monkeypatch)
    fake_ydl([
        DownloadError("ERROR: Failed to decrypt with DPAPI. See yt-dlp#10927"),
        None,
    ])

    yd.download_video_ytdlp("https://youtu.be/abc", save_path="output")

    assert len(FakeYDL.instances) == 2
    assert "cookiesfrombrowser" not in FakeYDL.instances[1].params


def test_other_download_error_propagates(monkeypatch, fake_ydl, ytb_cfg):
    ytb_cfg()
    DownloadError = _install_fake_yt_dlp(monkeypatch)
    fake_ydl([DownloadError("ERROR: unable to download video data: HTTP 404")])

    with pytest.raises(DownloadError):
        yd.download_video_ytdlp("https://youtu.be/abc", save_path="output")
    assert len(FakeYDL.instances) == 1


# ------------------------------------------------------------ cookie 体检

def _install_browser_cookies(monkeypatch, names=(), exc=None):
    """假 yt_dlp.cookies.extract_cookies_from_browser：返回给定 cookie 名或抛错。"""
    pkg = types.ModuleType("yt_dlp")
    cookies_mod = types.ModuleType("yt_dlp.cookies")

    def _extract(browser, *a, **kw):
        if exc:
            raise exc
        return [types.SimpleNamespace(name=n) for n in names]

    cookies_mod.extract_cookies_from_browser = _extract
    pkg.cookies = cookies_mod
    monkeypatch.setitem(sys.modules, "yt_dlp", pkg)
    monkeypatch.setitem(sys.modules, "yt_dlp.cookies", cookies_mod)


def test_probe_ok_when_cookiefile_has_login(ytb_cfg, cookie_file):
    ytb_cfg(cookies=cookie_file(LOGIN_COOKIES))
    info = yd.probe_cookies()
    assert info["verdict"] == "ok"
    assert info["cookies_have_login"] is True
    assert info["browser"] is None


def test_probe_ok_when_browser_has_login(monkeypatch, ytb_cfg):
    ytb_cfg(browser="edge")
    _install_browser_cookies(monkeypatch, names=["LOGIN_INFO", "SAPISID"])
    info = yd.probe_cookies()
    assert info["verdict"] == "ok"
    assert info["browser_ok"] is True
    assert info["browser_have_login"] is True


def test_probe_anonymous_when_only_visitor_cookies(monkeypatch, ytb_cfg):
    ytb_cfg(browser="edge")
    _install_browser_cookies(monkeypatch, names=["PREF", "YSC", "VISITOR_INFO1_LIVE"])
    info = yd.probe_cookies()
    assert info["verdict"] == "anonymous"
    assert "Sign in to confirm" in info["hint"]


def test_probe_reports_browser_unavailable(monkeypatch, ytb_cfg):
    ytb_cfg(browser="chrome")
    _install_browser_cookies(monkeypatch, exc=RuntimeError("database is locked"))
    info = yd.probe_cookies()
    assert info["verdict"] == "browser-unavailable"
    assert "chrome" in info["hint"]
    assert "database is locked" in info["browser_error"]


def test_probe_safe_without_youtube_config(cfg_path):
    cfg_path("language_split_with_space: []\n")     # 完全没有 youtube 段
    info = yd.probe_cookies()
    assert info["cookies_path"] is None
    assert info["verdict"] in ("anonymous", "browser-unavailable")
