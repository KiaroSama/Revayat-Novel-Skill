"""Actual link/junction and managed-tree admission, never force-authorized traversal."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "install"))


def directory_link(link, target):
    if os.name == "nt":
        done = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, timeout=10, stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert done.returncode == 0, "Windows junction capability required"
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("relative", [
    ".claude", ".claude/skills", ".claude/skills/revayat-novel",
    ".revayat-novel-installer",
])
def test_linked_ancestors_refuse_without_touching_external_content(tmp_path, relative):
    from install_transaction import publish
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "owner.txt"
    sentinel.write_bytes(b"outside bytes")
    base = tmp_path / "base"
    base.mkdir()
    link = base / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    directory_link(link, external)
    try:
        with pytest.raises(ValueError, match="Linked or reparse"):
            publish(base, [base / ".claude/skills/revayat-novel"],
                    {"SKILL.md": (b"new", 0o644)})
        assert sentinel.read_bytes() == b"outside bytes"
        assert sorted(p.name for p in external.iterdir()) == ["owner.txt"]
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()


@pytest.mark.parametrize("metadata", ["directory", "gitfile", "tracked"])
def test_git_managed_destination_refuses(tmp_path, metadata):
    from install_transaction import publish
    path = tmp_path / ".claude/skills/revayat-novel"
    path.mkdir(parents=True)
    (path / "owner.txt").write_bytes(b"old")
    if metadata == "directory":
        (path / ".git").mkdir()
    elif metadata == "gitfile":
        (path / ".git").write_bytes(b"gitdir: elsewhere\n")
    else:
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True,
                       timeout=10, stdin=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(tmp_path), "add", "--", str(path / "owner.txt")],
                       check=True, capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    with pytest.raises(ValueError):
        publish(tmp_path, [path], {"SKILL.md": (b"new", 0o644)})
    assert (path / "owner.txt").read_bytes() == b"old"


@pytest.mark.parametrize("relative", ["AGENTS.md", ".revayat-novel-installer/lock", "source/SKILL.md"])
def test_file_symlink_refuses_without_following_target(tmp_path, relative):
    from install_files import admit
    target = tmp_path / "outside.txt"
    target.write_bytes(b"outside original")
    link = tmp_path / "base" / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    # Windows symlink capability is required, not silently skipped.
    link.symlink_to(target)
    try:
        with pytest.raises(ValueError, match="Linked or reparse"):
            admit(link)
        assert target.read_bytes() == b"outside original"
    finally:
        link.unlink()


def test_predictable_old_temporary_pointer_is_never_opened(tmp_path):
    from install_transaction import publish
    oldtemp = tmp_path / "AGENTS.md.revayat-novel.tmp"
    oldtemp.write_bytes(b"unowned temporary sentinel")
    path = tmp_path / ".opencode/skills/revayat-novel"
    result = publish(tmp_path, [path], {"SKILL.md": (b"new", 0o644)}, pointer=(b"", b"new pointer"))
    assert result["committed"]
    assert oldtemp.read_bytes() == b"unowned temporary sentinel"



def test_nested_agent_repository_tracked_destination_refuses(tmp_path):
    from install_transaction import publish
    repository = tmp_path / ".claude"
    path = repository / "skills/revayat-novel"
    path.mkdir(parents=True)
    (path / "owner.txt").write_bytes(b"tracked original")
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True,
                   timeout=10, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(repository), "add", "skills/revayat-novel/owner.txt"],
                   check=True, capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    with pytest.raises(ValueError, match="Tracked"):
        publish(tmp_path, [path], {"SKILL.md": (b"new", 0o644)})
    assert (path / "owner.txt").read_bytes() == b"tracked original"



def test_source_destination_overlap_refuses():
    from install_files import disjoint
    source = Path.cwd() / "skills/revayat-novel"
    with pytest.raises(ValueError, match="overlap"):
        disjoint(source, source / "nested")
    with pytest.raises(ValueError, match="overlap"):
        disjoint(source, source.parent)


@pytest.mark.parametrize("value", [b'{"a":1,"a":2}', b'{"a":NaN}', b'[]' * (600 * 1024)])
def test_recovery_json_rejects_ambiguous_or_oversized_values(value):
    from install_files import strict_json
    with pytest.raises(ValueError):
        strict_json(value)
