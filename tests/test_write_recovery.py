"""Recovery owns the same lock as commit; evidence survives conflicts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import bookir as ir
import bookwrite


@pytest.fixture
def book_path(tmp_path):
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"] = [ir.make_block("paragraph", 1, text="A quiet evening.")]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def test_waiting_writer_never_recovers_a_live_commit(book_path, monkeypatch):
    side = book_path.with_name("evidence.json")
    ir.write_text(side, "before")
    ready, entered, finish = (threading.Event() for _ in range(3))
    failures = []
    write, take = ir.write_text, bookwrite._take_lock

    def pause(path, text):
        if Path(path) == book_path and threading.current_thread().name == "writer-a":
            ready.set()
            assert finish.wait(10), "writer A was not released"
        return write(path, text)

    def observed(path, actor):
        if actor == "writer-b":
            entered.set()
        return take(path, actor)

    def writer(actor):
        try:
            with bookwrite.transaction(book_path, actor=actor) as tx:
                tx.book["meta"]["title" if actor == "writer-a" else "author"] = actor
                if actor == "writer-a":
                    tx.side(side, "writer-a")
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(ir, "write_text", pause)
    monkeypatch.setattr(bookwrite, "_take_lock", observed)
    first = threading.Thread(target=writer, args=("writer-a",), name="writer-a")
    second = threading.Thread(target=writer, args=("writer-b",), name="writer-b")
    first.start()
    try:
        assert ready.wait(5), "writer A did not reach its commit barrier"
        second.start()
        assert entered.wait(5), "writer B never attempted to acquire the lock"
        assert side.read_text(encoding="utf-8") == "writer-a"
        assert bookwrite.journal_path(book_path).is_file()
    finally:
        finish.set()
        first.join(10)
        if second.ident is not None:
            second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    landed = ir.load_book(book_path)
    assert (landed["meta"]["title"], landed["meta"]["author"]) == ("writer-a", "writer-b")
    assert side.read_text(encoding="utf-8") == "writer-a"


def test_age_does_not_prove_a_live_lock_is_abandoned(book_path, monkeypatch):
    monkeypatch.setattr(bookwrite, "WAIT_SECONDS", 0.01)
    with bookwrite.transaction(book_path, actor="live"):
        os.utime(bookwrite.lock_path(book_path), (1, 1))
        with pytest.raises(bookwrite.Refused, match="writer|lock") as refusal:
            with bookwrite.transaction(book_path, actor="contender"):
                pytest.fail("the live lock was stolen")
        assert refusal.value.reason == "locked"


def interrupt_commit(book_path, side, monkeypatch):
    write = ir.write_text

    def fail_book(path, text):
        if Path(path).resolve() == book_path.resolve():
            raise OSError("injected book replacement failure")
        return write(path, text)

    with monkeypatch.context() as patch:
        patch.setattr(ir, "write_text", fail_book)
        with pytest.raises(OSError, match="injected book replacement"):
            with bookwrite.transaction(book_path, actor="interrupted") as tx:
                tx.book["meta"]["title"] = "after"
                tx.side(side, "after")


def test_recovery_preserves_an_independent_side_file_edit(book_path, monkeypatch):
    side = book_path.with_name("evidence.json")
    ir.write_text(side, "before")
    interrupt_commit(book_path, side, monkeypatch)
    ir.write_text(side, "independent")
    with pytest.raises(bookwrite.Refused) as refusal:
        bookwrite.recover(book_path)
    assert refusal.value.reason == "unresolved-sidecar"
    assert side.read_text(encoding="utf-8") == "independent"
    assert bookwrite.journal_path(book_path).is_file()


def test_recovery_paths_do_not_follow_the_callers_directory(book_path, monkeypatch):
    side = book_path.with_name("evidence.json")
    ir.write_text(side, "before")
    with monkeypatch.context() as patch:
        patch.chdir(book_path.parent)
        interrupt_commit(Path("book.json"), Path("evidence.json"), patch)
    elsewhere = book_path.parent / "elsewhere"
    elsewhere.mkdir()
    unrelated = elsewhere / "evidence.json"
    ir.write_text(unrelated, "unrelated")
    monkeypatch.chdir(elsewhere)
    assert bookwrite.recover(book_path)["direction"] == "back"
    assert side.read_text(encoding="utf-8") == "before"
    assert unrelated.read_text(encoding="utf-8") == "unrelated"


def test_commit_rechecks_the_snapshot_even_with_an_owned_lock(book_path):
    with pytest.raises(bookwrite.Refused) as refusal:
        with bookwrite.transaction(book_path, actor="cooperating") as tx:
            tx.book["meta"]["title"] = "my edit"
            external = ir.load_book(book_path)
            external["meta"]["title"] = "external edit"
            ir.save_book(external, book_path)
    assert refusal.value.reason == "lost-update"
    assert ir.load_book(book_path)["meta"]["title"] == "external edit"


@pytest.mark.parametrize("value", [[], None, 42, "text", {"schema": "unknown"}])
def test_invalid_journal_is_a_preserved_structured_refusal(book_path, value):
    journal = bookwrite.journal_path(book_path)
    ir.write_text(journal, json.dumps(value))
    before = book_path.read_bytes()
    with pytest.raises(bookwrite.Refused) as refusal:
        bookwrite.recover(book_path)
    assert refusal.value.reason in {"invalid-journal", "unsupported-journal"}
    assert json.loads(journal.read_text(encoding="utf-8")) == value
    assert book_path.read_bytes() == before


def test_side_only_failure_rolls_back_instead_of_claiming_commit(book_path, monkeypatch):
    first, second = (book_path.with_name(name) for name in ("first.json", "second.json"))
    ir.write_text(first, "old-first")
    ir.write_text(second, "old-second")
    write = ir.write_text

    def fail_second(path, text):
        if Path(path) == second:
            raise OSError("injected second side failure")
        return write(path, text)

    with monkeypatch.context() as patch:
        patch.setattr(ir, "write_text", fail_second)
        with pytest.raises(OSError, match="injected second side"):
            with bookwrite.transaction(book_path, actor="side-only") as tx:
                tx.side(first, "new-first")
                tx.side(second, "new-second")
    assert bookwrite.recover(book_path)["direction"] == "back"
    assert first.read_text(encoding="utf-8") == "old-first"
    assert second.read_text(encoding="utf-8") == "old-second"


def test_crashed_process_releases_its_os_lock_without_aging(book_path):
    script = ("import os,sys; from pathlib import Path; import bookwrite; "
              "held=bookwrite._take_lock(Path(sys.argv[1]),'crash'); "
              "print('owned',flush=True); os._exit(0)")
    env = dict(os.environ, PYTHONPATH=str(Path(bookwrite.__file__).parent), PYTHONIOENCODING="utf-8")
    process = subprocess.Popen([sys.executable, "-c", script, str(book_path)],
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env)
    try:
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0 and stdout.strip() == "owned", stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    with bookwrite.transaction(book_path, actor="successor") as tx:
        tx.book["meta"]["title"] = "recovered lock"
    assert ir.load_book(book_path)["meta"]["title"] == "recovered lock"


def test_releasing_an_old_owner_cannot_release_a_new_owner(book_path, monkeypatch):
    monkeypatch.setattr(bookwrite, "WAIT_SECONDS", 0.01)
    first = bookwrite._take_lock(book_path, "first")
    first.release()
    second = bookwrite._take_lock(book_path, "second")
    try:
        first.release()
        with pytest.raises(bookwrite.Refused) as refusal:
            bookwrite._take_lock(book_path, "third")
        assert refusal.value.reason == "locked"
    finally:
        second.release()


def test_two_recovery_contenders_share_one_owned_lock(book_path, monkeypatch):
    side = book_path.with_name("evidence.json")
    ir.write_text(side, "before")
    interrupt_commit(book_path, side, monkeypatch)
    ready, entered, finish = (threading.Event() for _ in range(3))
    write, take = ir.write_text, bookwrite._take_lock
    results, failures = [], []

    def pause(path, text):
        if Path(path) == side and threading.current_thread().name == "first-recovery":
            ready.set()
            assert finish.wait(10)
        return write(path, text)

    def observed(path, actor):
        if threading.current_thread().name == "second-recovery":
            entered.set()
        return take(path, actor)

    def recover():
        try:
            results.append(bookwrite.recover(book_path))
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(ir, "write_text", pause)
    monkeypatch.setattr(bookwrite, "_take_lock", observed)
    first = threading.Thread(target=recover, name="first-recovery")
    second = threading.Thread(target=recover, name="second-recovery")
    first.start()
    try:
        assert ready.wait(5)
        second.start()
        assert entered.wait(5)
        assert side.read_text(encoding="utf-8") == "after"
    finally:
        finish.set()
        first.join(10)
        if second.ident is not None:
            second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    assert sorted(result["recovered"] for result in results) == [False, True]
    assert side.read_text(encoding="utf-8") == "before"


@pytest.mark.parametrize("side_only", [False, True])
def test_cleanup_failure_is_a_committed_recoverable_result(book_path, monkeypatch, side_only):
    side = book_path.with_name("evidence.json")
    ir.write_text(side, "before")
    journal = bookwrite.journal_path(book_path)
    unlink = Path.unlink

    def fail_cleanup(path, *args, **kwargs):
        if path == journal:
            raise OSError("injected journal cleanup failure")
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_cleanup)
        with pytest.raises(OSError, match="injected journal cleanup"):
            with bookwrite.transaction(book_path, actor="cleanup") as tx:
                if not side_only:
                    tx.book["meta"]["title"] = "committed"
                tx.side(side, "after")
    assert side.read_text(encoding="utf-8") == "after"
    assert bookwrite.recover(book_path)["direction"] == "forward"
    assert side.read_text(encoding="utf-8") == "after"
    assert not journal.exists()


def test_recovery_preserves_a_third_state_book(book_path, monkeypatch):
    side = book_path.with_name("evidence.json")
    ir.write_text(side, "before")
    interrupt_commit(book_path, side, monkeypatch)
    external = ir.load_book(book_path)
    external["meta"]["title"] = "independent"
    ir.save_book(external, book_path)
    before = book_path.read_bytes(), side.read_bytes()
    with pytest.raises(bookwrite.Refused) as refusal:
        bookwrite.recover(book_path)
    assert refusal.value.reason == "unresolved-journal"
    assert (book_path.read_bytes(), side.read_bytes()) == before


def test_recovery_resumes_after_its_own_write_failure(book_path, monkeypatch):
    first, second = (book_path.with_name(name) for name in ("first.json", "second.json"))
    for path in (first, second):
        ir.write_text(path, "before")
    write = ir.write_text

    def fail_at(target):
        def write_failure(path, body):
            if Path(path) == target:
                raise OSError("injected recovery fault")
            return write(path, body)
        return write_failure

    with monkeypatch.context() as patch:
        patch.setattr(ir, "write_text", fail_at(book_path))
        with pytest.raises(OSError, match="injected recovery fault"):
            with bookwrite.transaction(book_path, actor="interrupted") as tx:
                tx.book["meta"]["title"] = "after"
                tx.side(first, "after")
                tx.side(second, "after")
    with monkeypatch.context() as patch:
        patch.setattr(ir, "write_text", fail_at(second))
        with pytest.raises(OSError, match="injected recovery fault"):
            bookwrite.recover(book_path)
    assert first.read_text(encoding="utf-8") == "before"
    assert second.read_text(encoding="utf-8") == "after"
    assert bookwrite.recover(book_path)["direction"] == "back"
    assert second.read_text(encoding="utf-8") == "before"
