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

1. acquire the OS lock (held until commit or recovery finishes),
2. finish any interrupted transaction while holding that same lock,
3. read the book **inside** the lock — so the caller's edit applies to what is
   actually there, not to a copy from before the wait,
4. hand the caller that copy to mutate,
5. validate the result whole, and refuse without writing anything if it fails,
6. write a journal naming every file, with the before and after of each,
7. write the side files, then the book — the book last, so the book's own digest
   says whether the transaction committed,
8. delete the journal.

A crash leaves a state the next writer can reconcile:
the book still matches ``before`` → roll the side files back; it matches
``after`` → roll them forward; it matches neither → refuse and keep the journal,
because a third writer changed the book mid-transaction and guessing which way to
go could destroy either one's work.

``expect`` is the other half of the same argument. A caller that decided
something from an earlier read (a recorded revision, an approval) passes the
digest it read; if the book has moved since, the decision may no longer hold and
the transaction refuses rather than overwriting. Neither this nor the lock ever
discards a cooperating writer's content. An external writer ignoring the lock
is detected at snapshot checks, but a filesystem replacement is not a
compare-and-swap: the final check-to-replace interval cannot be protected from
such a writer. Independent side-file edits are preserved and reported.

Lock files persist. Their existence, timestamp and recorded PID are diagnostic;
only the OS-held handle grants ownership. A crashed process releases its lock.
Side-only transactions use a journal commit marker because their book digest
cannot distinguish an interrupted write from a committed one.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import bookir as ir
import notegraph

SCHEMA = "revayat-novel/bookwrite@2"
LEGACY_SCHEMA = "revayat-novel/bookwrite@1"

#: How long to wait for a live lock before refusing. Bounded on purpose, and
#: polled rather than slept blind: the answer is a file appearing or not.
WAIT_SECONDS = 5.0
POLL_SECONDS = 0.05


class Refused(ValueError):
    """A named refusal; an existing journal may still require recovery.

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
        return digest(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        return ""


# --------------------------------------------------------------------------- #
# The lock
# --------------------------------------------------------------------------- #

class _Lock:
    def __init__(self, fd: int) -> None:
        self.fd: int | None = fd

    def release(self) -> None:
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            _os_lock(fd, release=True)
        finally:
            os.close(fd)


def _os_lock(fd: int, *, release: bool = False) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)


def _take_lock(book_path: Path, actor: str) -> _Lock:
    path = lock_path(book_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + WAIT_SECONDS
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                _os_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise Refused("locked", f"another writer holds {path.name}; "
                                  "retry when it finishes") from None
                time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
        body = json.dumps({"pid": os.getpid(), "actor": actor, "at": time.time()})
        os.write(fd, body.encode("utf-8"))
        os.ftruncate(fd, os.lseek(fd, 0, os.SEEK_CUR))
        return _Lock(fd)
    except BaseException:
        os.close(fd)
        raise


def _text(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return None


# --------------------------------------------------------------------------- #
# Recovery
# --------------------------------------------------------------------------- #

def recover(book_path: Path) -> dict[str, Any]:
    """Reconcile a journal while exclusively owning the book's OS lock."""
    book_path = Path(book_path).resolve()
    owned = _take_lock(book_path, "recovery")
    try:
        return _recover_locked(book_path)
    finally:
        owned.release()


def _read_journal(path: Path, book_path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as failure:
        raise Refused("unreadable-journal", f"cannot read {path}: {failure}") from None
    if not isinstance(record, dict):
        raise Refused("invalid-journal", "journal must be an object; evidence retained")
    if record.get("schema") not in (SCHEMA, LEGACY_SCHEMA):
        raise Refused("unsupported-journal", "unknown journal schema; evidence retained")
    book, side = record.get("book"), record.get("side")
    valid = (isinstance(book, dict) and isinstance(side, list)
             and isinstance(record.get("actor"), str))
    if not valid or not all(isinstance(book.get(k), str) for k in ("path", "before", "after")):
        raise Refused("invalid-journal", "invalid book or side-file record")
    if (not Path(book["path"]).is_absolute()
            or Path(book["path"]).resolve() != book_path
            or not re.fullmatch(r"[0-9a-f]{64}|", book["before"])
            or not re.fullmatch(r"[0-9a-f]{64}", book["after"])):
        raise Refused("invalid-journal", "journal has a different book or invalid digest")
    if record["schema"] == SCHEMA:
        if record.get("phase") not in ("prepared", "committed"):
            raise Refused("invalid-journal", "journal has no valid commit phase")
    elif book["before"] == book["after"]:
        raise Refused("unsupported-journal", "legacy side-only commit is ambiguous")
    seen = {book_path, path, lock_path(book_path)}
    for entry in side:
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                or not all(k in entry and (entry[k] is None or isinstance(entry[k], str))
                           for k in ("before", "after"))):
            raise Refused("invalid-journal", "invalid side-file state")
        target = Path(entry["path"])
        if (not target.is_absolute() or target.resolve() != target
                or not target.is_relative_to(book_path.parent) or target in seen):
            raise Refused("invalid-journal", "unsafe or duplicate side-file path")
        seen.add(target)
    return record


