"""``status`` and ``merge`` answer the same question about the same reply.

Two commands read one reply and used to reach opposite conclusions, in a way
that cannot be recovered from by trying again:

* ``merge`` refused a reply whose request token was missing or belonged to the
  previous cut, while ``status`` counted the job **translated** and ``next``
  returned ``null``. Nothing was wrong with it that a re-translation could not
  fix, and nothing would ever ask for one.
* the mirror image: an image-only job merged clean — ``ok: true``,
  ``chunks_merged: 1`` — while ``status`` reported ``translated: 0`` of ``1``
  with ``next: null``, so a driver looping until everything is translated had
  nothing left to ask for and could never finish. Measured on a one-image book
  before :mod:`eligible` stopped asking a zero-unit job which cut its
  nonexistent reply answers.

So this file asks both sides about one directory and refuses to let them
disagree. Every repairable refusal must be *offered again with a reason*, and
every complete job must be complete on both sides without anybody inventing a
response for it.

The mutations are not a catalogue of syntax errors — :mod:`tests.test_one_verdict`
owns the grammar. They are one example of each way a reply can be repairable:
absent, blank, unparseable, half-answered, referring to a note it does not
carry, answering a different cut, saying which cut it answers at all, and
answering text the book no longer contains.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bookir as ir  # noqa: E402
import chunk as chunking  # noqa: E402
import merge as merging  # noqa: E402
import worksheet as ws  # noqa: E402
from tests_support import reply_text  # noqa: E402


def _book(tmp_path: Path, count: int = 3) -> Path:
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    for index in range(1, count + 1):
        book["blocks"].append(ir.make_block(
            "paragraph", index,
            text=f"Paragraph number {index} of ordinary prose here. " * 2))
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _run(tmp_path: Path) -> tuple[Path, Path, dict]:
    """One worksheet carrying three units, so ``next`` is unambiguous.

    A single chunk on purpose: with one job outstanding, ``next`` can only be
    that job, and an assertion about it cannot pass by accident because some
    other worksheet happened to be unfinished too.
    """
    book_path = _book(tmp_path)
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=4000)
    assert len(manifest["chunks"]) == 1, manifest
    entry = manifest["chunks"][0]
    assert len(entry["unit_ids"]) >= 3, entry
    return book_path, chunks, entry


def _body(entry: dict, unit_ids: list[str] | None = None,
          extra: str = "") -> str:
    """The payload of a complete reply, or of the units named."""
    return "\n".join(
        f"@@ {unit_id} {entry['unit_kinds'][unit_id]}\nترجمه.{extra}\n"
        for unit_id in (unit_ids if unit_ids is not None else entry["unit_ids"]))


def _answer(chunks: Path, entry: dict, body: str | None = None) -> None:
    """A reply bound to the worksheet, as a translator's is."""
    ir.write_text(chunks / entry["output"],
                  reply_text(chunks / entry["file"],
                             _body(entry) if body is None else body))


# --------------------------------------------------------------------------- #
# One mutation per repairable refusal
# --------------------------------------------------------------------------- #

def _missing(book_path: Path, chunks: Path, entry: dict) -> None:
    """No reply at all — the ordinary starting state."""
    (chunks / entry["output"]).unlink(missing_ok=True)


def _empty(book_path: Path, chunks: Path, entry: dict) -> None:
    """A touched file, an interrupted write, a model that answered nothing."""
    ir.write_text(chunks / entry["output"], "")


def _malformed(book_path: Path, chunks: Path, entry: dict) -> None:
    """Bound correctly and answering none of the units — a refusal, a crash log."""
    _answer(chunks, entry, "I cannot help with that request.\n")


def _partial(book_path: Path, chunks: Path, entry: dict) -> None:
    """Some headers back, not all."""
    _answer(chunks, entry, _body(entry, entry["unit_ids"][:1]))


def _invalid(book_path: Path, chunks: Path, entry: dict) -> None:
    """A marker whose body the reply does not carry.

    Merging it would leave the literal ``[[fn:tr-01]]`` in the finished prose,
    so the reply is refused before a footnote number is allocated.
    """
    _answer(chunks, entry, _body(entry, extra=" [[fn:tr-01]]"))


def _wrong_request(book_path: Path, chunks: Path, entry: dict) -> None:
    """An answer to the cut this worksheet replaced.

    The filename cannot tell the two apart: a rebuild writes ``chunk0001.md``
    again, with the same ids and the same count, and the old answer sits at
    exactly the path the new one expects.
    """
    ir.write_text(chunks / entry["output"],
                  f"{ws.request_line('req1:' + '0' * 16)}\n{_body(entry)}")


def _unbound(book_path: Path, chunks: Path, entry: dict) -> None:
    """A complete reply that says nothing about which cut it answers."""
    ir.write_text(chunks / entry["output"], _body(entry))


def _stale_source(book_path: Path, chunks: Path, entry: dict) -> None:
    """A correct answer to text the book no longer contains."""
    _answer(chunks, entry)
    book = ir.load_book(book_path)
    book["blocks"][0]["text"] += " A sentence added after the worksheet was cut."
    ir.save_book(book, book_path)


