"""Recorded page QA, evidence identity and explicit-target behavior."""

from __future__ import annotations

import json
import pytest
import pagepdf
import renderqa
import runstate
import wordrender
from tests_support import png_bytes
from render_fixtures import _latin_book, _pdf_page, _two_sheet_pdf

pytest_plugins = ["render_fixtures"]

def test_a_page_that_renders_correctly_is_recorded_as_passed(tmp_path):
    pytest.importorskip("pymupdf")
    target = "Sample rendered line that must appear on the page."
    book_path = _latin_book(tmp_path, target)
    source = _pdf_page(tmp_path / "source.pdf", [("A line of source prose.", 54, 100)])
    rendered = _pdf_page(tmp_path / "target.pdf", [(target, 60, 100)])

    written = renderqa.check(tmp_path, book_path, 1,
                             target_pdf=rendered, source_pdf=source)

    assert written["ok"] is True and written["verified"] is True
    assert (tmp_path / "renders" / "source" / "page-0001.png").exists()
    assert (tmp_path / "renders" / "target" / "page-0001.png").exists()
    report = json.loads(renderqa.report_path(tmp_path, 1).read_text(encoding="utf-8"))
    assert report["schema"] == renderqa.SCHEMA and report["page"] == 1

    record = runstate.RunState(tmp_path).page(1)
    assert record["state"] == "qa_passed"
    assert record["attempts"] == 0
    assert set(record["hashes"]) >= {"translation", "render", "qa"}


def test_a_page_that_lost_its_text_is_recorded_as_failed(tmp_path):
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "Sample rendered line that vanished entirely.")
    rendered = _pdf_page(tmp_path / "target.pdf", [("Something else.", 60, 100)])

    written = renderqa.check(tmp_path, book_path, 1, target_pdf=rendered)
    assert written["ok"] is False and written["verified"] is True
    assert "text-missing" in {f["code"] for f in written["findings"]}

    record = runstate.RunState(tmp_path).page(1)
    assert record["state"] == "failed" and record["attempts"] == 1
    assert record["last_error"].startswith("text-missing")


def test_a_page_nothing_can_lay_out_is_unverified_not_passed(monkeypatch,
                                                             tmp_path):
    """Nobody has to hand a preview over any more - so the way to have nothing
    to look at is for the machine to be unable to lay one out."""
    book_path = _latin_book(tmp_path, "Anything at all.")
    monkeypatch.setattr(wordrender, "word_available", lambda: False)
    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: None)

    written = renderqa.check(tmp_path, book_path, 1)

    assert written["ok"] is False
    assert written["verified"] is False
    assert "LibreOffice" in written["unverified"]
    # An absent renderer is not a failed page: no attempt was spent on it.
    assert runstate.RunState(tmp_path).page(1) is None


def test_an_image_on_its_own_carries_no_geometry_to_check(tmp_path, sample_png):
    """A picture of a page shows a reviewer the page and tells a check nothing."""
    book_path = _latin_book(tmp_path, "Anything at all.")
    written = renderqa.check(tmp_path, book_path, 1, target_image=sample_png)

    assert written["ok"] is False and written["verified"] is False
    assert "no --target-pdf" in written["unverified"]


def test_a_target_that_is_not_there_is_unverified(tmp_path):
    book_path = _latin_book(tmp_path, "Anything at all.")
    written = renderqa.check(tmp_path, book_path, 1,
                             target_pdf=tmp_path / "never-built.pdf")
    assert written["verified"] is False and "not there" in written["unverified"]


def test_a_supplied_image_is_filed_as_the_evidence(tmp_path, sample_png):
    """The caller may render the page however it likes; this never runs Word."""
    pytest.importorskip("pymupdf")
    target = "Sample rendered line that must appear on the page."
    book_path = _latin_book(tmp_path, target)
    rendered = _pdf_page(tmp_path / "target.pdf", [(target, 60, 100)])

    written = renderqa.check(tmp_path, book_path, 1, target_pdf=rendered,
                             target_image=sample_png)
    assert written["ok"] is True
    filed = tmp_path / "renders" / "target" / "page-0001.png"
    assert filed.read_bytes() == sample_png.read_bytes()


