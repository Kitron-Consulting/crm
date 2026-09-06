"""Tests for update._binary_path — especially the PyApp path resolution that
previously overwrote the inner unpacked console script instead of the real
launcher, reporting success while leaving the binary stale.
"""

import os
import sys

from crm import update


def _make_exe(path):
    path.write_text("#!/bin/sh\n")
    os.chmod(path, 0o755)


def test_pyapp_skips_inner_bindir_and_finds_outer_launcher(monkeypatch, tmp_path):
    # Simulate PyApp: interpreter lives in the unpacked venv bin dir, which is
    # also prepended to PATH and contains an inner `crm` console script.
    inner = tmp_path / "pyapp" / "crm" / "1.6.1" / "python" / "bin"
    outer = tmp_path / "usr" / "local" / "bin"
    inner.mkdir(parents=True)
    outer.mkdir(parents=True)
    _make_exe(inner / "crm")   # the trap: PATH-first, but must NOT be chosen
    _make_exe(inner / "python3")
    _make_exe(outer / "crm")   # the real launcher

    monkeypatch.setenv("PYAPP", "1")
    monkeypatch.setenv("PATH", os.pathsep.join([str(inner), str(outer)]))
    monkeypatch.setattr(sys, "executable", str(inner / "python3"))
    # PyApp sets argv[0] to "-c"
    monkeypatch.setattr(sys, "argv", ["-c", "update"])

    assert update._binary_path() == str(outer / "crm")


def test_pyapp_returns_none_when_no_outer_launcher(monkeypatch, tmp_path):
    inner = tmp_path / "python" / "bin"
    inner.mkdir(parents=True)
    _make_exe(inner / "crm")
    _make_exe(inner / "python3")

    monkeypatch.setenv("PYAPP", "1")
    monkeypatch.setenv("PATH", str(inner))  # only the inner dir on PATH
    monkeypatch.setattr(sys, "executable", str(inner / "python3"))
    monkeypatch.setattr(sys, "argv", ["-c", "update"])

    assert update._binary_path() is None


def test_non_pyapp_plain_binary_uses_argv0(monkeypatch, tmp_path):
    binary = tmp_path / "crm"
    _make_exe(binary)
    monkeypatch.delenv("PYAPP", raising=False)
    monkeypatch.setattr(sys, "argv", [str(binary)])
    assert update._binary_path() == str(binary)


def test_non_pyapp_source_run_returns_none(monkeypatch, tmp_path):
    src = tmp_path / "__main__.py"
    src.write_text("")
    monkeypatch.delenv("PYAPP", raising=False)
    monkeypatch.setattr(sys, "argv", [str(src)])
    assert update._binary_path() is None
