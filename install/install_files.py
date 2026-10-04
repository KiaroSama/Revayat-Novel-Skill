"""Installer path admission, exact payload snapshots and owned instruction bytes."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath

BEGIN = b"<!-- BEGIN revayat-novel -->"
END = b"<!-- END revayat-novel -->"
AGENTS = {
    "claude": ".claude", "kiro": ".kiro", "codex": ".codex",
    "cursor": ".cursor", "cline": ".cline", "hermes": ".hermes",
    "opencode": ".opencode", "antigravity": ".agents",
}
STATE = ".revayat-novel-installer"


def strict_json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("Nonfinite JSON value")

    if len(data) > 1024 * 1024:
        raise ValueError("Installer JSON exceeds 1 MiB")
    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=constant)


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("Invalid relative payload path")
    parts = value.split("/")
    if (PurePosixPath(value).is_absolute() or any(
            p in ("", ".", "..") or ":" in p or p.rstrip(" .") != p
            or any(ord(c) < 32 for c in p) for p in parts)):
        raise ValueError("Unsafe relative payload path")
    return value


def path_kind(path: Path) -> str | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Linked or reparse path refused")
    if stat.S_ISDIR(info.st_mode):
        return "tree"
    if stat.S_ISREG(info.st_mode):
        return "file"
    raise ValueError("Special filesystem object refused")


def admit(path: Path, base: Path | None = None) -> Path:
    lexical = Path(os.path.abspath(path))
    chain = [lexical, *lexical.parents]
    for entry in reversed(chain):
        kind = path_kind(entry)
        if entry != lexical and kind not in (None, "tree"):
            raise ValueError("Path parent is not a directory")
    canonical = lexical.resolve(strict=False)
    if base is not None:
        try:
            canonical.relative_to(base)
        except ValueError:
            raise ValueError("Installer path escapes selected base") from None
        existing = lexical
        while path_kind(existing) is None:
            existing = existing.parent
        if existing.stat().st_dev != base.stat().st_dev:
            raise ValueError("Cross-filesystem installation refused")
    return canonical


def disjoint(left: Path, right: Path) -> None:
    if left == right or left in right.parents or right in left.parents:
        raise ValueError("Source and installation paths overlap")
    if left.exists() and right.exists() and os.path.samefile(left, right):
        raise ValueError("Aliased installation path refused")


def fingerprint(path: Path) -> str | None:
    kind = path_kind(path)
    if kind is None:
        return None
    records = []
    todo = [(path, "")]
    while todo:
        entry, relative = todo.pop()
        entry_kind = path_kind(entry)
        if entry.name.casefold() == ".git" or (
                entry_kind == "tree" and (entry / "HEAD").is_file()
                and (entry / "objects").is_dir() and (entry / "refs").is_dir()):
            raise ValueError("Git-managed content refused")
        info = entry.lstat()
        if entry_kind == "file":
            digest = hashlib.sha256(entry.read_bytes()).hexdigest()
            records.append([relative, "file", stat.S_IMODE(info.st_mode), digest])
        elif entry_kind == "tree":
            records.append([relative, "tree", stat.S_IMODE(info.st_mode)])
            children = sorted(entry.iterdir(), key=lambda p: p.name)
            todo.extend((p, f"{relative}/{p.name}" if relative else p.name)
                        for p in reversed(children))
        else:
            raise ValueError("Content disappeared during snapshot")
    encoded = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def refuse_tracked(path: Path, base: Path) -> None:
    # An ignored agent destination inside a project is valid; tracked content is not.
    import shutil
    import subprocess
    git = shutil.which("git")
    enclosing = next((p for p in (path, *path.parents)
                      if (p / ".git").exists()), None)
    if enclosing is None:
        return
    if not git:
        raise ValueError("Git is required to verify managed destination ownership")
    relative = path.relative_to(enclosing).as_posix()
    done = subprocess.run(
        [git, "-C", str(enclosing), "ls-files", "--cached", "-z", "--",
         f":(top,literal){relative}"], capture_output=True, timeout=10,
        stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if done.returncode or done.stdout:
        raise ValueError("Tracked or unverifiable installation destination refused")


def sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path: Path, content: bytes, mode: int = 0o600) -> None:
    with path.open("xb") as handle:
        if handle.write(content) != len(content):
            raise OSError("Incomplete installer write")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, mode)
    if path.read_bytes() != content:
        raise OSError("Installer write verification failed")


def atomic_bytes(path: Path, content: bytes) -> None:
    admit(path)
    fd, name = tempfile.mkstemp(prefix=".revayat-novel-write-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            if handle.write(content) != len(content):
                raise OSError("Incomplete installer state write")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        if temp.exists():
            temp.unlink()


def payload_snapshot(source: Path, inventory: Path) -> dict:
    admit(source)
    admit(inventory)
    paths = strict_json(inventory.read_bytes())
    if not isinstance(paths, list) or not paths or paths != sorted(paths):
        raise ValueError("Payload inventory must be nonempty and sorted")
    checked = [safe_relative(p) for p in paths]
    if len({p.casefold() for p in checked}) != len(checked):
        raise ValueError("Duplicate or case-colliding payload path")
    required = {"SKILL.md", "requirements.txt", "requirements-optional.txt",
                "scripts/revayat-novel.py", "references/translation-policy.md"}
    if not required.issubset(checked):
        raise ValueError("Incomplete required skill payload")
    # Existing parent files in the manifest must come from the declared inventory,
    # not a second recursive source scan that would admit private checkout files.
    result = {}
    for relative in checked:
        path = admit(source / relative, source)
        if path_kind(path) != "file":
            raise ValueError("Declared payload file is missing")
        body = path.read_bytes()
        result[relative] = (body, stat.S_IMODE(path.stat().st_mode))
    return result


def stage_payload(path: Path, snapshot: dict) -> None:
    path.mkdir(mode=0o700)
    for relative, (body, mode) in snapshot.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        write_new(target, body, mode)
        actual_mode = stat.S_IMODE(target.stat().st_mode)
        mask = stat.S_IWRITE if os.name == "nt" else 0o777
        if actual_mode & mask != mode & mask:
            raise OSError("Staged payload permissions differ from source")
    actual = sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file())
    if actual != sorted(snapshot):
        raise OSError("Staged payload inventory differs from declared files")
    for directory in sorted((p for p in path.rglob("*") if p.is_dir()),
                            key=lambda p: len(p.parts), reverse=True):
        sync_directory(directory)
    sync_directory(path)


def pointer_bytes(original: bytes, destinations: list[str]) -> bytes:
    original.decode("utf-8-sig", errors="strict")
    for destination in destinations:
        if not destination or any(ord(c) < 32 or c == "`" for c in destination):
            raise ValueError("Path cannot be represented in an instruction pointer")
    lines = original.splitlines(keepends=True)
    markers = []
    offset = 0
    for i, line in enumerate(lines):
        content = line.rstrip(b"\r\n")
        if i == 0:
            content = content.removeprefix(b"\xef\xbb\xbf")
        if BEGIN in content or END in content:
            if content not in (BEGIN, END):
                raise ValueError("Pointer marker is not a standalone line")
            start = offset + (3 if i == 0 and line.startswith(b"\xef\xbb\xbf") else 0)
            markers.append((content, start, offset + len(line.rstrip(b"\r\n"))))
        offset += len(line)
    if markers and [m[0] for m in markers] != [BEGIN, END]:
        raise ValueError("Malformed or repeated owned pointer region")
    owned = original[markers[0][1]:markers[1][2]] if markers else original
    eol = b"\r\n" if b"\r\n" in owned else b"\n"
    text = [BEGIN.decode(), "## Revayat Novel — Persian book translation", "",
            "To translate a book into Persian and produce a Word file, follow:"]
    for destination in sorted(set(destinations)):
        text += [f"- `{destination}/SKILL.md` (`{{SKILL_DIR}}` is `{destination}`)."]
    text.append(END.decode())
    section = eol.join(line.encode("utf-8") for line in text)
    if markers:
        return original[:markers[0][1]] + section + original[markers[1][2]:]
    separator = b"" if not original or original.endswith((b"\n", b"\r")) else eol
    return original + separator + section + eol


def destination_for(base: Path, agent: str, scope: str) -> Path:
    home = base / (".config/opencode" if agent == "opencode" and scope == "user"
                   else AGENTS[agent])
    return home / "skills" / "revayat-novel"
