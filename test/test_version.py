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
        assert c.get("/api/version").json() == {"version": __version__}
        assert c.get("/api/pipeline").json()["version"] == __version__