#: ``(mutation, the status list it must appear in)``. The key matters: it is what
#: tells an operator whether to re-translate, rebuild or re-extract, and a
#: refusal reported under the wrong one sends them to fix the wrong thing.
REFUSALS = [
    pytest.param(_missing, "pending", id="missing"),
    pytest.param(_empty, "empty", id="empty"),
    pytest.param(_malformed, "malformed", id="malformed"),
    pytest.param(_partial, "partial", id="partial"),
    pytest.param(_invalid, "invalid", id="unresolved-note"),
    pytest.param(_wrong_request, "wrong_request", id="wrong-request"),
    pytest.param(_unbound, "unbound", id="unbound"),
    pytest.param(_stale_source, "stale_source", id="stale-source"),
]


@pytest.mark.parametrize("mutate, listing", REFUSALS)
def test_a_reply_merge_refuses_is_offered_again_with_a_reason(
        tmp_path, mutate, listing):
    """The defect, stated as a property: refused by one side, offered by the other.

    Every one of these is repairable — by a new answer or by a rebuild — so a
    run that reports nothing outstanding while one sits on disk is a run that
    has quietly stopped.
    """
    book_path, chunks, entry = _run(tmp_path)
    mutate(book_path, chunks, entry)
    before = book_path.read_bytes()

    report = merging.merge(book_path, chunks, strict=True)
    progress = chunking.status(chunks)

    assert report["ok"] is False, report
    assert book_path.read_bytes() == before, "a refused merge wrote to the book"
    assert entry["id"] in progress[listing], (listing, progress)
    assert progress["next"] == entry["id"], progress
    assert progress["next_reason"], progress
    assert progress["translated"] == 0, progress


#: The same list without the one refusal a re-answer cannot clear: a moved source
#: needs the *rebuild* its reason names, and that has its own test below. Left in
#: as a runtime skip it would be a hidden gap in the suite's skip budget.
REANSWERABLE = [case for case in REFUSALS if case.values[0] is not _stale_source]


@pytest.mark.parametrize("mutate, listing", REANSWERABLE)
def test_a_refused_reply_is_repaired_by_answering_it_again(
        tmp_path, mutate, listing):
    """The other half of "repairable": the offer leads somewhere.

    A state reported as outstanding that a correct answer cannot clear is not a
    retry, it is a dead end — and the two are indistinguishable from the
    scheduler's side.
    """
    book_path, chunks, entry = _run(tmp_path)
    mutate(book_path, chunks, entry)
    _answer(chunks, entry)

    report = merging.merge(book_path, chunks, strict=True)
    progress = chunking.status(chunks)

    assert report["ok"] is True, report
    assert report["units_applied"] == len(entry["unit_ids"]), report
    assert progress["next"] is None, progress
    assert progress["translated"] == progress["total"], progress


def test_a_moved_source_is_cleared_by_rebuilding_not_by_answering_again(tmp_path):
    """``stale-source`` names the repair, and the repair is a rebuild.

    Re-answering the same worksheet cannot help: the worksheet itself quotes
    text the book no longer contains, so an answer to it is an answer to the old
    cut however carefully it is written.
    """
    book_path, chunks, entry = _run(tmp_path)
    _stale_source(book_path, chunks, entry)

    _answer(chunks, entry)
    assert merging.merge(book_path, chunks, strict=True)["ok"] is False
    assert chunking.status(chunks)["stale_source"] == [entry["id"]]

    rebuilt = chunking.build(book_path, chunks, glossary_path=None,
                             budget=4000, force=True)
    _answer(chunks, rebuilt["chunks"][0])

    report = merging.merge(book_path, chunks, strict=True)
    progress = chunking.status(chunks)

    assert report["ok"] is True, report
    assert progress["next"] is None, progress
    assert progress["stale_source"] == [], progress


# --------------------------------------------------------------------------- #
# The positive controls, which are what keep the refusals honest
# --------------------------------------------------------------------------- #

def test_a_valid_reply_is_complete_on_both_sides(tmp_path):
    """Without this, refusing everything would pass every test above."""
    book_path, chunks, entry = _run(tmp_path)
    _answer(chunks, entry)

    report = merging.merge(book_path, chunks, strict=True)
    progress = chunking.status(chunks)

    assert report["ok"] is True, report
    assert report["units_applied"] == len(entry["unit_ids"]), report
    assert report["stale"] == [] and report["unverified_freshness"] == [], report
    assert progress["translated"] == progress["total"] == 1, progress
    assert progress["next"] is None, progress
    assert progress["next_reason"] == "", progress


