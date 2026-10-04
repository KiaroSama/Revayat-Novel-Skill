"""Recoverable cooperating-installer publication; originals are retained, never deleted."""
from __future__ import annotations

import errno
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from install_files import (
    AGENTS, STATE, admit, atomic_bytes, destination_for, fingerprint,
    path_kind, refuse_tracked, safe_relative, stage_payload, strict_json, sync_directory, write_new,
)

SCHEMA = "revayat-novel/install@1"


@contextmanager
def installation_lock(base: Path, wait: float = 5.0):
    base = admit(base)
    if path_kind(base) != "tree":
        raise ValueError("Installation base must be an existing directory")
    state = admit(base / STATE, base)
    if path_kind(state) not in (None, "tree"):
        raise ValueError("Installer state is not a directory")
    state.mkdir(mode=0o700, exist_ok=True)
    if os.name != "nt" and state.stat().st_mode & 0o077:
        raise ValueError("Installer state must be owner-only")
    lock = admit(state / "lock", base)
    if path_kind(lock) not in (None, "file"):
        raise ValueError("Installer lock is not a regular file")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                _lock(fd)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise ValueError("Another installer holds the OS lock") from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        if os.fstat(fd).st_ino != lock.stat().st_ino:
            raise ValueError("Installer lock identity changed")
        ignore = admit(state / ".gitignore", base)
        if path_kind(ignore) is None:
            write_new(ignore, b"*\n")
            sync_directory(state)
        elif path_kind(ignore) != "file" or ignore.read_bytes() != b"*\n":
            raise ValueError("Installer state protection is invalid")
        for protected in (state, lock, ignore, state / "journal.json"):
            refuse_tracked(protected, base)
        yield state
    finally:
        try:
            if acquired:
                _lock(fd, release=True)
        finally:
            os.close(fd)


def _lock(fd: int, release: bool = False):
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)


