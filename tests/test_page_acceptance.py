"""Two guarantees a finished page owes: it is current, and it is finite.

``accept`` is the last gate before a page is done with, and the retry cap is the
only thing that stops a page being re-rendered forever. Both were decided from
*labels* — the state a record happened to be in, and a report written at some
earlier time — rather than from the content they are supposed to be about.

The helpers come from `test_pagerun`, which owns the page-run fixtures; `tests/`
is on `sys.path`, so importing them is cheaper than a second copy that can drift
away from the lifecycle it is meant to model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import bookir as ir
import pagerun
import renderqa
import runstate
from test_pagerun import TARGET, _one_page_book, _rendered, _reviewed

pytest.importorskip("pymupdf")


def _translated_page(tmp_path: Path, text: str = TARGET) -> tuple[Path, Path]:
    """A page built, answered and merged — the state QA is run against."""
    book_path = _one_page_book(tmp_path)
    pages = tmp_path / "pages"
    pagerun.build(book_path, pages)
    upcoming = pagerun.next_page(pages)
    ir.write_text(Path(upcoming["output"]), f"@@ b00001 para\n{text}\n")
    merged = pagerun.merge_page(book_path, pages, 1)
    assert merged["ok"], merged
    return book_path, pages


def _qa_passed(tmp_path: Path, book_path: Path, text: str = TARGET) -> dict:
    written = renderqa.check(tmp_path, book_path, 1,
                             target_pdf=_rendered(tmp_path / "target.pdf", text))
    assert written["ok"] is True, written
    return written


# --------------------------------------------------------------------------- #
# S03 — an acceptance that outlives the content it was about
# --------------------------------------------------------------------------- #

def test_editing_the_translation_after_qa_passed_blocks_acceptance(tmp_path):
    """Every gate `accept` consults is about an earlier moment.

    ``untranslated`` only asks whether there is *some* Persian; the record says
    ``qa_passed``; the report on disk still says it passed; and the review is
    bound to render files that nobody re-made. So a target edited after QA
    sails through all four and the page is accepted having never been checked in
    the form it will actually print.
    """
    book_path, pages = _translated_page(tmp_path)
    _qa_passed(tmp_path, book_path)
    _reviewed(tmp_path)

    # The window that matters is between QA passing and accepting: somebody
    # improves the Persian by hand, or a second typography pass runs, and
    # nothing re-renders. Every gate `accept` consults still describes the
    # version that was checked.
    book = ir.load_book(book_path)
    book["blocks"][0]["target"] = "یک سطر تازه که هیچ‌کس ندیده است."
    ir.save_book(book, book_path)

    refused = pagerun.accept(book_path, pages, 1)

    assert refused["ok"] is False, refused
    assert refused["refused"] == "translation-changed", refused
    assert "render-qa" in refused["detail"]


def test_an_unchanged_translation_still_accepts(tmp_path):
    """The positive control. A check that refuses everything is not a check."""
    book_path, pages = _translated_page(tmp_path)
    _qa_passed(tmp_path, book_path)
    _reviewed(tmp_path)

    assert pagerun.accept(book_path, pages, 1)["ok"] is True


def test_a_page_re_rendered_after_the_edit_accepts_again(tmp_path):
    """The recovery path, which is the other half of refusing.

    A refusal that cannot be cleared by doing the right thing is a trap, so the
    test says what the right thing is: render QA again, look again, accept.
    """
    book_path, pages = _translated_page(tmp_path)
    _qa_passed(tmp_path, book_path)
    _reviewed(tmp_path)

    improved = "یک سطر تازه که هیچ‌کس ندیده است."
    book = ir.load_book(book_path)
    book["blocks"][0]["target"] = improved
    ir.save_book(book, book_path)
    assert pagerun.accept(book_path, pages, 1)["ok"] is False

    _qa_passed(tmp_path, book_path, text=improved)
    _reviewed(tmp_path)
    assert pagerun.accept(book_path, pages, 1)["ok"] is True


# --------------------------------------------------------------------------- #
# S04 — a retry cap that counts
# --------------------------------------------------------------------------- #

def _fails_qa(tmp_path: Path, book_path: Path, *, max_attempts: int) -> dict:
    """Render QA against a page that does not carry the translation."""
    return renderqa.check(
        tmp_path, book_path, 1,
        target_pdf=_rendered(tmp_path / "wrong.pdf", "Not the translation."),
        max_attempts=max_attempts)


def test_re_merging_the_same_answers_does_not_reset_the_retry_cap(tmp_path):
    """The cap tested the *label*, so moving the label cleared the cap.

    ``merge_page`` sets the page to ``translated`` and then ``merged``, which are
    not ``failed``, and the attempt counter was left alone — so re-running merge
    with the identical reply bought another render every time, forever, with
    nothing having changed about the page.
    """
    book_path, pages = _translated_page(tmp_path)

    failed = _fails_qa(tmp_path, book_path, max_attempts=1)
    assert failed["ok"] is False
    record = runstate.RunState(tmp_path).page(1)
    assert record["state"] == "failed" and record["attempts"] == 1

    exhausted = _fails_qa(tmp_path, book_path, max_attempts=1)
    assert exhausted["refused"] == "retry-exhausted", exhausted

    # Merge the very same worksheet again. Nothing about the page has changed.
    assert pagerun.merge_page(book_path, pages, 1)["ok"]
    assert runstate.RunState(tmp_path).page(1)["state"] == "merged"

    still_exhausted = _fails_qa(tmp_path, book_path, max_attempts=1)

    assert still_exhausted["refused"] == "retry-exhausted", still_exhausted
    assert runstate.RunState(tmp_path).page(1)["attempts"] == 1, (
        "the attempt counter moved without a new attempt")


def test_a_corrected_reply_earns_another_attempt(tmp_path):
    """The cap is about unchanged inputs, not about punishment.

    A translator who fixes the reply has given the page something new to fail
    on, so the count starts again — keyed on the answers, which is what actually
    changed, rather than on anyone having relabelled a record.
    """
    book_path, pages = _translated_page(tmp_path)
    assert _fails_qa(tmp_path, book_path, max_attempts=1)["ok"] is False
    assert _fails_qa(tmp_path, book_path,
                     max_attempts=1)["refused"] == "retry-exhausted"

    # Latin, like `TARGET`, and for the same reason: PyMuPDF does not read
    # Arabic script back faithfully, so a Persian reply here would *pass* the
    # text check against the wrong render and the retry would prove nothing.
    upcoming = pagerun.jobs_for(pagerun.load_manifest(pages), 1)[0]
    ir.write_text(pages / upcoming["output"],
                  "@@ b00001 para\nA corrected line, and a different one.\n")
    assert pagerun.merge_page(book_path, pages, 1)["ok"]

    assert runstate.RunState(tmp_path).page(1)["attempts"] == 0, (
        "a corrected reply did not reset the count")

    retried = _fails_qa(tmp_path, book_path, max_attempts=1)
    assert retried.get("refused") != "retry-exhausted", retried
    assert retried["ok"] is False, "the fixture must still fail QA"
    assert runstate.RunState(tmp_path).page(1)["attempts"] == 1, (
        "the retry was allowed, so it has to count toward the cap")


def test_a_page_nobody_can_lay_out_spends_no_attempt(tmp_path, monkeypatch):
    """The cap counts the page's failures, and this is not one of them.

    Investigated and left alone on purpose. A document that cannot be laid out
    here is a statement about the machine — no Word, no LibreOffice — not about
    this page, so spending the page's retry budget on it would exhaust the page
    for an environment problem *and still be exhausted* once the renderer was
    installed, because nothing about the page moved. Re-running costs nothing: it
    returns immediately with the install instructions in `unverified`.

    `test_renderqa.py` asserts the same invariant from the other side, and from
    the start. This test exists so the next person to harden the retry cap finds
    the reason here rather than rediscovering it by breaking two tests.
    """
    import preview

    book_path, _pages = _translated_page(tmp_path)
    monkeypatch.setattr(
        preview, "build",
        lambda *a, **k: {"ok": False, "detail": "no renderer on this machine"})

    first = renderqa.check(tmp_path, book_path, 1, max_attempts=1)
    assert first["ok"] is False
    assert first["verified"] is False
    assert "no renderer" in first["unverified"]

    # The page is where merging left it, with its attempts untouched.
    record = runstate.RunState(tmp_path).page(1)
    assert record["state"] == "merged", record
    assert record["attempts"] == 0, record

    # And it is still reachable rather than capped out.
    again = renderqa.check(tmp_path, book_path, 1, max_attempts=1)
    assert again.get("refused") != "retry-exhausted", again
