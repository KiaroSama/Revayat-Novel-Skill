"""Tool discovery must honor virtual environments and numeric install versions."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

import extract

LOG = logging.getLogger(__name__)


def test_console_scripts_remain_beside_the_invoked_interpreter(tmp_path, monkeypatch):
    env_bin = tmp_path / "venv" / "bin"
    env_bin.mkdir(parents=True)
    invoked = env_bin / "python"
    monkeypatch.setattr(extract.sys, "executable", str(invoked))
    original = Path.resolve

    def resolving(path, *args, **kwargs):
        # Virtual environments may link their interpreter to a system binary.
        if path == invoked:
            return tmp_path / "system" / "python"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolving)
    (env_bin / "tesseract").write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(extract.shutil, "which", lambda _: None)
    assert extract._interpreter_scripts() == env_bin
    assert extract.find_tool(["tesseract"]) == str(env_bin / "tesseract")
    LOG.debug("Verified discovery without resolving away the virtual environment")


@pytest.mark.parametrize("versions,expected", [
    (["gs10.9.0", "gs10.10.0"], "gs10.10.0"),
    (["gs9.56.1", "gs10.2.0"], "gs10.2.0"),
    (["gs10.07.9", "gs10.07.10"], "gs10.07.10"),
])
def test_versioned_install_candidates_are_numeric(tmp_path, monkeypatch, versions, expected):
    for version in versions:
        path = tmp_path / version / "gswin64c.exe"
        path.parent.mkdir()
        path.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(extract.shutil, "which", lambda _: None)
    monkeypatch.setattr(extract, "_interpreter_scripts", lambda: tmp_path / "no-bin")
    monkeypatch.setattr(extract, "BUNDLED_TOOLS", {"ghostscript": (str(tmp_path / "gs*" / "gswin64c.exe"),)})
    assert Path(extract.find_tool(["gs"], "ghostscript")).parent.name == expected


def test_ocr_path_keeps_the_discovered_command_alias(tmp_path, monkeypatch):
    alias = tmp_path / "scripts" / "tesseract"
    monkeypatch.setattr(extract, "find_tool", lambda names, label: str(alias) if label == "tesseract" else None)
    original = Path.resolve

    def resolving(path, *args, **kwargs):
        return tmp_path / "actual" / "versioned-worker" if path == alias else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolving)
    environment = extract.ocr_environment({"PATH": "original-path"})
    assert environment["PATH"].split(extract.os.pathsep)[0] == str(alias.parent)
    assert environment["PATH"].endswith("original-path")
