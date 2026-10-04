"""Declared payload bytes, not dirty recursive checkout contents, define installation."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "install"))


def test_inventory_equals_tracked_skill_files():
    actual = json.loads((ROOT / "install" / "payload.json").read_text(encoding="utf-8"))
    tracked = subprocess.check_output(
        ["git", "ls-files", "skills/revayat-novel"], cwd=ROOT, timeout=10,
        encoding="utf-8", stdin=subprocess.DEVNULL,
    ).splitlines()
    assert actual == sorted(p.removeprefix("skills/revayat-novel/") for p in tracked)
    from installer import INVENTORY_SHA256
    import hashlib
    assert hashlib.sha256((ROOT / "install/payload.json").read_bytes()).hexdigest() == INVENTORY_SHA256


def test_only_declared_payload_installs(tmp_path):
    from install_files import payload_snapshot, stage_payload
    names = ["SKILL.md", "requirements-optional.txt", "requirements.txt",
             "scripts/revayat-novel.py", "references/translation-policy.md"]
    source = tmp_path / "source"
    source.mkdir()
    for name in names:
        p = source / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(name.encode("utf-8"))
    (source / "private-manuscript.txt").write_bytes(b"excluded")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(sorted(names)), encoding="utf-8")
    stage = tmp_path / "stage"
    stage_payload(stage, payload_snapshot(source, inventory))
    assert sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()) == sorted(names)
    for name in names:
        assert (stage / name).read_bytes() == name.encode("utf-8")
    assert not (stage / "private-manuscript.txt").exists()


@pytest.mark.parametrize("paths", [
    ["SKILL.md"], ["../escape", "SKILL.md"], ["/escape", "SKILL.md"],
    ["SKILL.md", "skill.md"], ["SKILL.md", "SKILL.md"], ["a:stream"],
])
def test_invalid_inventory_refuses_before_staging(tmp_path, paths):
    from install_files import payload_snapshot
    source = tmp_path / "source"
    source.mkdir()
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps(sorted(paths)), encoding="utf-8")
    with pytest.raises(ValueError):
        payload_snapshot(source, inventory)


def test_readonly_source_is_flushed_before_readonly_mode(tmp_path):
    from install_files import stage_payload
    stage = tmp_path / "stage"
    mode = 0o444
    try:
        stage_payload(stage, {"SKILL.md": (b"readonly payload", mode)})
        assert (stage / "SKILL.md").read_bytes() == b"readonly payload"
        assert not (stage / "SKILL.md").stat().st_mode & 0o200
    finally:
        if (stage / "SKILL.md").exists():
            os.chmod(stage / "SKILL.md", 0o600)
