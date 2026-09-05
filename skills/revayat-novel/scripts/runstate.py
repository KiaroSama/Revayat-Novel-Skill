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

The same file also carries the finer record a page-at-a-time run needs: where
each source page has got to, how many times it has failed, and the hashes of
its source, its translation, its render and its QA report. Stage identity
answers for the book, and a book is finished one page at a time — "chunked" is
not an answer about page 214.

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

#: The lifecycle of one source page, in order. A page-at-a-time run needs its
#: own record because the stage-level one answers for the book: "the book was
#: chunked" says nothing about whether page 214 has been looked at, and a book
#: is finished one page at a time or not at all.
#:
#: The operations walk it: ``pagerun.build`` reaches ``extracted``,
#: ``pagerun.merge_page`` reaches ``translated`` then ``merged``,
#: ``renderqa.check`` reaches ``qa_passed`` or ``failed``, and
#: ``pagerun.accept`` reaches ``accepted`` only once each of those left real
#: evidence behind. Setting a state here is still a record and not a lock —
#: the gate that decides whether a page has earned ``accepted`` lives in
#: ``pagerun.accept``, where the evidence it weighs is.
PAGE_STATES = ("pending", "extracted", "translated", "merged", "rendered",
               "qa_passed", "accepted", "failed")

#: A page's hashes, in dependency order. ``source`` is the input; everything
#: after it is evidence *derived* from that input, so a moved source page
#: invalidates them and nothing else — not the page before it, not the book.
PAGE_HASHES = ("source", "translation", "render", "qa")

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
        self._save()
        return entry

    # ----------------------------------------------------------------- #
    # Per-page lifecycle
    # ----------------------------------------------------------------- #

    def page(self, page_no: int) -> dict[str, Any] | None:
        """The record for one source page, or ``None`` if it has none."""
        entry = self.data["pages"].get(str(int(page_no)))
        return entry if isinstance(entry, dict) else None

    def pages(self) -> dict[int, dict[str, Any]]:
        """Every page record, keyed by page number."""
        return {int(key): value
                for key, value in sorted(self.data["pages"].items(),
                                         key=lambda kv: int(kv[0]))
                if isinstance(value, dict)}

    def set_page(self, page_no: int, state: str, *,
                 hashes: Mapping[str, Any] | None = None,
                 error: str = "") -> dict[str, Any]:
        """Move one page to ``state`` and record the evidence for it.

        ``attempts`` counts failures rather than runs, because that is what a
        retry cap is about: a page that has been rendered five times and passed
        every time has not exhausted anything. Only ``failed`` increments it,
        and any other state clears the error that went with it.
        """
        if state not in PAGE_STATES:
            raise ValueError(f"unknown page state {state!r}; "
                             f"expected one of {PAGE_STATES}")
        entry = self.page(page_no) or {"state": "pending", "attempts": 0,
                                       "last_error": "", "hashes": {}}
        entry["state"] = state
        if state == "failed":
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
            entry["last_error"] = str(error)[:400]
        else:
            entry["last_error"] = str(error)[:400] if error else ""
        for name, value in (hashes or {}).items():
            if name not in PAGE_HASHES:
                raise ValueError(f"unknown page hash {name!r}; "
                                 f"expected one of {PAGE_HASHES}")
            entry.setdefault("hashes", {})[name] = str(value)
        entry["updated_utc"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S")
        self.data["pages"][str(int(page_no))] = entry
        self._save()
        return entry

    def note_page_source(self, page_no: int, source_hash: str) -> bool:
        """Record this page's source, and say whether that invalidated it.

        A corrected page has to discard its own translation, render and QA
        report — they answer text the book no longer contains — while leaving
        every other page exactly where it was. That containment is the whole
        reason the record is per page: re-extracting one bad scan should not
        cost a translator the other four hundred pages.
        """
        entry = self.page(page_no)
        if entry is None:
            self.set_page(page_no, "pending", hashes={"source": source_hash})
            return False
        if entry.get("hashes", {}).get("source", "") == str(source_hash):
            return False

        entry["hashes"] = {"source": str(source_hash)}
        entry["state"] = "pending"
        entry["attempts"] = 0
        entry["last_error"] = ""
        entry["updated_utc"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S")
        self.data["pages"][str(int(page_no))] = entry
        self._save()
        return True

    def _save(self) -> None:
        ir.write_text(self.path,
                      json.dumps(self.data, ensure_ascii=False, indent=1) + "\n")


def source_digest(book: dict[str, Any]) -> str:
    """Identity of everything a worksheet is cut from.

    The *source* side only. Hashing ``book.json`` itself would be wrong in the
    one direction that matters: merge writes the translations back into that
    same file, so every successful merge would report the worksheets it was
    built from as stale — and the next ``chunk build`` would refuse over a book
    whose source text never moved. A re-extraction changes this digest; a
    translation does not.
    """
    parts = [
        f"{block['id']}\x00{block['type']}\x00"
        f"{block.get('text') or ''}\x00{block.get('alt') or ''}"
        for block in book.get("blocks", [])
    ]
    parts += [f"{note['id']}\x00{note.get('text') or ''}"
              for note in book.get("footnotes", [])]
    return ir.sha256_bytes("\n".join(parts).encode("utf-8"))


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
        return {"schema": SCHEMA, "stages": {}, "pages": {}}
    data.setdefault("schema", SCHEMA)
    if not isinstance(data.get("pages"), dict):
        data["pages"] = {}
    return data