def test_retrying_a_page_for_ever_is_impossible(tmp_path):
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "Sample rendered line that vanished entirely.")
    rendered = _pdf_page(tmp_path / "target.pdf", [("Something else.", 60, 100)])

    for attempt in (1, 2, 3):
        written = renderqa.check(tmp_path, book_path, 1, target_pdf=rendered,
                                 max_attempts=3)
        assert written["ok"] is False
        assert runstate.RunState(tmp_path).page(1)["attempts"] == attempt

    refused = renderqa.check(tmp_path, book_path, 1, target_pdf=rendered,
                             max_attempts=3)
    assert refused["refused"] == "retry-exhausted"
    assert "limit 3" in refused["detail"]
    # The wall holds: the count did not move, and nothing was re-rendered.
    assert runstate.RunState(tmp_path).page(1)["attempts"] == 3


def test_a_corrected_source_page_lets_the_page_be_tried_again(tmp_path):
    """The cap bounds retries of the same failure, not the page for ever."""
    state = runstate.RunState(tmp_path)
    state.note_page_source(1, "before")
    for _ in range(3):
        state.set_page(1, "failed", error="still blank")
    assert state.page(1)["attempts"] == 3

    assert runstate.RunState(tmp_path).note_page_source(1, "after") is True
    record = runstate.RunState(tmp_path).page(1)
    assert record["state"] == "pending" and record["attempts"] == 0


def test_the_cli_reports_the_page_and_exits_on_the_outcome(tmp_path, capsys):
    pytest.importorskip("pymupdf")
    target = "Sample rendered line that must appear on the page."
    book_path = _latin_book(tmp_path, target)
    good = _pdf_page(tmp_path / "good.pdf", [(target, 60, 100)])
    bad = _pdf_page(tmp_path / "bad.pdf", [("Nothing like it.", 60, 100)])

    arguments = ["--book", str(book_path), "--work", str(tmp_path), "--page", "1"]
    assert renderqa.main(arguments + ["--target-pdf", str(good)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert renderqa.main(arguments + ["--target-pdf", str(bad)]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False

    assert renderqa.main(arguments + ["--target-pdf", str(bad),
                                      "--max-attempts", "1"]) == 2
    assert json.loads(capsys.readouterr().out)["refused"] == "retry-exhausted"


def test_a_conversion_failure_leaves_the_page_unverified_not_passed(
        monkeypatch, tmp_path, sample_book_and_docx):
    """"We could not look" must never be recorded as "we looked and it was fine".

    This is the failure mode worth a test of its own: a converter that is not
    installed is the *normal* state on a fresh machine, and a check that quietly
    passes there would be worse than no check.
    """
    book_path, docx_path = sample_book_and_docx
    monkeypatch.setattr(wordrender, "word_available", lambda: False)
    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: None)

    report = renderqa.check(tmp_path, book_path, 1, docx=docx_path)
    assert report["ok"] is False, report
    assert "LibreOffice" in report["unverified"]
    assert report.get("findings") in (None, []), (
        "an unverified page invented findings it could not have seen"
    )


def test_an_explicit_target_wins_over_conversion(monkeypatch, tmp_path,
                                                 sample_book_and_docx):
    """A caller who rendered the page themselves is not second-guessed."""
    book_path, docx_path = sample_book_and_docx
    called = []
    monkeypatch.setattr(renderqa, "render_docx",
                        lambda *a, **k: called.append(1))

    supplied = tmp_path / "supplied.png"
    supplied.write_bytes(png_bytes(40, 30))

    renderqa.check(tmp_path, book_path, 1, docx=docx_path, target_image=supplied)
    assert not called, "the converter ran even though a target was supplied"


def test_a_target_image_that_is_not_there_is_reported_not_raised(
        tmp_path, sample_book_and_docx):
    """A wrong path is a mistake to report, not a traceback to hand back."""
    book_path, _ = sample_book_and_docx
    report = renderqa.check(tmp_path, book_path, 1,
                            target_image=tmp_path / "missing.png")
    assert report["ok"] is False
    assert "missing.png" in report["unverified"]


def test_a_page_that_was_laid_out_says_so_even_when_judging_it_fails(tmp_path,
                                                                     monkeypatch):
    """Laying the document out is the slow part; losing that fact wastes it.

    If the run dies between rendering the page and judging it, the record must
    not read `merged` — that says the page was never laid out, and the next run
    pays for the render again. `rendered` was in the state list from the start
    with nothing writing it, which is the same bug wearing a name.
    """
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "A line that was rendered before the crash.")
    rendered = _pdf_page(tmp_path / "target.pdf", [("Anything.", 60, 100)])

    def damaged(*args, **kwargs):
        raise RuntimeError("the PDF ended in the middle of an object")

    # `_view_of_open` is where reading a page back happens: `views_of` opens the
    # document once and measures every page through it, rather than calling
    # `page_view` per page. Same seam, same assertions — only the name moved, and
    # it has now moved twice: reading a PDF back lives in `pagepdf`, and
    # `views_of` resolves this function as *its own* module global. Patching the
    # re-export on `pagecheck` therefore replaces a name nothing calls — which is
    # worse than the AttributeError that caught it, because the test would pass
    # while exercising the undamaged path.
    monkeypatch.setattr(pagepdf, "_view_of_open", damaged)
    written = renderqa.check(tmp_path, book_path, 1, target_pdf=rendered)

    assert written["verified"] is False, "a page nobody could read is not a pass"
    record = runstate.RunState(tmp_path).page(1)
    assert record["state"] == "rendered"
    assert record["hashes"]["render"], "the render it kept is not identified"


def test_the_review_identity_covers_every_target_sheet(tmp_path):
    """A page that reflows onto two sheets has two sheets of evidence.

    Binding to the first PNG alone left a hole exactly the width of the
    overflow: sheet two could be re-rendered from different text and the
    reviewer's `yes` would still stand, because nothing it was bound to moved.
    """
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "A line that fits on the first sheet.")
    two = _two_sheet_pdf(tmp_path / "two.pdf",
                         "A line that fits on the first sheet.", "The overflow.")

    renderqa.check(tmp_path, book_path, 1, target_pdf=two)
    before = runstate.RunState(tmp_path).page(1)["hashes"]["render"]
    assert len(json.loads(renderqa.report_path(tmp_path, 1).read_text(
        encoding="utf-8"))["renders"]["target_sheets"]) == 2

    # Change ONLY the second sheet. The first is byte-identical.
    changed = _two_sheet_pdf(tmp_path / "changed.pdf",
                             "A line that fits on the first sheet.",
                             "A completely different overflow.")
    renderqa.check(tmp_path, book_path, 1, target_pdf=changed)
    after = runstate.RunState(tmp_path).page(1)["hashes"]["render"]

    assert before != after, (
        "the second sheet changed and the review identity did not — a review "
        "made before this would still read as current"
    )


