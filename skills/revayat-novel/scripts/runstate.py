"""Stage identity — never reuse output whose inputs moved.

The pipeline is resumable on purpose: the extraction, the glossary, the
worksheets and the translations all survive on disk between runs so a book
never has to be redone from the start. That is only safe while the cached
output still answers the input it was made from. Re-extract a corrected PDF,
then merge yesterday's worksheets, and the result looks complete and is quietly
wrong — every id still resolves, every count still matches, and the paragraphs
are answers to sentences the book no longer contains.

This module is the record that makes the question answerable: for each stage,
the SHA-256 of everything that stage depends on, in ``run-state.json`` in the
working directory. :meth:`RunState.is_stale` compares a stage's recorded inputs
against the ones a caller is about to use and names what moved.

It is a record, not a lock. Nothing here deletes, rewrites or refuses anything —
the caller decides what to do with the answer.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import bookir as ir

SCHEMA = "revayat-novel/runstate@1"

#: The stages that leave reusable output behind, in pipeline order.
STAGES = ("extract", "glossary", "chunk", "translate", "typography", "build")

#: Sits beside ``book.json`` in the working directory rather than inside any one
#: stage's output folder: staleness is a relationship *between* stages, and the
#: stage that has gone stale is often not the one holding the files.
STATE_NAME = "run-state.json"


def file_hash(path: str | os.PathLike[str] | None) -> str:
    """SHA-256 of a file, or ``""`` when there is no file to hash.

    A missing input has to hash differently from a present one rather than
    raise: "the glossary was deleted" is an answer about staleness, not a crash.
    """
    if path is None:
        return ""
    try:
        return ir.sha256_file(path)
    except OSError:
        return ""


class RunState:
    """The ``run-state.json`` of one working directory."""

    def __init__(self, work_dir: str | os.PathLike[str]) -> None:
        self.path = Path(work_dir) / STATE_NAME
        self.data = _read(self.path)

    # ----------------------------------------------------------------- #
    # Queries
    # ----------------------------------------------------------------- #

    def recorded(self, stage: str) -> dict[str, Any] | None:
        """What was recorded for ``stage``, or ``None`` if nothing ever was.

        Callers use this to tell "the inputs moved" from "this working
        directory predates the record" — only the first is a reason to refuse.
        """
        entry = self.data["stages"].get(stage)
        return entry if isinstance(entry, dict) else None

    def is_stale(self, stage: str, inputs: Mapping[str, Any]) -> tuple[bool, str]:
        """``(stale, reason)``; ``reason`` is empty only when it is fresh.

        A stage nobody recorded is stale, and so is a missing state file: an
        unknown history is not evidence of a matching one.
        """
        entry = self.recorded(stage)
        if entry is None:
            if not self.path.exists():
                return True, f"there is no {STATE_NAME} in {self.path.parent}"
            return True, f"stage {stage!r} has never been recorded"

        before = {str(key): str(value)
                  for key, value in (entry.get("inputs") or {}).items()}
        now = {str(key): str(value) for key, value in inputs.items()}
        moved = sorted(key for key in set(before) | set(now)
                       if before.get(key) != now.get(key))
        if moved:
            return True, f"{', '.join(moved)} changed since the last {stage} run"
        return False, ""

    # ----------------------------------------------------------------- #
    # Recording
    # ----------------------------------------------------------------- #

    def record(self, stage: str, inputs: Mapping[str, Any],
               outputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Write what ``stage`` was just run against, and return the entry.

        Values are stored as strings so a hash and a plain setting (a chunk
        budget, an OCR language) can live in the same map and compare the same
        way — the point is only whether it changed.
        """
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
        entry = {
            "inputs": {str(key): str(value) for key, value in inputs.items()},
            "outputs": {str(key): str(value)
                        for key, value in (outputs or {}).items()},
            "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.data["stages"][stage] = entry
        ir.write_text(self.path,
                      json.dumps(self.data, ensure_ascii=False, indent=1) + "\n")
        return entry


def _read(path: Path) -> dict[str, Any]:
    """The stored state, or an empty one.

    A missing *or unreadable* file means "nothing is known", which makes every
    stage stale. Raising here would strand a whole working directory over the
    one file whose only job is to answer conservatively.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("stages"), dict):
        return {"schema": SCHEMA, "stages": {}}
    data.setdefault("schema", SCHEMA)
    return data
