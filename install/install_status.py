"""Bounded read-only observations; no lock, recovery, logging or publication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from install_files import AGENTS, STATE, admit, destination_for, path_kind, strict_json
from install_transaction import _validate

MAX_FILES = 20000
MAX_BYTES = 256 * 1024 * 1024


class Unstable(ValueError):
    pass


def identity(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    path_kind(path)
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def snapshot(path, budget):
    """Same fingerprint grammar as publication, streaming bounded bytes only."""
    if identity(path) is None:
        return None
    records = []
    todo = [(path, "")]
    while todo:
        entry, relative = todo.pop()
        before = identity(entry)
        if before is None:
            raise Unstable("participant disappeared")
        kind = path_kind(entry)
        budget[0] += 1
        if budget[0] > MAX_FILES:
            raise ValueError("status file limit")
        if kind == "file":
            digest = hashlib.sha256()
            with entry.open("rb") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != before[:2] or identity(entry) != before:
                    raise Unstable("participant identity changed before read")
                while raw := handle.read(65536):
                    budget[1] += len(raw)
                    if budget[1] > MAX_BYTES:
                        raise ValueError("status byte limit")
                    digest.update(raw)
                if (os.fstat(handle.fileno()).st_dev, os.fstat(handle.fileno()).st_ino) != before[:2]:
                    raise Unstable("participant identity changed")
            records.append([relative, "file", stat.S_IMODE(before[2]), digest.hexdigest()])
        elif kind == "tree":
            records.append([relative, "tree", stat.S_IMODE(before[2])])
            children = sorted(entry.iterdir(), key=lambda p: p.name)
            if len(children) + budget[0] > MAX_FILES:
                raise ValueError("status file limit")
            todo.extend((child, relative + "/" + child.name if relative else child.name)
                        for child in reversed(children))
        else:
            raise ValueError("invalid participant")
        if identity(entry) != before:
            raise Unstable("participant changed while reading")
    body = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def read_record(path):
    before = identity(path)
    if before is None or path_kind(path) != "file":
        raise ValueError("invalid state record")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino) != before[:2] or identity(path) != before:
            raise Unstable("state record changed before read")
        data = handle.read(1024 * 1024 + 1)
    if before != identity(path):
        raise Unstable("state record changed")
    return strict_json(data)


def observe(base, agent, scope):
    state = admit(base / STATE, base)
    destinations = [admit(destination_for(base, name, scope), base)
                    for name in (AGENTS if agent == "all" else [agent])]
    kinds = [path_kind(p) for p in destinations]
    if any(kind not in (None, "tree") for kind in kinds):
        raise ValueError("invalid skill destination")
    installed = sum(kind == "tree" for kind in kinds)
    result = {"schema": "revayat-novel/install-status@1", "status": "absent",
              "installed": installed, "receipts": 0, "backups": 0,
              "participants": 0, "phase": None}
    if path_kind(state) is None:
        if installed:
            result["status"] = "unknown"
        return result, (identity(state), tuple(identity(p) for p in destinations))
    if path_kind(state) != "tree" or (os.name != "nt" and state.stat().st_mode & 0o077):
        raise ValueError("unsafe installer state")
    initial = identity(state)
    members = sorted(state.iterdir(), key=lambda p: p.name)
    if len(members) > 1000:
        raise ValueError("status record limit")
    metadata = tuple((p.name, identity(p)) for p in members)
    if any(path_kind(p) not in ("file", "tree") for p in members):
        raise ValueError("invalid state member")
    receipts = [p for p in members if p.name.startswith("receipt-") and p.suffix == ".json"]
    backups = [p for p in members if p.name.startswith("backup-")]
    stages = [p for p in members if p.name.startswith("stage-")]
    result.update(receipts=len(receipts), backups=len(backups))
    journal = state / "journal.json"
    record_path = journal if path_kind(journal) is not None else (
        max(receipts, key=lambda p: (p.stat().st_mtime_ns, p.name)) if receipts else None)
    budget = [0, 0]
    signatures = []
    record_digest = None
    if record_path is not None:
        record = read_record(record_path)
        record_digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        participants = _validate(base, record)
        phase = record["phase"]
        result.update(phase=phase, participants=len(participants))
        if record_path != journal and record_path.name != f"receipt-{record['token']}.json":
            raise ValueError("receipt identity differs")
        conflict = False
        for entry, destination, stage, backup in participants:
            current, staged, saved = (snapshot(p, budget) for p in (destination, stage, backup))
            signatures.append((current, staged, saved))
            old, new = entry["old"], entry["new"]
            if phase == "committed":
                valid = current == new and staged is None and saved == old
            else:
                valid = ((current == old and staged == new and saved is None)
                         or (current is None and staged == new and old is not None and saved == old)
                         or (current == new and staged is None and saved == old))
            conflict |= not valid
        if conflict:
            result["status"] = "conflict"
        elif record_path == journal:
            result["status"] = "cleanup_pending" if phase == "committed" else "prepared"
        else:
            result["status"] = "committed" if phase == "committed" else "absent"
    elif installed or stages or backups:
        result["status"] = "unknown"
    if stages and record_path != journal:
        result["status"] = "unknown"
    if initial != identity(state) or metadata != tuple((p.name, identity(p)) for p in sorted(state.iterdir(), key=lambda p: p.name)):
        raise Unstable("state membership changed")
    return result, (initial, metadata, record_digest, tuple(signatures), tuple(identity(p) for p in destinations))


def inspect(base: Path, agent: str, scope: str) -> dict:
    try:
        if agent not in (*AGENTS, "all") or scope not in ("user", "project"):
            raise ValueError("invalid status selection")
        base = admit(base)
        if path_kind(base) != "tree":
            raise ValueError("invalid selected base")
        first, first_identity = observe(base, agent, scope)
        second, second_identity = observe(base, agent, scope)
        if first != second or first_identity != second_identity:
            raise Unstable("observation changed")
        return second
    except Unstable:
        return {"schema": "revayat-novel/install-status@1", "status": "unstable",
                "cause": "state-changed"}
    except (OSError, ValueError, UnicodeError):
        return {"schema": "revayat-novel/install-status@1", "status": "unknown",
                "cause": "unverifiable-state"}
