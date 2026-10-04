"""Native installation must preserve the whole old plan on refusal."""
from __future__ import annotations

import os
import subprocess

from test_installers import ROOT, SH, PS1, _bash, _pwsh

import pytest


def test_late_invalid_destination_preserves_first_installation(tmp_path):
    base = tmp_path / "project space"
    old = base / ".claude" / "skills" / "revayat-novel"
    old.mkdir(parents=True)
    sentinel = old / "owner.txt"
    sentinel.write_bytes(b"previous installation\r\n")
    late = base / ".kiro"
    late.mkdir()
    (late / "skills").write_bytes(b"not a directory")
    bash = _bash()
    assert bash, "native Bash is required for installer preservation acceptance"
    done = subprocess.run(
        [bash, str(SH), "--scope", "project", "--path", str(base),
         "--agent", "all", "--force"],
        cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"},
        stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
        timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert done.returncode != 0, done.stdout + done.stderr
    assert sentinel.is_file(), "late failure removed the earlier owner's file"
    assert sentinel.read_bytes() == b"previous installation\r\n"
    assert (late / "skills").read_bytes() == b"not a directory"
    assert not (base / "AGENTS.md").exists()


@pytest.mark.parametrize("wrapper", ["bash", "powershell"] if os.name == "nt" else ["bash"])
@pytest.mark.parametrize("scenario", ["malformed", "invalid_utf8", "no_consent", "success"])
def test_native_wrapper_preserves_instruction_bytes(tmp_path, wrapper, scenario):
    base = tmp_path / "book space فارسی"
    old = base / ".opencode/skills/revayat-novel"
    old.mkdir(parents=True)
    (old / "owner.txt").write_bytes(b"old sentinel")
    prefix = bytes.fromhex("efbbbf") + b"Owner\r\n"
    suffix = b"\r\nprivate tail"
    initial = (prefix + b"<!-- BEGIN revayat-novel -->\r\nold\r\n<!-- END revayat-novel -->" + suffix)
    if scenario == "malformed":
        initial = prefix + b"<!-- BEGIN revayat-novel -->\r\nprivate instructions"
    if scenario == "invalid_utf8":
        initial = bytes([255]) + b"private invalid text"
    other = base / ".agents/skills/revayat-novel"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_bytes(b"preserved other agent")
    pointer = base / "AGENTS.md"
    pointer.write_bytes(initial)
    if wrapper == "bash":
        command = [_bash(), str(SH), "--scope", "project", "--path", str(base), "--agent", "opencode"]
        if scenario != "no_consent":
            command += ["--force"]
    else:
        assert _pwsh(), "Windows PowerShell capability is required"
        command = [_pwsh(), "-NoProfile", "-NonInteractive", "-File", str(PS1),
                   "-Scope", "project", "-Path", str(base), "-Agent", "opencode"]
        if scenario != "no_consent":
            command += ["-Force"]
    done = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", timeout=30,
                          env={**os.environ, "PYTHONUTF8": "1"},
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    logs = list((base / ".revayat-novel-installer").glob("installer_*.log"))
    assert len(logs) == 1
    log = logs[0].read_text(encoding="utf-8")
    assert "UTC]" in log and "[installer]" in log
    assert "private instructions" not in log and "private tail" not in log
    if scenario != "success":
        assert done.returncode != 0
        assert (old / "owner.txt").read_bytes() == b"old sentinel"
        assert pointer.read_bytes() == initial
    else:
        assert done.returncode == 0, done.stdout + done.stderr
        assert (old / "SKILL.md").is_file()
        assert "committed=True" in log and "exit=0" in log
        result = pointer.read_bytes()
        assert result.startswith(prefix) and result.endswith(suffix)
        assert other.as_posix().encode("utf-8") in result
        repeat = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                capture_output=True, encoding="utf-8", timeout=30,
                                env={**os.environ, "PYTHONUTF8": "1"},
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert repeat.returncode == 0, repeat.stdout + repeat.stderr
        assert pointer.read_bytes() == result
        assert len(list((base / ".revayat-novel-installer").glob("installer_*.log"))) == 2
        backups = list((base / ".revayat-novel-installer").glob("backup-*"))
        assert any(p.is_dir() and (p / "owner.txt").read_bytes() == b"old sentinel" for p in backups)
