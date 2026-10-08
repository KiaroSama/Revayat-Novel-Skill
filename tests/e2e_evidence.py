"""Copy a bounded exact allowlist from an owned synthetic E2E workspace."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

MAX_BYTES = 10 * 1024 * 1024
MAX_FILES = 50
STAGES = frozenset({"source", "glossary", "chunk", "merge", "typography", "meaning",
                    "fluency", "qa", "build", "package", "delivery"})
FIXED = frozenset({"book.json", "glossary.json", "assets/fig.png", "book.fa.docx",
                    "chunks/manifest.json", "review/manifest.json", "review/review.json",
                    "fluency/manifest.json", "fluency/fluency.json", "status.json"})
SHEETS = re.compile(r"(?:chunks/(?:out_)?chunk\d{4}\.md|(?:review|fluency)/(?:out_)?sheet_\d{4}\.md)")


def regular(path, *, directory=False):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Linked evidence path refused")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Evidence object type refused")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def admitted(root, relative):
    if not isinstance(relative, str) or "\\" in relative or relative not in FIXED and not SHEETS.fullmatch(relative):
        raise ValueError("Unlisted evidence path refused")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(p in (".", "..") or ":" in p for p in pure.parts):
        raise ValueError("Unsafe evidence path refused")
    path = root / relative
    for parent in reversed(path.parents):
        if parent == root or root in parent.parents:
            regular(parent, directory=True)
    if not path.resolve().is_relative_to(root):
        raise ValueError("Evidence path escapes workspace")
    regular(path)
    return path


def prepare(work, staging, *, stage, exit_code, job_sha, files):
    if not isinstance(stage, str) or stage not in STAGES or type(exit_code) is not int or not 1 <= exit_code <= 255:
        raise ValueError("Invalid failed stage status")
    if not isinstance(job_sha, str) or not re.fullmatch(r"[a-f0-9]{40,64}", job_sha):
        raise ValueError("Invalid job commit identity")
    if (not isinstance(files, list) or not all(isinstance(p, str) for p in files)
            or len(files) >= MAX_FILES or len(set(files)) != len(files)):
        raise ValueError("Invalid evidence enumeration")
    work = Path(os.path.abspath(work))
    staging = Path(os.path.abspath(staging))
    for path in (work, *work.parents):
        regular(path, directory=True)
    for path in staging.parents:
        if path.exists():
            regular(path, directory=True)
    if staging.exists() or staging == work or work in staging.parents or staging in work.parents:
        raise ValueError("Evidence staging must be new and disjoint")
    selected, records = [], []
    total = 0
    for relative in sorted(files):
        source = admitted(work, relative)
        before = regular(source)
        if before[2] + total > MAX_BYTES:
            raise ValueError("Evidence exceeds 10 MiB")
        with source.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != before[:2] or regular(source) != before:
                raise ValueError("Evidence identity changed before read")
            raw = handle.read(MAX_BYTES - total + 1)
        if regular(source) != before or len(raw) != before[2]:
            raise ValueError("Evidence changed during read")
        total += len(raw)
        if total > MAX_BYTES:
            raise ValueError("Evidence exceeds 10 MiB")
        selected.append((relative, raw))
        records.append({"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    manifest = {"schema": "revayat-novel/e2e-evidence@1", "synthetic": True,
                "job": "pipeline", "job_sha": job_sha, "stage": stage, "exit_code": exit_code,
                "retention_days": 7, "files": records, "total_bytes": total}
    encoded = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")
    if len(encoded) + total > MAX_BYTES:
        raise ValueError("Evidence plus manifest exceeds 10 MiB")
    staging.mkdir(parents=True, mode=0o700)
    for relative, raw in selected:
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            if handle.write(raw) != len(raw):
                raise OSError("Incomplete evidence write")
    # The upload action sees only this new dedicated set, never the workspace.
    actual = {p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()}
    if actual != set(files):
        raise ValueError("Evidence staging membership differs")
    for record in records:
        copied = admitted(staging, record["path"])
        if hashlib.sha256(copied.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("Evidence copy differs")
    # A partial manifest must never make this set eligible for CI upload.
    pending = staging / ".manifest-pending"
    try:
        with pending.open("xb") as handle:
            if handle.write(encoded) != len(encoded):
                raise OSError("Incomplete evidence manifest write")
            handle.flush()
            os.fsync(handle.fileno())
        regular(pending)
        if pending.read_bytes() != encoded:
            raise OSError("Evidence manifest verification failed")
        os.replace(pending, staging / "manifest.json")
    finally:
        if pending.exists():
            pending.unlink()
    return manifest
