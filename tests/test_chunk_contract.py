"""What ``chunk status`` knows, and what a worksheet is allowed to cost.

``status`` is the resume view: an operator and a script both ask it what is left
to do. Two states it could not distinguish made it answer wrongly in opposite
directions — a job it listed as unfinished but never offered, and a job it
called done on the strength of the file merely being non-empty.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import chunk as chunking  # noqa: E402


def _book(tmp_path: Path, texts: list[str]) -> Path:
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    for index, text in enumerate(texts, start=1):
        book["blocks"].append(ir.make_block("paragraph", index, text=text))
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _two_chunks(tmp_path: Path) -> tuple[Path, Path, dict]:
    """At least two worksheets, the first carrying more than one unit.

    Measured rather than guessed: the budget bounds the *rendered* worksheet, so
    the fixed scaffolding and the 450 characters of neighbour context either side
    take most of a small budget. This shape produces two worksheets of ten and
    six units, and the assertions below are what keeps it honest if that drifts.
    """
    book_path = _book(tmp_path, [
        f"Paragraph number {i} of ordinary prose here. " * 2
        for i in range(1, 17)])
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=1300)
    assert len(manifest["chunks"]) >= 2, manifest
    assert len(manifest["chunks"][0]["unit_ids"]) >= 2, manifest
    return book_path, chunks, manifest


def _answer(chunks: Path, entry: dict) -> None:
    ir.write_text(chunks / entry["output"], "\n".join(
        f"@@ {unit_id} {entry['unit_kinds'][unit_id]}\nترجمه.\n"
        for unit_id in entry["unit_ids"]))


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #

def test_an_empty_output_file_is_offered_as_the_next_job(tmp_path):
    """It was listed as empty and then never selected.

    ``next`` came from the missing-file list alone, so a job whose output exists
    and is blank — a touched file, an interrupted write, a model that answered
    with nothing — was reported as outstanding and never handed to anybody. The
    run looks resumable and stops making progress.
    """
    _book_path, chunks, manifest = _two_chunks(tmp_path)
    first = manifest["chunks"][0]
    ir.write_text(chunks / first["output"], "")

    progress = chunking.status(chunks)

    assert first["id"] in progress["empty"]
    assert progress["next"] == first["id"], progress


def test_a_file_of_unrelated_text_does_not_count_as_translated(tmp_path):
    """Existence is not an answer.

    Any non-blank bytes counted as a finished worksheet, so a crash log, a
    refusal from the model, or a half-written file all read as "done" — and the
    job is never offered again.
    """
    _book_path, chunks, manifest = _two_chunks(tmp_path)
    first = manifest["chunks"][0]
    ir.write_text(chunks / first["output"],
                  "I cannot help with that request.\n")

    progress = chunking.status(chunks)

    assert progress["translated"] == 0, progress
    assert first["id"] in progress["malformed"], progress
    assert progress["next"] == first["id"], progress


def test_a_partial_answer_is_neither_done_nor_ignored(tmp_path):
    """Some headers back, not all: real, and its own state."""
    _book_path, chunks, manifest = _two_chunks(tmp_path)
    first = manifest["chunks"][0]
    assert len(first["unit_ids"]) >= 2, "the fixture needs a multi-unit chunk"

    unit_id = first["unit_ids"][0]
    ir.write_text(chunks / first["output"],
                  f"@@ {unit_id} {first['unit_kinds'][unit_id]}\nترجمه.\n")

    progress = chunking.status(chunks)

    assert progress["translated"] == 0, progress
    assert first["id"] in progress["partial"], progress
    assert progress["next"] == first["id"], progress


def test_the_next_job_is_the_earliest_unfinished_one_in_manifest_order(tmp_path):
    """Answer the first, and the second is next — not whichever is missing."""
    _book_path, chunks, manifest = _two_chunks(tmp_path)
    _answer(chunks, manifest["chunks"][0])

    progress = chunking.status(chunks)

    assert progress["translated"] == 1, progress
    assert progress["next"] == manifest["chunks"][1]["id"], progress


def test_a_fully_answered_run_has_no_next_job(tmp_path):
    """The positive control, and the condition a driver loops until."""
    _book_path, chunks, manifest = _two_chunks(tmp_path)
    for entry in manifest["chunks"]:
        _answer(chunks, entry)

    progress = chunking.status(chunks)

    assert progress["translated"] == len(manifest["chunks"])
    assert progress["next"] is None
    assert progress["pending"] == []
    assert progress["malformed"] == []
    assert progress["partial"] == []


# --------------------------------------------------------------------------- #
# The budget
# --------------------------------------------------------------------------- #

def test_one_huge_paragraph_does_not_produce_a_worksheet_over_the_budget(tmp_path):
    """The budget exists to keep a payload inside a model's context.

    A single unit longer than the whole allowance was emitted whole: the chunker
    costs a block by ``len(text)`` and appends it regardless of the cap, and the
    reversible segmentation that exists for exactly this case was only ever
    wired into the page route. Measured before the fix: a 100,000-character
    paragraph under a 6,000 budget produced a 100,239-character worksheet.

    The prose is not shortened and the budget is not quietly raised: the unit is
    cut into segments that rejoin exactly.
    """
    budget = 6000
    book_path = _book(tmp_path, ["Sentence of perfectly ordinary prose. " * 2700])
    chunks = tmp_path / "chunks"

    manifest = chunking.build(book_path, chunks, glossary_path=None,
                              budget=budget)

    sizes = {entry["file"]: len((chunks / entry["file"])
                                .read_text(encoding="utf-8"))
             for entry in manifest["chunks"]}
    assert sizes, manifest
    assert max(sizes.values()) <= budget, sizes


def test_the_segments_of_a_cut_paragraph_rejoin_to_the_original(tmp_path):
    """Cutting is only safe because it is reversible. This is that property,
    end to end through the real builder rather than on `split_text` alone."""
    import merge as merging
    import segments

    original = "Sentence of perfectly ordinary prose. " * 2700
    book_path = _book(tmp_path, [original])
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=6000)

    answered: dict[str, str] = {}
    for entry in manifest["chunks"]:
        worksheet = (chunks / entry["file"]).read_text(encoding="utf-8")
        answered.update(merging.parse_worksheet(worksheet))

    rejoined = segments.rejoin(answered)
    assert list(rejoined) == ["b00001"], rejoined
    # `rejoin` normalises the whitespace at each cut to one space, which is the
    # documented round-trip weakening; the words and their order are exact.
    assert rejoined["b00001"].split() == original.split()


def test_a_paragraph_that_cannot_be_cut_small_enough_refuses_clearly(tmp_path):
    """An indivisible payload is an honest refusal, never a silent overflow.

    One word longer than the allowance cannot be cut without producing fragments
    that are words in no language, so there is nothing safe to do but say so.
    """
    book_path = _book(tmp_path, ["x" * 4000])
    chunks = tmp_path / "chunks"

    with pytest.raises(chunking.OverBudget) as refusal:
        chunking.build(book_path, chunks, glossary_path=None, budget=600)

    assert "b00001" in str(refusal.value)


def test_a_split_block_run_announces_a_first_mention_once(tmp_path):
    """The announcement belongs to one worksheet, not to a run of blocks.

    Splitting a block run into several worksheets gave every one of them the same
    ``block_ids``, so each was told it owned the name's first appearance. The
    translator then sees "introduce this name here" twice and does, which is the
    exact duplication the glossary pass exists to prevent — and it asked for it.

    Caught by the end-to-end script, which pytest does not collect, after the
    budget began splitting runs that used not to split.
    """
    import glossary as gl

    # Enough prose that one run becomes several worksheets at this budget. The
    # name sits mid-sentence on purpose: the scanner discards a capitalised run
    # that only ever opens a sentence, which is how ordinary words masquerade as
    # names — and a fixture that never clears that filter proves nothing.
    book_path = _book(tmp_path, [
        f"On that day Elizabeth Bennet walked on, paragraph {i} of prose here. " * 2
        for i in range(1, 13)])

    # Scanned, not hand-built: the scan is what records `first_block_id`, and
    # that field is the whole basis of the ownership decision under test.
    proposals = gl.scan(ir.load_book(book_path), minimum=2)
    for entry in proposals:
        if entry["source"] == "Elizabeth Bennet":
            entry.update({"target": "الیزابت بنت", "later_form": "الیزابت بنت",
                          "first_form": "الیزابت بنت (Elizabeth Bennet)",
                          "locked": True})
    owner = next(e for e in proposals if e["source"] == "Elizabeth Bennet")
    assert owner["first_block_id"], "the scan must record where the name first appears"

    glossary = gl.new_glossary()
    glossary["entries"] = proposals
    glossary_path = tmp_path / "glossary.json"
    gl.save(glossary, glossary_path)

    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=glossary_path,
                              budget=1300)

    assert len(manifest["chunks"]) >= 2, "the fixture must split, or it proves nothing"
    announced = [entry["id"] for entry in manifest["chunks"]
                 if "first mention" in
                 (chunks / entry["file"]).read_text(encoding="utf-8")]

    assert len(announced) == 1, (
        f"the first mention was announced in {len(announced)} worksheets: "
        f"{announced}")


def test_an_ordinary_book_is_not_segmented(tmp_path):
    """The negative control: nothing is cut that fits, so ordinary books keep
    producing whole units and the worksheet ids stay plain."""
    book_path = _book(tmp_path, [f"Paragraph {i} of ordinary prose. " * 5
                                 for i in range(1, 6)])
    chunks = tmp_path / "chunks"

    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=6000)

    every_unit = [u for entry in manifest["chunks"] for u in entry["unit_ids"]]
    assert every_unit, manifest
    assert not any("#" in unit_id for unit_id in every_unit), every_unit
