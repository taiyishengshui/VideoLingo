"""Version single source: version.py -> /api/version, /api/pipeline, frontend package."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_semver_starting_at_3():
    from version import __version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)
    assert tuple(int(x) for x in __version__.split(".")) >= (3, 0, 0)


def test_frontend_package_matches_backend_version():
    from version import __version__
    pkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == __version__


def test_setup_reads_version_from_single_source():
    text = (ROOT / "setup.py").read_text(encoding="utf-8")
    assert "from version import __version__" in text
    assert not re.search(r"VERSION\s*=\s*['\"]\d", text)


def test_version_endpoints(cfg_path, tmp_cwd):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import server.app as app_mod
    from version import __version__

    cfg_path()
    with TestClient(app_mod.create_app()) as c:
        body = c.get("/api/version").json()
        assert body["version"] == __version__
        assert set(body["build"]) == {"hash", "committed_at", "dirty"}
        assert isinstance(body["build"]["dirty"], bool)
        assert c.get("/api/pipeline").json()["version"] == __version__


def test_version_module_has_no_import_side_effects():
    """setup.py / launch.py import version.py — that must never spawn git."""
    import ast
    text = (ROOT / "version.py").read_text(encoding="utf-8")
    top_calls = [
        node for node in ast.parse(text).body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    assert not top_calls, "version.py must not call anything at import time"


def test_build_info_without_git_is_unknown(monkeypatch):
    import version
    monkeypatch.setattr(version.os.path, "isdir", lambda p: False)
    info = version.build_info()
    assert info == {"hash": "unknown", "committed_at": "unknown", "dirty": False}


def test_build_info_git_failure_is_unknown(monkeypatch):
    import version
    monkeypatch.setattr(version.os.path, "isdir", lambda p: True)
    monkeypatch.setattr(version, "_git", lambda *a, **k: None)
    assert version.build_info()["hash"] == "unknown"


def test_build_info_reports_commit_and_dirty(monkeypatch):
    import version
    monkeypatch.setattr(version.os.path, "isdir", lambda p: True)
    when = "2026-11-16 12:21:36 +0800"

    def fake_git(args, root):
        if args[0] == "rev-parse":
            return "3093955"
        if args[0] == "show":
            return when
        return " M PRD.md"

    monkeypatch.setattr(version, "_git", fake_git)
    assert version.build_info() == {
        "hash": "3093955", "committed_at": when, "dirty": True
    }