def test_a_job_with_nothing_to_translate_needs_no_reply_from_anybody(tmp_path):
    """An image with no alt text: finished the moment it was cut.

    Measured before this passed: ``merge`` reported ``ok: true`` with the job
    merged while ``status`` called it ``unverified`` — for having no spans to
    recompute a digest over — and counted ``translated: 0`` of ``1``. ``next``
    was ``null``, so there was nothing to offer and nothing to wait for, and a
    driver looping until the run is translated never stopped. Demanding a reply
    instead would be worse: it asks a model for prose the page does not contain.
    """
    book = ir.new_book(source_path="sample.epub", source_format="epub")
    book["blocks"].append(ir.make_block("image", 1, src="plate.png"))
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=2000)
    entry = manifest["chunks"][0]
    assert entry["unit_ids"] == [], manifest

    report = merging.merge(book_path, chunks, strict=True)
    progress = chunking.status(chunks)

    assert report["ok"] is True, report
    assert report["chunks_merged"] == 1, report
    assert report["missing_outputs"] == [], report
    assert not (chunks / entry["output"]).exists(), "nothing should be expected"
    assert progress["nothing_to_translate"] == [entry["id"]], progress
    assert progress["translated"] == progress["total"] == 1, progress
    assert progress["next"] is None, progress


def test_accepting_an_unbound_reply_is_an_explicit_operator_decision(tmp_path):
    """``--revalidate-unbound`` is the one place the two sides may differ.

    ``status`` has no such switch, so it keeps reporting the reply as unbound:
    the flag says "I know this answer predates the binding and I accept it on
    the strength of the source digest", which is a judgement an operator makes
    once, not a property of the directory. Pinned here so the asymmetry stays
    deliberate — silently accepting an unbound reply is the defect this whole
    file exists for.
    """
    book_path, chunks, entry = _run(tmp_path)
    _unbound(book_path, chunks, entry)

    refused = merging.merge(book_path, chunks, strict=True)
    assert refused["ok"] is False, refused
    assert chunking.status(chunks)["unbound"] == [entry["id"]]

    accepted = merging.merge(book_path, chunks, strict=True,
                             revalidate_unbound=True)

    assert accepted["ok"] is True, accepted
    assert accepted["revalidated"] == [entry["id"]], accepted
    assert accepted["units_applied"] == len(entry["unit_ids"]), accepted


# --------------------------------------------------------------------------- #
# The page scheduler, which had the same defect one level down
# --------------------------------------------------------------------------- #

def _split_page(tmp_path: Path) -> tuple[Path, Path, list[dict]]:
    """A page cut into two sub-jobs, both of them real prose.

    No renderer and no PDF: this is about which job the scheduler hands back,
    which is decided from the manifest and the replies on disk.
    """
    import pagerun  # noqa: PLC0415 — imported here so the chunk tests stay light

    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", index, page=1, text=text)
        for index, text in enumerate(
            ["The first half of the page. " * 12,
             "The second half of the page. " * 12], start=1)]
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)
    pages = tmp_path / "pages"
    manifest = pagerun.build(book_path, pages, budget=900)
    entries = pagerun.jobs_for(manifest, 1)
    assert len(entries) == 2, "the fixture did not actually split"
    return book_path, pages, entries


def test_the_page_scheduler_hands_back_the_part_merge_refused(tmp_path):
    """Not the first part, and not a part that is already correct.

    Measured before the fix: part 2 answered without a request line was refused
    as ``malformed: ['page0001-02']`` and the page recorded ``failed`` — with
    ``merge failed`` as the whole reason — while ``next_page`` offered
    ``page0001-01``. A driver following it re-translated a part that was already
    right, and the broken one was never named, so the page could not recover.
    """
    import pagerun  # noqa: PLC0415
    import runstate  # noqa: PLC0415

    book_path, pages, entries = _split_page(tmp_path)
    ir.write_text(pages / entries[0]["output"],
                  reply_text(pages / entries[0]["file"],
                             "@@ b00001 para\nمتن اول\n"))
    # Complete prose, correct ids and kinds — and nothing saying which cut it
    # answers, which is the refusal merge has made since the request binding.
    ir.write_text(pages / entries[1]["output"], "@@ b00002 para\nمتن دوم\n")

    refused = pagerun.merge_page(book_path, pages, 1)
    upcoming = pagerun.next_page(pages)
    record = runstate.RunState(tmp_path).page(1)

    assert refused["ok"] is False, refused
    assert entries[1]["id"] in refused["malformed"], refused
    assert upcoming["id"] == entries[1]["id"], upcoming
    assert upcoming["reason"], upcoming
    # The reason survives into the run state too: `failed` with no cause is a
    # dead end for whoever reads the record next.
    assert record["state"] == "failed", record
    assert entries[1]["id"] in record["last_error"], record
    assert record["last_error"] != "merge failed", record


def test_repairing_that_part_lets_the_page_merge_and_move_on(tmp_path):
    """The positive control: the offer leads somewhere, on the page route too."""
    import pagerun  # noqa: PLC0415

    book_path, pages, entries = _split_page(tmp_path)
    for entry, body in zip(entries, ("@@ b00001 para\nمتن اول\n",
                                     "@@ b00002 para\nمتن دوم\n")):
        ir.write_text(pages / entry["output"],
                      reply_text(pages / entry["file"], body))

    report = pagerun.merge_page(book_path, pages, 1)
    upcoming = pagerun.next_page(pages)

    assert report["ok"] is True, report
    assert upcoming["reason"] == "", upcoming
    # Still offered, because merged is three states short of accepted — but for
    # the lifecycle's reason, not because a reply is wrong.
    assert upcoming["state"] == "merged", upcoming