def test_the_review_identity_covers_the_source_render(tmp_path):
    """The reviewer compares two images. Changing one of them is a change."""
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "The translated line.")
    target = _pdf_page(tmp_path / "target.pdf", [("The translated line.", 60, 100)])
    first = _pdf_page(tmp_path / "s1.pdf", [("An English source line.", 54, 100)])
    second = _pdf_page(tmp_path / "s2.pdf", [("A different source line.", 54, 100)])

    renderqa.check(tmp_path, book_path, 1, target_pdf=target, source_pdf=first)
    before = runstate.RunState(tmp_path).page(1)["hashes"]["render"]
    renderqa.check(tmp_path, book_path, 1, target_pdf=target, source_pdf=second)
    after = runstate.RunState(tmp_path).page(1)["hashes"]["render"]

    assert before != after, "the source page changed under the reviewer's eyes"


def test_identical_evidence_keeps_a_review_current(tmp_path):
    """The mirror: re-running QA over an unchanged page must not invalidate it.

    Without this, the digest could be `random()` and the two tests above would
    still pass — and every review would go stale on every re-run.
    """
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "The translated line.")
    target = _pdf_page(tmp_path / "target.pdf", [("The translated line.", 60, 100)])
    source = _pdf_page(tmp_path / "source.pdf", [("An English source line.", 54, 100)])

    renderqa.check(tmp_path, book_path, 1, target_pdf=target, source_pdf=source)
    before = runstate.RunState(tmp_path).page(1)["hashes"]["render"]
    renderqa.check(tmp_path, book_path, 1, target_pdf=target, source_pdf=source)

    assert runstate.RunState(tmp_path).page(1)["hashes"]["render"] == before


def test_a_one_sheet_page_still_has_an_identity(tmp_path):
    """The ordinary case, unchanged: one sheet, one stable digest."""
    pytest.importorskip("pymupdf")
    book_path = _latin_book(tmp_path, "The translated line.")
    target = _pdf_page(tmp_path / "target.pdf", [("The translated line.", 60, 100)])

    written = renderqa.check(tmp_path, book_path, 1, target_pdf=target)
    assert written["renders"]["target_sheets"] == [written["renders"]["target"]]
    assert runstate.RunState(tmp_path).page(1)["hashes"]["render"]