def _recover_locked(book_path: Path) -> dict[str, Any]:
    path = journal_path(book_path)
    if not path.is_file():
        return {"recovered": False}
    record = _read_journal(path, book_path)
    try:
        present_book = _text(book_path)
    except (OSError, UnicodeError) as failure:
        raise Refused("unreadable-book", str(failure)) from None
    now = digest(present_book) if present_book is not None else ""
    book, side = record["book"], record["side"]
    if now == book["before"] == book["after"]:
        direction = "forward" if record["phase"] == "committed" else "back"
    elif now == book["after"]:
        direction = "forward"
    elif now == book["before"]:
        direction = "back"
    else:
        raise Refused(
            "unresolved-journal",
            f"{path} describes a write by {record.get('actor')!r} that left the "
            f"book at neither its before nor its after digest, so something else "
            f"has changed it. Nothing was touched: the journal names both states "
            f"and a person has to decide.")

    # Check every file before touching any. A partially finished recovery is
    # resumable: either recorded state is acceptable, an independent third is not.
    for entry in side:
        try:
            present = _text(Path(entry["path"]))
        except (OSError, UnicodeError) as failure:
            raise Refused("unreadable-sidecar", str(failure)) from None
        if present not in (entry["before"], entry["after"]):
            raise Refused("unresolved-sidecar", f"independent edit at {entry['path']}; "
                          "journal and files retained")
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
        self._side_expected: dict[Path, str] = {}

    def side(self, path: Path | str, text: str, *, expect: str | None = None) -> None:
        """Queue UTF-8 evidence; relative paths are rooted beside the book."""
        path = Path(path)
        if not path.is_absolute():
            path = self.book_path.parent / path
        path = path.resolve()
        if (not path.is_relative_to(self.book_path.parent)
                or path in (self.book_path, lock_path(self.book_path), journal_path(self.book_path))
                or any(path == existing for existing, _ in self._side)
                or not isinstance(text, str)):
            raise Refused("invalid-sidecar", "side files must be unique UTF-8 files inside the book work directory")
        self._side.append((path, text))
        if expect is not None:
            self._side_expected[path] = expect

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
        self._check_snapshot()
        if after == self.before and not self._side:
            # Nothing to do, and a journal for a write that changes nothing is a
            # recovery decision waiting to be made for no reason.
            return {"ok": True, "changed": False, "digest": after}

        record = {
            "schema": SCHEMA,
            "phase": "prepared",
            "actor": self.actor,
            "at": time.time(),
            "book": {"path": str(self.book_path), "before": self.before,
                     "after": after},
            "side": [
                {"path": str(path),
                 "before": _text(path),
                 "after": body}
                for path, body in self._side],
        }
        journal = journal_path(self.book_path)
        for entry in record["side"]:
            path = Path(entry["path"])
            actual = digest(entry["before"]) if entry["before"] is not None else ""
            if path in self._side_expected and actual != self._side_expected[path]:
                raise Refused("sidecar-changed", f"validated evidence changed at {path}; nothing was written")
        ir.write_text(journal, json.dumps(record, ensure_ascii=False, indent=1))
        for entry in record["side"]:
            path = Path(entry["path"])
            if _text(path) != entry["before"]:
                raise Refused("sidecar-changed", f"evidence changed at {path}; journal retained")
            ir.write_text(path, entry["after"])
        self._check_snapshot()
        for path, body in self._side:
            if _text(path) != body:
                raise Refused("sidecar-changed", f"evidence changed before the book commit at {path}; journal retained")
        if after != self.before:
            ir.write_text(self.book_path, text)
        else:
            record["phase"] = "committed"
            ir.write_text(journal, json.dumps(record, ensure_ascii=False, indent=1))
        journal.unlink(missing_ok=True)
        return {"ok": True, "changed": after != self.before, "digest": after,
                "side": [str(path) for path, _ in self._side]}

    def _check_snapshot(self) -> None:
        try:
            text = _text(self.book_path)
        except (OSError, UnicodeError) as error:
            raise Refused("unreadable-book", str(error)) from None
        if (digest(text) if text is not None else "") != self.before:
            raise Refused("lost-update", "book changed outside this transaction; "
                          "the external edit and any recovery journal are retained")


class _Open:
    """The context manager :func:`transaction` returns."""

    def __init__(self, book_path: Path, actor: str, expect: str | None) -> None:
        self.book_path = Path(book_path).resolve()
        self.actor = actor
        self.expect = expect
        self.lock: _Lock | None = None
        self.tx: Transaction | None = None
        self.result: dict[str, Any] = {}

    def __enter__(self) -> Transaction:
        self.lock = _take_lock(self.book_path, self.actor)
        try:
            _recover_locked(self.book_path)
            snapshot = _text(self.book_path)
            if snapshot is None and self.expect != "":
                raise Refused("no-book", f"{self.book_path} is not readable")
            before = digest(snapshot) if snapshot is not None else ""
            if self.expect is not None and self.expect != before:
                raise Refused(
                    "lost-update",
                    f"{self.book_path.name} has changed since this command read "
                    f"it, so the decision it made may no longer hold. Nothing "
                    f"was written and nobody's work was discarded; read it again "
                    f"and repeat.")
            try:
                book = ir.decode_book(snapshot) if snapshot is not None else {}
            except ValueError as failure:
                raise Refused("invalid-book", str(failure)) from None
            self.tx = Transaction(self.book_path, book, before, self.actor)
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
            self.lock.release()
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
