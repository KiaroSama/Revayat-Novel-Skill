"""Book-processing guidance remains usable in the real installed payload."""

import os
from pathlib import Path
import re
import sys

import bookir as ir
from test_installers import _bash, _pwsh

ROOT = Path(__file__).resolve().parents[1]


def test_project_install_contains_native_document_and_opt_in_workflows(tmp_path):
    project = tmp_path / "translation project"
    project.mkdir()
    if os.name == "nt":
        command = [_pwsh(), "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                   str(ROOT / "install/install.ps1"), "-Agent", "claude", "-Scope",
                   "project", "-Path", str(project), "-Force"]
    else:
        command = [_bash(), str(ROOT / "install/install.sh"), "--agent", "claude",
                   "--scope", "project", "--path", str(project), "--force"]
    assert command[0], "the CI installer lane requires its native shell"
    installed = ir.run_bounded(command, 120)
    assert installed.returncode == 0, installed.stderr.decode("utf-8")
    skill = project / ".claude/skills/revayat-novel"
    entry = (skill / "SKILL.md").read_text(encoding="utf-8")
    for name in ("native-docx.md", "native-pdf.md", "parallel-work.md", "web-novels.md",
                 "book-preflight.md"):
        assert f"references/{name}" in entry
        guide = skill / "references" / name
        assert guide.is_file()
        for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", guide.read_text(encoding="utf-8")):
            if "://" not in link:
                assert (guide.parent / link).resolve().is_file(), link
    parallel = (skill / "references/parallel-work.md").read_text(encoding="utf-8")
    assert "No answer is not consent" in parallel
    assert "coordinator alone" in entry
    # The copied command resolves its own helpers, independent of the checkout.
    command = ir.run_bounded([sys.executable, str(skill / "scripts/revayat-novel.py"),
                              "web-import", "--help"], 20)
    assert command.returncode == 0
    assert b"--manifest" in command.stdout and b"--resume" in command.stdout
