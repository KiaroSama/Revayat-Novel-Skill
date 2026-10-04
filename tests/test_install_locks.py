"""Real live OS locks are not stolen by age and release on process death."""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

INSTALL = Path(__file__).resolve().parents[1] / "install"
sys.path.insert(0, str(INSTALL))


def test_old_timestamp_live_lock_is_not_stolen_and_death_releases_it(tmp_path):
    from install_transaction import recover
    script = (
        "import sys; from pathlib import Path; from install_transaction import installation_lock; "
        "context=installation_lock(Path(sys.argv[1])); context.__enter__(); "
        "print('owned',flush=True); sys.stdin.buffer.read(1)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)],
        env={**os.environ, "PYTHONPATH": str(INSTALL), "PYTHONUTF8": "1"},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            ready = pool.submit(child.stdout.readline)
            assert ready.result(timeout=10).rstrip(b"\r\n") == b"owned"
            lock = tmp_path / ".revayat-novel-installer" / "lock"
            os.utime(lock, (1, 1))
            with pytest.raises(ValueError, match="OS lock"):
                recover(tmp_path, wait=0.1)
            assert child.poll() is None
            child.kill()
            child.wait(timeout=5)
            assert recover(tmp_path, wait=0.5) is None
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for handle in (child.stdin, child.stdout, child.stderr):
                handle.close()
