"""One way to change ``book.json``: locked, validated, journalled, atomic.

`ir.write_text` has always replaced a file atomically, and atomic replacement is
not the property a book needs. Three failures it does not touch, all of them
measured on this pipeline:

* **A lost update.** A writer loads the book, works, and saves. Anything another
  writer committed in between is gone — including an edit to a field this writer
  never looked at, which is the case nobody notices, because the file it wrote is
  perfectly well-formed.
* **A half-committed pair.** `fluency apply` writes the book and *then* its
  sidecar. A failure between the two leaves the Persian changed with nothing
  recording that it was applied, so the next run applies the same edits again.
* **An invalid book saved.** A replacement carrying `[[fn:fn0099]]`, a note the
  book does not have, was written straight to disk: measured, `validate_book`
  then reported `unknown footnote ref fn0099` on a file the pipeline had just
  accepted. Validation after the write is a report, not a gate.

So every writer that changes the book goes through one transaction:

1. finish any transaction an interrupted run left behind (:func:`recover`),
2. take the lock,
3. read the book **inside** the lock — so the caller's edit applies to what is
   actually there, not to a copy from before the wait,
4. hand the caller that copy to mutate,
5. validate the result whole, and refuse without writing anything if it fails,
6. write a journal naming every file, with the before and after of each,
7. write the side files, then the book — the book last, so the book's own digest
   says whether the transaction committed,
8. delete the journal.

A crash at any step leaves a state the next writer resolves without a human:
the book still matches ``before`` → roll the side files back; it matches
``after`` → roll them forward; it matches neither → refuse and keep the journal,
because a third writer changed the book mid-transaction and guessing which way to
go could destroy either one's work.

``expect`` is the other half of the same argument. A caller that decided
something from an earlier read (a recorded revision, an approval) passes the
digest it read; if the book has moved since, the decision may no longer hold and
the transaction refuses rather than overwriting. Neither this nor the lock ever
discards the other writer's content — the refusal is the point.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import bookir as ir
import notegraph

SCHEMA = "revayat-novel/bookwrite@1"

#: How long a lock is believed. A writer holding one is doing a few hundred
#: milliseconds of work on a loaded book; two minutes means the holder died.
LOCK_SECONDS = 120.0

#: How long to wait for a live lock before refusing. Bounded on purpose, and
#: polled rather than slept blind: the answer is a file appearing or not.
WAIT_SECONDS = 5.0
POLL_SECONDS = 0.05


class Refused(Exception):
    """The transaction did not happen, and nothing was written.

    Carries the machine-readable reason the CLI reports, so a caller turns it
    into its own refusal shape without re-deriving why.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def lock_path(book_path: Path) -> Path:
    return Path(str(book_path) + ".lock")


def journal_path(book_path: Path) -> Path:
    return Path(str(book_path) + ".journal.json")


def serialise(book: dict[str, Any]) -> str:
    """Exactly what :func:`bookir.save_book` would write, without writing it.

    One formatter, because the digest this module compares has to be the digest
    of the bytes that actually land.
    """
    book["stats"] = ir.book_stats(book)
    return json.dumps(book, ensure_ascii=False, indent=1) + "\n"


def digest(text: str) -> str:
    return ir.sha256_bytes(text.encode("utf-8"))


