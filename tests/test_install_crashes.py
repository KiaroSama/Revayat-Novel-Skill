"""Actual process death at public publication signals, with bounded pipe ownership."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))
INSTALL = Path(__file__).resolve().parents[1] / "install"


@pytest.mark.parametrize("event,index", [
    ("prepared", -1), ("backed_up", 0), ("promoted", 0),
    ("backed_up", 1), ("promoted", 1), ("backed_up", 2), ("promoted", 2),
    ("committed", -1),
])
def test_process_death_recovers_at_publication_boundary(tmp_path, event, index):
    from install_transaction import recover
    paths = [tmp_path / agent / "skills" / "revayat-novel" for agent in (".claude", ".kiro")]
    for path in paths:
        path.mkdir(parents=True)
        (path / "owner.txt").write_bytes(b"retained original")
    pointer = tmp_path / "AGENTS.md"
    pointer.write_bytes(b"private original\r\n")
    script = (
        "import os,sys; from pathlib import Path; "
        "from install_transaction import publish; "
        "base=Path(sys.argv[1]); "
        "observe=lambda event,index: os._exit(73) if (event,str(index)) == "
        "(sys.argv[2],sys.argv[3]) else None; "
        "publish(base,[base/a/'skills'/'revayat-novel' for a in ('.claude','.kiro')],"
        "{'SKILL.md':(b'new payload',420)},"
        "pointer=((base/'AGENTS.md').read_bytes(),b'new pointer'),observer=observe)"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), event, str(index)],
        env={**os.environ, "PYTHONPATH": str(INSTALL), "PYTHONUTF8": "1"},
        stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert done.returncode == 73, done.stderr.decode("utf-8")
    result = recover(tmp_path)
    assert not result["cleanup_pending"]
    assert pointer.read_bytes() == (b"new pointer" if event == "committed" else b"private original\r\n")
    for path in paths:
        if event == "committed":
            assert (path / "SKILL.md").read_bytes() == b"new payload"
            assert not (path / "owner.txt").exists()
        else:
            assert (path / "owner.txt").read_bytes() == b"retained original"
            assert not (path / "SKILL.md").exists()


@pytest.mark.parametrize("identical", [False, True])
def test_interrupted_recovery_is_resumable(tmp_path, identical):
    from install_transaction import recover
    path = tmp_path / ".claude/skills/revayat-novel"
    path.mkdir(parents=True)
    name = "SKILL.md" if identical else "owner.txt"
    (path / name).write_bytes(b"new payload" if identical else b"old")
    env = {**os.environ, "PYTHONPATH": str(INSTALL), "PYTHONUTF8": "1"}
    script = (
        "import os,sys; from pathlib import Path; from install_transaction import publish; "
        "b=Path(sys.argv[1]); publish(b,[b/'.claude/skills/revayat-novel'],"
        "{'SKILL.md':(b'new payload',438 if os.name=='nt' else 420)},"
        "observer=lambda e,i: os._exit(73) if e=='promoted' else None)"
    )
    first = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=env,
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert first.returncode == 73, first.stderr.decode("utf-8")
    script = (
        "import os,sys; from pathlib import Path; from install_transaction import recover; "
        "recover(Path(sys.argv[1]),observer=lambda e,i: os._exit(74) "
        "if e=='recovery_staged' else None)"
    )
    second = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=env,
                            capture_output=True, stdin=subprocess.DEVNULL, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert second.returncode == 74, second.stderr.decode("utf-8")
    assert not recover(tmp_path)["committed"]
    assert (path / name).read_bytes() == (b"new payload" if identical else b"old")
    assert recover(tmp_path) is None


@pytest.mark.parametrize("event", ["prepared", "promoted", "committed"])
def test_initial_absence_recovers_without_inventing_old_content(tmp_path, event):
    from install_transaction import recover
    path = tmp_path / ".claude/skills/revayat-novel"
    script = (
        "import os,sys; from pathlib import Path; from install_transaction import publish; "
        "b=Path(sys.argv[1]); publish(b,[b/'.claude/skills/revayat-novel'],"
        "{'SKILL.md':(b'new',420)},observer=lambda e,i: os._exit(73) if e==sys.argv[2] else None)"
    )
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), event],
                            env={**os.environ, "PYTHONPATH": str(INSTALL), "PYTHONUTF8": "1"},
                            capture_output=True, stdin=subprocess.DEVNULL, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 73, result.stderr.decode("utf-8")
    assert recover(tmp_path)["committed"] == (event == "committed")
    assert path.exists() == (event == "committed")
