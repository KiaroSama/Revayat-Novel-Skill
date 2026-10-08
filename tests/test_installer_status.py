"""Status observes installer state without creating or recovering anything."""

import json
from pathlib import Path
import sys

import bookir as ir
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))

ENGINE = Path(__file__).resolve().parents[1] / "install" / "installer.py"


def test_status_on_absent_state_is_readonly_and_reports_absent(tmp_path):
    base = tmp_path / "selected project"
    base.mkdir()
    sentinel = base / "private instructions.txt"
    sentinel.write_bytes(b"private sentinel must never be printed")
    before = {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*")}
    process = ir.run_bounded([sys.executable, str(ENGINE), "--status", "--scope",
                              "project", "--path", str(base), "--agent", "claude"], 10)
    assert process.returncode == 0, process.stderr.decode("utf-8")
    report = json.loads(process.stdout.decode("utf-8"))
    assert report["status"] == "absent"
    assert report["installed"] == 0
    assert b"private sentinel" not in process.stdout + process.stderr
    assert {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*")} == before


def _state(base, phase="prepared", *, receipt=False):
    from install_files import STATE, fingerprint
    from install_transaction import SCHEMA

    state = base / STATE
    state.mkdir(mode=0o700)
    token = "a" * 32
    destination = base / ".claude/skills/revayat-novel"
    stage = state / f"stage-{token}-0"
    participant = destination if phase == "committed" else stage
    participant.mkdir(parents=True)
    (participant / "private.txt").write_bytes(b"PRIVATE-BODY-NEVER-PRINTED")
    record = {"schema": SCHEMA, "base": str(base.resolve()), "token": token,
              "phase": phase, "participants": [{"destination": ".claude/skills/revayat-novel",
                  "stage": f"{STATE}/stage-{token}-0", "backup": f"{STATE}/backup-{token}-0",
                  "kind": "tree", "old": None, "new": fingerprint(participant)}]}
    (state / (f"receipt-{token}.json" if receipt else "journal.json")).write_text(
        json.dumps(record), encoding="utf-8")
    return state, participant


def _bytes(base):
    return {p.relative_to(base).as_posix(): p.read_bytes() if p.is_file() else None
            for p in base.rglob("*")}


@pytest.mark.parametrize("phase,receipt,status,code", [
    ("prepared", False, "prepared", 2),
    ("committed", False, "cleanup_pending", 2),
    ("committed", True, "committed", 0),
])
def test_status_preserves_pending_committed_state_without_housekeeping(tmp_path, phase, receipt, status, code):
    _state(tmp_path, phase, receipt=receipt)
    before = _bytes(tmp_path)
    process = ir.run_bounded([sys.executable, str(ENGINE), "--status", "--force", "--scope",
                              "project", "--path", str(tmp_path), "--agent", "claude"], 10)
    assert process.returncode == code, process.stderr.decode("utf-8")
    report = json.loads(process.stdout.decode("utf-8"))
    assert report["status"] == status and report["participants"] == 1
    assert b"PRIVATE-BODY" not in process.stdout + process.stderr
    assert _bytes(tmp_path) == before


def test_status_reports_conflict_without_repair_or_body_output(tmp_path):
    import install_status

    _, participant = _state(tmp_path)
    (participant / "private.txt").write_bytes(b"changed private body")
    before = _bytes(tmp_path)
    result = install_status.inspect(tmp_path, "claude", "project")
    assert result["status"] == "conflict"
    assert _bytes(tmp_path) == before
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("wrapper", ["bash", "pwsh"])
def test_native_wrappers_forward_readonly_status(tmp_path, wrapper):
    from test_installers import _bash, _pwsh

    shell = _bash() if wrapper == "bash" else _pwsh()
    if shell is None:
        pytest.skip("no bash here that can open install.sh" if wrapper == "bash" else "no PowerShell on this machine")
    root = ENGINE.parent
    command = ([shell, str(root / "install.sh"), "--status", "--scope", "project", "--path", str(tmp_path)]
               if wrapper == "bash" else [shell, "-NoProfile", "-NonInteractive", "-File", str(root / "install.ps1"),
                                          "-Status", "-Scope", "project", "-Path", str(tmp_path)])
    process = ir.run_bounded(command, 15)
    assert process.returncode == 0, process.stderr.decode("utf-8", "replace")
    assert json.loads(process.stdout.decode("utf-8"))["status"] == "absent"
    assert not list(tmp_path.iterdir())


def test_status_reports_double_read_instability_without_taking_a_lock(tmp_path, monkeypatch):
    import install_status

    state, _ = _state(tmp_path)
    observe = install_status.observe
    count = 0

    def changing(*args):
        nonlocal count
        result = observe(*args)
        count += 1
        if count == 1:
            (state / "new-file").write_bytes(b"changed concurrently")
        return result

    monkeypatch.setattr(install_status, "observe", changing)
    assert install_status.inspect(tmp_path, "claude", "project")["status"] == "unstable"
    assert not (state / "lock").exists()


def test_linked_status_state_is_unknown_and_never_followed(tmp_path):
    import install_status
    import os

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_bytes(b"private outside sentinel")
    base = tmp_path / "base"
    base.mkdir()
    linked = base / ".revayat-novel-installer"
    try:
        os.symlink(outside, linked, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import subprocess
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
                              capture_output=True, timeout=5,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    try:
        assert install_status.inspect(base, "claude", "project")["status"] == "unknown"
        assert (outside / "private.txt").read_bytes() == b"private outside sentinel"
        assert sorted(p.name for p in outside.iterdir()) == ["private.txt"]
    finally:
        if linked.is_symlink():
            linked.unlink()
        else:
            os.rmdir(linked)


@pytest.mark.parametrize("state_bytes", [b"{broken", b"[]", b"\xff", b'{"schema":"future"}'])
def test_malformed_state_is_unknown_and_preserved(tmp_path, state_bytes):
    import install_status

    state, _ = _state(tmp_path)
    (state / "journal.json").write_bytes(state_bytes)
    before = _bytes(tmp_path)
    assert install_status.inspect(tmp_path, "claude", "project")["status"] == "unknown"
    assert _bytes(tmp_path) == before
