"""The installers put the skill where each agent actually looks.

One agent does not look where the others do. OpenCode reads *project* skills
from `.opencode/skills` but *user* skills from `~/.config/opencode/skills` — and
both installers derived the user path from the project folder name, landing in
`~/.opencode/skills`, a directory OpenCode never reads. A user who ran
`--scope user --agent opencode` got a success message and no skill.

Verified against opencode.ai/docs/skills, not inferred from the layout. The
tests below run the real installers under a throwaway HOME so the fixture is the
installer's own behaviour, not a reading of its source.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SH = ROOT / "install" / "install.sh"
PS1 = ROOT / "install" / "install.ps1"
SKILL = "revayat-novel"

#: A bounded wall for a subprocess that only copies a directory. If it takes
#: longer than this it is hung on a prompt, and the test should say so rather
#: than wait for the suite timeout.
INSTALL_TIMEOUT = 120


def _fake_home(tmp_path: Path) -> dict[str, str]:
    """An environment whose HOME is a throwaway, on every platform's spelling."""
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)          # what pwsh derives $HOME from on Windows
    env["HOMEDRIVE"], env["HOMEPATH"] = os.path.splitdrive(str(home))
    return env


def _bash() -> str | None:
    return shutil.which("bash")


def _pwsh() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_bash() is None, reason="no bash on this machine")
def test_the_shell_installer_puts_a_user_scope_opencode_skill_where_opencode_looks(
        tmp_path):
    env = _fake_home(tmp_path)
    home = Path(env["HOME"])

    done = subprocess.run(
        [_bash(), str(SH), "--scope", "user", "--agent", "opencode", "--force"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        timeout=INSTALL_TIMEOUT, stdin=subprocess.DEVNULL,
    )
    assert done.returncode == 0, done.stdout + done.stderr

    right = home / ".config" / "opencode" / "skills" / SKILL / "SKILL.md"
    wrong = home / ".opencode" / "skills" / SKILL
    assert right.exists(), (
        f"the skill did not land in ~/.config/opencode/skills:\n{done.stdout}")
    assert not wrong.exists(), (
        "the skill was also (or instead) put in ~/.opencode/skills, where "
        "OpenCode never looks")


@pytest.mark.skipif(_bash() is None, reason="no bash on this machine")
def test_the_shell_installer_keeps_project_scope_opencode_in_dot_opencode(tmp_path):
    """The fix must not move the *project* location, which was already right."""
    env = _fake_home(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()

    done = subprocess.run(
        [_bash(), str(SH), "--scope", "project", "--path", str(project),
         "--agent", "opencode", "--force"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        timeout=INSTALL_TIMEOUT, stdin=subprocess.DEVNULL,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert (project / ".opencode" / "skills" / SKILL / "SKILL.md").exists()
    assert not (project / ".config").exists()


@pytest.mark.skipif(_pwsh() is None, reason="no PowerShell on this machine")
def test_the_powershell_installer_puts_a_user_scope_opencode_skill_where_opencode_looks(
        tmp_path):
    env = _fake_home(tmp_path)
    home = Path(env["HOME"])

    done = subprocess.run(
        [_pwsh(), "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(PS1),
         "-Agent", "opencode", "-Scope", "user", "-Force"],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        timeout=INSTALL_TIMEOUT, stdin=subprocess.DEVNULL,
    )
    assert done.returncode == 0, done.stdout + done.stderr

    right = home / ".config" / "opencode" / "skills" / SKILL / "SKILL.md"
    wrong = home / ".opencode" / "skills" / SKILL
    if not right.exists() and not wrong.exists():
        # pwsh did not honour the redirected HOME on this platform, so the run
        # installed into the real profile — which this test must not touch and
        # cannot assert on. Say so rather than pass or fail on nothing.
        pytest.skip("pwsh ignored the redirected HOME; cannot observe the "
                    "destination without touching the real profile")
    assert right.exists(), done.stdout
    assert not wrong.exists(), (
        "the skill was put in ~/.opencode/skills, where OpenCode never looks")