def file_digest(path: Path) -> str:
    """The digest of what is on disk, or ``""`` when nothing is."""
    try:
        return digest(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""


# --------------------------------------------------------------------------- #
# The lock
# --------------------------------------------------------------------------- #

def _take_lock(book_path: Path, actor: str) -> Path:
    path = lock_path(book_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            handle = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            held = _lock_age(path)
            if held is not None and held > LOCK_SECONDS:
                # The holder is gone: a lock nobody can release would stop the
                # pipeline for ever, which is a worse failure than the race it
                # was protecting against.
                path.unlink(missing_ok=True)
                continue
            if time.monotonic() >= deadline:
                raise Refused(
                    "locked",
                    f"another writer holds {path.name} "
                    f"({_lock_holder(path)}). Nothing was written; run again "
                    f"when it finishes.") from None
            time.sleep(POLL_SECONDS)
            continue
        with os.fdopen(handle, "w", encoding="utf-8") as writer:
            json.dump({"pid": os.getpid(), "actor": actor, "at": time.time()},
                      writer)
        return path


def _lock_age(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _lock_holder(path: Path) -> str:
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
        return f"{held.get('actor', '?')} pid {held.get('pid', '?')}"
    except (OSError, json.JSONDecodeError):
        return "unknown"


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #

def recover(book_path: Path) -> dict[str, Any]:
    """Finish or undo whatever an interrupted transaction left. Never guesses.

    Called by every transaction before it starts, so the recovery path is the
    ordinary path rather than a command somebody has to remember.
    """
    path = journal_path(Path(book_path))
    if not path.is_file():
        return {"recovered": False}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as failure:
        raise Refused("unreadable-journal",
                      f"{path} cannot be read ({failure}), so it is unknown "
                      f"whether the last write committed. Nothing was "
                      f"touched.") from None

    now = file_digest(Path(book_path))
    book = record.get("book") or {}
    side = record.get("side") or []
    if now == book.get("after"):
        direction = "forward"
    elif now == book.get("before"):
        direction = "back"
    else:
        raise Refused(
            "unresolved-journal",
            f"{path} describes a write by {record.get('actor')!r} that left the "
            f"book at neither its before nor its after digest, so something else "
            f"has changed it. Nothing was touched: the journal names both states "
            f"and a person has to decide.")

    for entry in side:
        target = Path(entry["path"])
        wanted = entry.get("after" if direction == "forward" else "before")
        if wanted is None:
            target.unlink(missing_ok=True)
        elif file_digest(target) != digest(wanted):
            ir.write_text(target, wanted)
    path.unlink(missing_ok=True)
    return {"recovered": True, "direction": direction,
            "actor": record.get("actor", ""),
            "files": [entry["path"] for entry in side]}


# --------------------------------------------------------------------------- #
# The transaction
# --------------------------------------------------------------------------- #

class Transaction:
    """A book to mutate, plus the side files that commit with it."""

    def __init__(self, book_path: Path, book: dict[str, Any], before: str,
                 actor: str) -> None:
        self.book_path = Path(book_path)
        self.book = book
        self.before = before
        self.actor = actor
        self._side: list[tuple[Path, str]] = []

    def side(self, path: Path | str, text: str) -> None:
        """Queue a file that must land if and only if the book does."""
        self._side.append((Path(path), text))

    # -- committing ------------------------------------------------------- #

    def _validate(self) -> None:
        # The structure and the footnote graph, together, because a book whose
        # markers do not resolve is as unshippable as one with a duplicate block
        # id — and the graph was checked by nothing at the moment of writing: a
        # canonical reference inside a note body merged clean.
        problems = ir.validate_book(self.book) + notegraph.problems(self.book)
        if problems:
            raise Refused(
                "invalid-book",
                "the change would leave the book invalid, so nothing was "
                "written: " + "; ".join(problems[:6]))

    def commit(self) -> dict[str, Any]:
        self._validate()
        text = serialise(self.book)
        after = digest(text)
        if after == self.before and not self._side:
            # Nothing to do, and a journal for a write that changes nothing is a
            # recovery decision waiting to be made for no reason.
            return {"ok": True, "changed": False, "digest": after}

        record = {
            "schema": SCHEMA,
            "actor": self.actor,
            "at": time.time(),
            "book": {"path": str(self.book_path), "before": self.before,
                     "after": after},
            "side": [
                {"path": str(path),
                 "before": (path.read_text(encoding="utf-8")
                            if path.is_file() else None),
                 "after": body}
                for path, body in self._side],
        }
        journal = journal_path(self.book_path)
        ir.write_text(journal, json.dumps(record, ensure_ascii=False, indent=1))
        for path, body in self._side:
            ir.write_text(path, body)
        ir.write_text(self.book_path, text)
        journal.unlink(missing_ok=True)
        return {"ok": True, "changed": after != self.before, "digest": after,
                "side": [str(path) for path, _ in self._side]}


class _Open:
    """The context manager :func:`transaction` returns."""

    def __init__(self, book_path: Path, actor: str, expect: str | None) -> None:
        self.book_path = Path(book_path)
        self.actor = actor
        self.expect = expect
        self.lock: Path | None = None
        self.tx: Transaction | None = None
        self.result: dict[str, Any] = {}

    def __enter__(self) -> Transaction:
        recover(self.book_path)
        self.lock = _take_lock(self.book_path, self.actor)
        try:
            before = file_digest(self.book_path)
            if not before:
                raise Refused("no-book", f"{self.book_path} is not readable")
            if self.expect is not None and self.expect != before:
                raise Refused(
                    "lost-update",
                    f"{self.book_path.name} has changed since this command read "
                    f"it, so the decision it made may no longer hold. Nothing "
                    f"was written and nobody's work was discarded; read it again "
                    f"and repeat.")
            self.tx = Transaction(self.book_path,
                                  ir.load_book(self.book_path), before,
                                  self.actor)
            return self.tx
        except BaseException:
            self._release()
            raise

    def __exit__(self, kind, value, traceback) -> bool:
        try:
            if kind is None and self.tx is not None:
                self.result = self.tx.commit()
        finally:
            self._release()
        return False

    def _release(self) -> None:
        if self.lock is not None:
            self.lock.unlink(missing_ok=True)
            self.lock = None


def replace(book_path: Path | str, book: dict[str, Any], *, actor: str,
            expect: str) -> dict[str, Any]:
    """Commit a book the caller has already computed, in one guarded step.

    For a stage that reads the book, works on it at length and writes once —
    `merge`, `falint fix`. Mutating inside the lock would mean holding it for the
    whole computation and restructuring several hundred lines; ``expect`` gives
    the same guarantee from the other side: the file has not moved since the
    caller read it, so replacing it cannot discard anybody's work. If it *has*
    moved, this refuses and the caller re-runs — which is why the refusal exists
    rather than a merge strategy nobody could verify.
    """
    with transaction(book_path, actor=actor, expect=expect) as opened:
        opened.book.clear()
        opened.book.update(book)
    return {"ok": True}


def transaction(book_path: Path | str, *, actor: str,
                expect: str | None = None) -> _Open:
    """Open a write transaction on ``book_path``.

    ``actor`` names the command, and travels into the lock and the journal so a
    stuck or interrupted write says who was doing it. ``expect`` is the digest
    the caller read earlier, when it has made a decision from that read.
    """
    return _Open(Path(book_path), actor, expect)
