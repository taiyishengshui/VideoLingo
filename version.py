"""VideoLingo version information.

``__version__`` is the product semver and stays static (setup.py, launch.py and
frontend/package.json all read it). ``build_info()`` is the code-freshness
marker the user actually wants: it reports the current git commit so the page
can prove which checkout is running (FR-37 / v1.39).

Importing this module has ZERO side effects — git is probed only when
build_info() is explicitly called.
"""
from __future__ import annotations

import os
import subprocess

__version__ = "3.0.0"


def _git(args, root):
    """Run one read-only git command; never raise, never hang."""
    try:
        out = subprocess.run(
            ["git", "-C", root, *args],
            capture_output=True, text=True, timeout=2, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def build_info():
    """THE single git build marker. Safe to call without git installed."""
    root = os.path.dirname(os.path.abspath(__file__))
    unknown = {"hash": "unknown", "committed_at": "unknown", "dirty": False}
    if not os.path.isdir(os.path.join(root, ".git")):
        return unknown
    short = _git(["rev-parse", "--short", "HEAD"], root)
    if not short:
        return unknown
    committed = _git(["show", "-s", "--format=%ci", "HEAD"], root) or "unknown"
    return {
        "hash": short,
        "committed_at": committed,
        "dirty": bool(_git(["status", "--porcelain"], root)),
    }