def _persist(path: Path, record: dict):
    atomic_bytes(path, (json.dumps(record, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")) + "\n").encode("utf-8"))


def _allowed(base: Path) -> set[Path]:
    return {destination_for(base, agent, scope) for agent in AGENTS
            for scope in ("user", "project")} | {base / "AGENTS.md"}


def _validate(base: Path, record: dict):
    if not isinstance(record, dict) or set(record) != {
            "schema", "base", "token", "phase", "participants"}:
        raise ValueError("Invalid installer journal fields")
    token = record["token"]
    if (record["schema"] != SCHEMA or record["base"] != str(base)
            or not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token)
            or record["phase"] not in ("prepared", "committed", "rolled_back")):
        raise ValueError("Invalid installer journal identity")
    entries = record["participants"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 9:
        raise ValueError("Invalid installer participants")
    seen = set()
    allowed = _allowed(base)
    result = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != {
                "destination", "stage", "backup", "kind", "old", "new"}:
            raise ValueError("Invalid installer participant fields")
        if any(not isinstance(entry[key], str) for key in
               ("destination", "stage", "backup", "kind", "new")):
            raise ValueError("Invalid installer participant types")
        destination = admit(base / safe_relative(entry["destination"]), base)
        stage = admit(base / safe_relative(entry["stage"]), base)
        backup = admit(base / safe_relative(entry["backup"]), base)
        if (destination not in allowed or destination in seen
                or entry["kind"] != ("file" if destination == base / "AGENTS.md" else "tree")
                or stage != base / STATE / f"stage-{token}-{i}"
                or backup != base / STATE / f"backup-{token}-{i}"):
            raise ValueError("Installer recovery path is not an owned participant")
        for key in ("old", "new"):
            value = entry[key]
            if not (key == "old" and value is None) and (
                    not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)):
                raise ValueError("Invalid installer fingerprint")
        refuse_tracked(destination, base)
        for path in (destination, stage, backup):
            if path_kind(path) not in (None, entry["kind"]):
                raise ValueError("Installer participant type changed")
        seen.add(destination)
        result.append((entry, destination, stage, backup))
    return result


def _classify(participant, phase: str):
    entry, destination, stage, backup = participant
    current, staged, saved = (fingerprint(p) for p in (destination, stage, backup))
    old, new = entry["old"], entry["new"]
    if staged not in (None, new) or saved not in (None, old):
        raise ValueError("Installer recovery conflict: changed stage or backup")
    if phase == "committed":
        if current != new or staged is not None or (old is not None and saved != old):
            raise ValueError("Installer committed state conflicts with current content")
        return "committed"
    if old is None:
        if saved is not None:
            raise ValueError("Unexpected installer backup")
        if current is None and staged == new:
            return "restored"
        if current == new and staged is None:
            return "new"
    elif saved == old:
        if current is None and staged == new:
            return "old_moved"
        if current == new and staged is None:
            return "new"
    elif saved is None and current == old and staged == new:
        return "restored"
    raise ValueError("Installer recovery conflict: unknown current state")


def _rename(source: Path, target: Path):
    admit(source)
    admit(target)
    if path_kind(target) is not None:
        raise ValueError("Installer rename destination unexpectedly exists")
    os.rename(source, target)
    sync_directory(source.parent)
    if source.parent != target.parent:
        sync_directory(target.parent)


def _receipt(state: Path, record: dict) -> dict:
    journal = state / "journal.json"
    receipt = state / f"receipt-{record['token']}.json"
    admit(receipt, state)
    content = journal.read_bytes()
    if path_kind(receipt) is None:
        atomic_bytes(receipt, content)
    elif path_kind(receipt) != "file" or receipt.read_bytes() != content:
        raise ValueError("Installer receipt conflicts with recorded decision")
    journal.unlink()
    sync_directory(state)
    return {"committed": record["phase"] == "committed", "cleanup_pending": False,
            "receipt": str(receipt)}


def _recover(base: Path, state: Path, observer=None) -> dict | None:
    journal = admit(state / "journal.json", base)
    if path_kind(journal) is None:
        return None
    if path_kind(journal) != "file":
        raise ValueError("Installer journal is not a regular file")
    with journal.open("rb") as handle:
        record = strict_json(handle.read(1024 * 1024 + 1))
    participants = _validate(base, record)
    # Validate the whole plan before the first recovery rename.
    statuses = [_classify(p, record["phase"]) for p in participants]
    if record["phase"] == "committed":
        try:
            return _receipt(state, record)
        except (OSError, ValueError):
            return {"committed": True, "cleanup_pending": True, "journal": str(journal)}
    for i in reversed(range(len(participants))):
        entry, destination, stage, backup = participants[i]
        status = statuses[i]
        if _classify(participants[i], "prepared") != status:
            raise ValueError("Installer recovery state changed after validation")
        if status == "new":
            _rename(destination, stage)
            if observer:
                observer("recovery_staged", i)
        if entry["old"] is not None and status in ("new", "old_moved"):
            _rename(backup, destination)
            if observer:
                observer("restored", i)
    for p in participants:
        if _classify(p, "prepared") != "restored":
            raise ValueError("Installer rollback did not restore recorded state")
    record["phase"] = "rolled_back"
    _persist(journal, record)
    return _receipt(state, record)


def recover(base: Path, *, observer=None, wait: float = 5.0) -> dict | None:
    base = admit(base)
    with installation_lock(base, wait) as state:
        return _recover(base, state, observer)


def publish(base: Path, destinations: list[Path], snapshot: dict, *, pointer=None,
            source_check=None, observer=None, prepare=None) -> dict:
    base = admit(base)
    with installation_lock(base) as state:
        recovered = _recover(base, state, observer)
        if recovered and recovered.get("cleanup_pending"):
            return recovered
        if prepare:
            destinations, snapshot, pointer, source_check = prepare()
        selected = [(Path(p), "tree", None) for p in destinations]
        if pointer is not None:
            original, replacement = pointer
            pointer_path = admit(base / "AGENTS.md", base)
            current = pointer_path.read_bytes() if pointer_path.exists() else b""
            if current != original:
                raise ValueError("Instruction pointer changed while planning")
            selected.append((pointer_path, "file", replacement))
        if not selected:
            raise ValueError("Nothing selected for installation")
        token = uuid.uuid4().hex
        record = {"schema": SCHEMA, "base": str(base), "token": token,
                  "phase": "prepared", "participants": []}
        old_states = []
        allowed = _allowed(base)
        for destination, kind, body in selected:
            destination = admit(destination, base)
            if destination not in allowed or path_kind(destination) not in (None, kind):
                raise ValueError("Invalid selected installation destination")
            refuse_tracked(destination, base)
            old_states.append(fingerprint(destination))
        if len({p[0] for p in selected}) != len(selected):
            raise ValueError("Duplicate installation destination")
        for i, (destination, kind, body) in enumerate(selected):
            destination.parent.mkdir(parents=True, exist_ok=True)
            admit(destination.parent, base)
            stage = state / f"stage-{token}-{i}"
            backup = state / f"backup-{token}-{i}"
            if path_kind(stage) is not None or path_kind(backup) is not None:
                raise ValueError("Installer staging path already exists")
            if kind == "tree":
                stage_payload(stage, snapshot)
            else:
                mode = destination.stat().st_mode & 0o777 if destination.exists() else 0o600
                write_new(stage, body, mode)
            sync_directory(stage.parent)
            record["participants"].append({
                "destination": destination.relative_to(base).as_posix(),
                "stage": stage.relative_to(base).as_posix(),
                "backup": backup.relative_to(base).as_posix(), "kind": kind,
                "old": old_states[i], "new": fingerprint(stage),
            })
            if observer:
                observer("staged", i)
        participants = _validate(base, record)
        if source_check:
            source_check()
        for p in participants:
            _classify(p, "prepared")
        journal = state / "journal.json"
        _persist(journal, record)
        if observer:
            observer("prepared", -1)
        try:
            for p in participants:
                if _classify(p, "prepared") != "restored":
                    raise ValueError("Installation plan changed before publication")
            for i, (entry, destination, stage, backup) in enumerate(participants):
                if fingerprint(destination) != entry["old"]:
                    raise ValueError("Installation target changed before publication")
                if entry["old"] is not None:
                    _rename(destination, backup)
                    if observer:
                        observer("backed_up", i)
                _rename(stage, destination)
                if observer:
                    observer("promoted", i)
            for p in participants:
                _classify(p, "committed")
            record["phase"] = "committed"
            _persist(journal, record)
            if observer:
                observer("committed", -1)
        except (Exception, KeyboardInterrupt, SystemExit):
            # The on-disk marker, not the exception, decides whether rollback is legal.
            disk = strict_json(journal.read_bytes())
            if disk["phase"] == "committed":
                return {"committed": True, "cleanup_pending": True,
                        "journal": str(journal)}
            _recover(base, state, observer)
            raise
        try:
            return _receipt(state, record)
        except (OSError, ValueError):
            return {"committed": True, "cleanup_pending": True, "journal": str(journal)}
