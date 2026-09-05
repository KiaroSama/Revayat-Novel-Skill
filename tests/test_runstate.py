"""Resumability: never reuse cached output whose inputs moved.

The pipeline keeps everything on disk so a book can be picked up where it was
left. That is only safe while the cached output still answers the input it was
made from — merge yesterday's worksheets into a re-extracted book and the
result looks complete and is quietly wrong, because every id still resolves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bookir as ir
import chunk as chunking
import runstate


# --------------------------------------------------------------------------- #
# The record itself
# --------------------------------------------------------------------------- #

def test_a_missing_state_file_makes_every_stage_stale(tmp_path):
    """An unknown history is not evidence of a matching one."""
    state = runstate.RunState(tmp_path)
    for stage in runstate.STAGES:
        stale, reason = state.is_stale(stage, {"book": "abc"})
        assert stale is True
        assert runstate.STATE_NAME in reason


def test_recording_a_stage_makes_exactly_that_stage_fresh(tmp_path):
    state = runstate.RunState(tmp_path)
    state.record("chunk", {"book": "abc", "budget": "6000"}, {"chunks": 4})

    assert (tmp_path / runstate.STATE_NAME).exists()
    assert runstate.RunState(tmp_path).is_stale(
        "chunk", {"book": "abc", "budget": "6000"}) == (False, "")
    assert runstate.RunState(tmp_path).is_stale("build", {"book": "abc"})[0] is True


def test_the_reason_names_what_moved(tmp_path):
    state = runstate.RunState(tmp_path)
    state.record("chunk", {"book": "abc", "glossary": "def"})

    stale, reason = state.is_stale("chunk", {"book": "abc", "glossary": "xyz"})
    assert stale is True
    assert "glossary" in reason and "book" not in reason


def test_an_input_that_disappeared_is_a_change_not_a_crash(tmp_path):
    state = runstate.RunState(tmp_path)
    state.record("chunk", {"book": "abc", "glossary": "def"})
    stale, reason = state.is_stale("chunk", {"book": "abc", "glossary": ""})
    assert stale is True and "glossary" in reason


def test_a_value_that_is_not_a_hash_still_counts(tmp_path):
    """The chunk budget moves the unit boundaries, so it is an input too."""
    state = runstate.RunState(tmp_path)
    state.record("chunk", {"book": "abc", "budget": 6000})
    assert state.is_stale("chunk", {"book": "abc", "budget": 6000})[0] is False
    assert state.is_stale("chunk", {"book": "abc", "budget": 4000})[0] is True


def test_a_corrupt_state_file_is_read_as_empty_rather_than_fatal(tmp_path):
    """The one file whose whole job is to answer conservatively must not
    strand a working directory when it is unreadable."""
    (tmp_path / runstate.STATE_NAME).write_text("{not json", encoding="utf-8")
    state = runstate.RunState(tmp_path)
    assert state.recorded("chunk") is None
    assert state.is_stale("chunk", {"book": "abc"})[0] is True
    state.record("chunk", {"book": "abc"})
    assert json.loads((tmp_path / runstate.STATE_NAME).read_text(encoding="utf-8"))


def test_an_unknown_stage_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        runstate.RunState(tmp_path).record("translate-ish", {})


def test_hashing_a_file_that_is_not_there_answers_instead_of_raising(tmp_path):
    present = tmp_path / "book.json"
    present.write_text("{}", encoding="utf-8")
    assert runstate.file_hash(present)
    assert runstate.file_hash(tmp_path / "gone.json") == ""
    assert runstate.file_hash(None) == ""
    # A directory is not a file either; it must not raise on the way past.
    assert runstate.file_hash(tmp_path) == ""


# --------------------------------------------------------------------------- #
# chunk build / status
# --------------------------------------------------------------------------- #

def _work(tmp_path: Path, *, paragraphs: int = 6) -> tuple[Path, Path]:
    book = ir.new_book()
    book["blocks"] = [ir.make_block("heading", 1, level=1, text="Chapter One")] + [
        ir.make_block("paragraph", index, text=f"Paragraph number {index} of prose. " * 6)
        for index in range(2, paragraphs + 2)
    ]
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)
    return book_path, tmp_path / "chunks"


def _translate_everything(chunks: Path, manifest: dict) -> None:
    for entry in manifest["chunks"]:
        ir.write_text(chunks / entry["output"],
                      "\n".join(f"@@ {u} x\nمتن\n" for u in entry["unit_ids"]))


def _edit(book_path: Path) -> None:
    book = ir.load_book(book_path)
    book["blocks"][1]["text"] += " A sentence the translator has never seen."
    ir.save_book(book, book_path)


def test_a_first_build_records_what_it_was_cut_from(tmp_path):
    book_path, chunks = _work(tmp_path)
    chunking.build(book_path, chunks, glossary_path=None, budget=400)

    entry = runstate.RunState(tmp_path).recorded("chunk")
    assert entry is not None
    assert entry["inputs"]["book"] == chunking.source_digest(ir.load_book(book_path))
    assert entry["inputs"]["budget"] == "400"
    assert entry["outputs"]["manifest"]


def test_merging_the_translations_back_does_not_orphan_the_worksheets(tmp_path):
    """Merge writes the Persian into the same book.json the worksheets came from.

    Hashing that file would report every successful merge as a changed book,
    and the next build would refuse over source text that never moved.
    """
    import merge as merging

    book_path, chunks = _work(tmp_path)
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=400)
    _translate_everything(chunks, manifest)
    before = runstate.file_hash(book_path)

    assert merging.merge(book_path, chunks)["ok"]
    assert runstate.file_hash(book_path) != before, "merge did not touch the file"
    assert chunking.status(chunks)["stale"] is False

    # A re-extraction still moves it.
    _edit(book_path)
    assert chunking.status(chunks)["stale"] is True


def test_rebuilding_over_translations_of_a_different_book_is_refused(tmp_path):
    book_path, chunks = _work(tmp_path)
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=400)
    _translate_everything(chunks, manifest)
    _edit(book_path)

    with pytest.raises(chunking.StaleWorksheets) as refusal:
        chunking.build(book_path, chunks, glossary_path=None, budget=400)
    message = str(refusal.value)
    assert "book changed" in message
    assert "--force" in message
    # Nothing was overwritten on the way out.
    assert (chunks / manifest["chunks"][0]["output"]).read_text(encoding="utf-8")


def test_force_rebuilds_anyway(tmp_path):
    book_path, chunks = _work(tmp_path)
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=400)
    _translate_everything(chunks, manifest)
    _edit(book_path)

    rebuilt = chunking.build(book_path, chunks, glossary_path=None, budget=400,
                             force=True)
    assert rebuilt["chunks"]
    assert runstate.RunState(tmp_path).is_stale(
        "chunk", chunking._chunk_inputs(book_path, None, 400)) == (False, "")


def test_worksheets_nobody_has_answered_are_rebuilt_without_complaint(tmp_path):
    book_path, chunks = _work(tmp_path)
    chunking.build(book_path, chunks, glossary_path=None, budget=400)
    _edit(book_path)
    assert chunking.build(book_path, chunks, glossary_path=None, budget=400)["chunks"]


def test_a_changed_glossary_orphans_the_translations_too(tmp_path):
    import glossary as gl

    book_path, chunks = _work(tmp_path)
    glossary_path = tmp_path / "glossary.json"
    gl.save(gl.new_glossary(), glossary_path)
    manifest = chunking.build(book_path, chunks, glossary_path=glossary_path,
                              budget=400)
    _translate_everything(chunks, manifest)

    glossary = gl.load(glossary_path)
    glossary["entries"].append(gl.make_entry(1, "Elizabeth Bennet"))
    gl.save(glossary, glossary_path)

    with pytest.raises(chunking.StaleWorksheets) as refusal:
        chunking.build(book_path, chunks, glossary_path=glossary_path, budget=400)
    assert "glossary changed" in str(refusal.value)


def test_a_working_directory_with_no_run_state_behaves_exactly_as_before(tmp_path):
    """A first run must never be blocked by bookkeeping that does not exist."""
    book_path, chunks = _work(tmp_path)
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=400)
    _translate_everything(chunks, manifest)
    (tmp_path / runstate.STATE_NAME).unlink()
    _edit(book_path)

    assert chunking.build(book_path, chunks, glossary_path=None, budget=400)["chunks"]


def test_status_reports_staleness_beside_what_is_left_to_do(tmp_path):
    book_path, chunks = _work(tmp_path)
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=400)

    fresh = chunking.status(chunks)
    assert fresh["stale"] is False and fresh["stale_reason"] == ""
    assert fresh["total"] == len(manifest["chunks"])

    _edit(book_path)
    moved = chunking.status(chunks)
    assert moved["stale"] is True
    assert "book changed" in moved["stale_reason"]


def test_status_says_it_cannot_tell_rather_than_guessing(tmp_path):
    """``false`` there would be a claim, not a comparison."""
    book_path, chunks = _work(tmp_path)
    chunking.build(book_path, chunks, glossary_path=None, budget=400)

    (tmp_path / runstate.STATE_NAME).unlink()
    assert chunking.status(chunks)["stale"] is None

    chunking.build(book_path, chunks, glossary_path=None, budget=400)
    book_path.unlink()
    unknown = chunking.status(chunks)
    assert unknown["stale"] is None
    assert "not where the manifest says" in unknown["stale_reason"]


def test_status_survives_being_run_from_somewhere_else(tmp_path, monkeypatch):
    """The manifest stores the path as it was typed; a relative one must not
    hash as missing and report the book as changed on every resume."""
    book_path, chunks = _work(tmp_path)
    monkeypatch.chdir(tmp_path)
    chunking.build(Path("book.json"), Path("chunks"), glossary_path=None, budget=400)

    monkeypatch.chdir(tmp_path.parent)
    assert chunking.status(chunks)["stale"] is False


def test_the_cli_refuses_with_a_non_zero_exit_and_says_why(tmp_path, capsys):
    book_path, chunks = _work(tmp_path)
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=400)
    _translate_everything(chunks, manifest)
    _edit(book_path)

    arguments = ["build", "--book", str(book_path), "--out", str(chunks),
                 "--budget", "400"]
    assert chunking.main(arguments) == 2
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["ok"] is False
    assert refusal["refused"] == "stale-worksheets"
    assert "--force" in refusal["detail"]

    assert chunking.main(arguments + ["--force"]) == 0
    assert json.loads(capsys.readouterr().out)["chunks"]
