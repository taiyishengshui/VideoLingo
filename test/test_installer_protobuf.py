"""Regression guard for the protobuf 3.19.6 / opentelemetry-proto conflict.

Context: pyannote-audio>=4 pulls opentelemetry-proto (needs protobuf>=5),
while descript-audiotools==0.7.2 pins protobuf<3.20. pip leaves 3.19.6 and
whisperx.load_model crashes with "cannot import name 'builder' from
google.protobuf.internal". installer.py now aligns protobuf after base install
and health_check detects the broken state.
"""
import installer
from installer import ensure_protobuf, health_check, protobuf_version_ok


def _stub_version(monkeypatch, version):
    monkeypatch.setattr(installer, "package_version",
                        lambda name: version if name == "protobuf" else None)


def test_protobuf_version_ok(monkeypatch):
    for version, expected in [
        (None, False),
        ("3.19.6", False),
        ("4.25.8", False),
        ("5.29.6", True),
        ("6.31.0", True),
        ("not-a-version", False),
    ]:
        _stub_version(monkeypatch, version)
        assert protobuf_version_ok() is expected, version


def test_ensure_protobuf_upgrades_old_version_with_no_deps(monkeypatch):
    calls = []
    _stub_version(monkeypatch, "3.19.6")
    monkeypatch.setattr(installer, "pip_install",
                        lambda packages, **kw: calls.append((packages, kw)))

    ensure_protobuf()

    assert len(calls) == 1
    packages, kw = calls[0]
    assert packages == [f"protobuf=={installer.PROTOBUF_VERSION}"]
    assert kw.get("extra_args") == ["--no-deps"], "must not touch other packages"


def test_ensure_protobuf_is_noop_when_healthy(monkeypatch):
    calls = []
    _stub_version(monkeypatch, "5.29.6")
    monkeypatch.setattr(installer, "pip_install",
                        lambda packages, **kw: calls.append((packages, kw)))

    ensure_protobuf()

    assert calls == []


def _stub_health_env(monkeypatch, versions):
    monkeypatch.setattr(installer, "load_state",
                        lambda: {"requirements_hash": installer.requirements_hash()})
    monkeypatch.setattr(installer, "package_version", lambda name: versions.get(name))
    monkeypatch.setattr(installer.shutil, "which", lambda cmd: "C:/ffmpeg/bin/ffmpeg.exe")
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")


def test_health_check_detects_stale_protobuf(monkeypatch):
    versions = {
        "streamlit": "1.49.1", "openai": "2.49.0", "pandas": "2.3.2",
        "torch": "2.8.0", "torchaudio": "2.8.0", "spacy": "3.8.7",
        "whisperx": "3.8.1", "demucs": "4.0.1", "protobuf": "5.29.6",
    }
    _stub_health_env(monkeypatch, versions)
    assert health_check(quiet=True) == 0

    versions["protobuf"] = "3.19.6"
    assert health_check(quiet=True) == 1
