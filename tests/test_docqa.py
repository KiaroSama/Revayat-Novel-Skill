"""The finished book, checked as the artefact the reader receives.

Accepting pages one at a time is necessary and not sufficient, and the gap
between the two is exactly what this file is about. Every page of a document can
be individually well-formed — nothing off the trim, no hole, no text on a plate
— while a paragraph the IR holds is simply not in it. No per-page check can see
that, because no page is missing anything: the missing paragraph belongs to a
page that no longer exists once Persian has reflowed.

So the load-bearing test here is a mutation: take a document that passes, delete
one visible paragraph from it, leave the geometry perfect, and require the check
to fail. A completeness check that has only ever been seen to pass has not been
shown to notice anything.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

import pytest

import bookir as ir
import docqa
import pagecheck
import review
import wordrender
from build_docx import Builder, add_arguments

PERSIAN = [
    "صبح به آرامی از فراز تپه‌ها بالا آمد و الیزابت کنار پنجره ایستاده بود.",
    "دارسی هیچ نگفت و او رویش را از پنجره برگرداند و به راه افتاد.",
    "جاده تا پایین دره ادامه داشت و هیچ‌کس در آن ساعت از آن نمی‌گذشت.",
]


def _book(tmp_path: Path, targets=None) -> Path:
    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    targets = list(targets or PERSIAN)
    blocks = []
    for index, target in enumerate(targets, start=1):
        block = ir.make_block("paragraph", index, page=1, bbox=[72, 90, 320, 140],
                              text=f"An English paragraph number {index} here.")
        block["target"] = target
        blocks.append(block)
    book["blocks"] = blocks
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _built(tmp_path: Path, book_path: Path) -> Path:
    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma",
                                 "--no-toc"])
    docx = tmp_path / "book.fa.docx"
    Builder(ir.load_book(book_path), tmp_path, options).build(docx)
    return docx


def _reviewed(work_dir: Path) -> dict:
    """File a clean reviewer verdict against whatever was last rendered."""
    filed = review.record(work_dir, review.DOCUMENT,
                          dict.fromkeys(review.QUESTIONS, True),
                          render=docqa.document_hash(work_dir))
    assert filed["ok"], filed
    return filed


def _laid_out(tmp_path: Path, book_path: Path, docx: Path) -> dict:
    """Render, review, check — the documented order, in one call.

    The first pass is *expected* to come back unverified: nobody has looked yet,
    and that is the gate working. Only a machine that cannot lay a document out
    at all is a reason to skip, and it says so in the message — an earlier
    version of this helper skipped on any unverified result, which quietly
    turned five tests including the mutation into no tests at all.
    """
    first = docqa.check_document(tmp_path, book_path, docx)
    reason = str(first.get("unverified", ""))
    # Only a machine that cannot produce pages is a reason to skip. "Nobody has
    # looked" and "reviewed, then rendered again" both mean *a review is
    # needed*, which is the next line — an earlier version treated either as a
    # broken machine and quietly turned the mutation tests into no tests at all.
    if any(tool in reason for tool in ("LibreOffice", "Word", "PyMuPDF")):
        pytest.skip(f"nothing on this machine can lay a document out: {reason}")
    _reviewed(tmp_path)
    return docqa.check_document(tmp_path, book_path, docx)


# --------------------------------------------------------------------------- #
# The document that is right
# --------------------------------------------------------------------------- #

def test_a_correctly_assembled_document_passes(tmp_path):
    pytest.importorskip("pymupdf")
    book_path = _book(tmp_path)
    report = _laid_out(tmp_path, book_path, _built(tmp_path, book_path))

    assert report["ok"] is True and report["verified"] is True
    assert report["findings"] == []
    assert report["counts"]["expected_blocks"] == len(PERSIAN)
    assert docqa.report_path(tmp_path).exists()


def test_every_final_page_is_kept_as_an_image(tmp_path):
    """The last gate is a person looking at these. Pages nobody kept cannot be
    looked at, re-read, or checked again against a later render."""
    pytest.importorskip("pymupdf")
    book_path = _book(tmp_path)
    report = _laid_out(tmp_path, book_path, _built(tmp_path, book_path))

    assert len(report["renders"]) == report["pages"] >= 1
    for name in report["renders"]:
        assert (tmp_path / name).exists(), f"{name} was reported but not written"


# --------------------------------------------------------------------------- #
# The mutation: geometry perfect, one paragraph gone
# --------------------------------------------------------------------------- #

def _without_one_paragraph(docx: Path, phrase: str, destination: Path) -> Path:
    """Delete the `w:p` carrying `phrase`, and nothing else.

    Editing the package rather than the book on purpose: the IR must go on
    saying the paragraph belongs in the document. That is the whole shape of the
    defect — assembly dropped something the book still expects, and every page
    that remains is perfectly well-formed.
    """
    import re

    with zipfile.ZipFile(docx) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}

    body = parts["word/document.xml"].decode("utf-8")
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", body, re.S)
    doomed = [p for p in paragraphs if phrase[:20] in p]
    assert len(doomed) == 1, (
        f"the fixture is not what this test assumes: {len(doomed)} paragraphs "
        f"carry {phrase[:20]!r}"
    )
    parts["word/document.xml"] = body.replace(doomed[0], "").encode("utf-8")

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as out:
        for name, blob in parts.items():
            out.writestr(name, blob)
    return destination


def test_a_paragraph_deleted_from_an_otherwise_perfect_document_is_caught(tmp_path):
    """The claim this whole module exists for, proved by breaking it.

    The document that comes out of this still paginates correctly, still sets
    every remaining paragraph inside the body, still has no hole and no overlap.
    Only a check that asks the *book* what should be there can tell.
    """
    pytest.importorskip("pymupdf")
    book_path = _book(tmp_path)
    whole = _built(tmp_path, book_path)
    _laid_out(tmp_path, book_path, whole)

    damaged = _without_one_paragraph(whole, PERSIAN[1], tmp_path / "damaged.docx")
    # Reviewed too: a review is bound to the document it describes, and the
    # point of this test is that the *completeness* gate refuses, not that a
    # stale review does.
    after = _laid_out(tmp_path, book_path, damaged)

    assert after["verified"] is True, "the damaged document still renders"
    assert after["ok"] is False, "a book missing a paragraph passed"
    codes = {f["code"] for f in after["findings"]}
    assert "text-missing" in codes
    assert any(f.get("unit") == "document" for f in after["findings"]), (
        "the finding is about the book, not about one of its pages"
    )
    assert all(p["ok"] for p in after["per_page"]), (
        "the point of this test is that every individual page is still fine"
    )


def test_a_paragraph_written_twice_is_caught(tmp_path):
    """The mirror of the one above: assembly that repeats rather than drops."""
    pytest.importorskip("pymupdf")
    book_path = _book(tmp_path)
    whole = _built(tmp_path, book_path)
    _laid_out(tmp_path, book_path, whole)

    import re
    with zipfile.ZipFile(whole) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    body = parts["word/document.xml"].decode("utf-8")
    doomed = [p for p in re.findall(r"<w:p[ >].*?</w:p>", body, re.S)
              if PERSIAN[1][:20] in p]
    parts["word/document.xml"] = body.replace(
        doomed[0], doomed[0] + doomed[0]).encode("utf-8")
    doubled = tmp_path / "doubled.docx"
    with zipfile.ZipFile(doubled, "w", zipfile.ZIP_DEFLATED) as out:
        for name, blob in parts.items():
            out.writestr(name, blob)

    after = _laid_out(tmp_path, book_path, doubled)
    assert after["verified"] is True and after["ok"] is False
    assert "text-duplicated" in {f["code"] for f in after["findings"]}


# --------------------------------------------------------------------------- #
# The gates that must not pass by default
# --------------------------------------------------------------------------- #

def test_a_document_nobody_looked_at_is_unverified_not_passed(tmp_path):
    """Measuring is not looking. A book can measure perfectly and read badly."""
    pytest.importorskip("pymupdf")
    book_path = _book(tmp_path)
    report = docqa.check_document(tmp_path, book_path, _built(tmp_path, book_path))
    if "PyMuPDF" in str(report.get("unverified", "")):
        pytest.skip("PyMuPDF cannot read a page back here")
    if "LibreOffice" in str(report.get("unverified", "")):
        pytest.skip("nothing on this machine lays a document out")

    assert report["ok"] is False
    assert report["verified"] is False
    assert "nobody has looked at the document" in report["unverified"]
    assert report["findings"] == [], (
        "the measurements were fine; it is the looking that is missing"
    )


def test_a_final_review_does_not_survive_the_book_being_rebuilt(tmp_path):
    """Otherwise the review describes a document that no longer exists."""
    pytest.importorskip("pymupdf")
    book_path = _book(tmp_path)
    docx = _built(tmp_path, book_path)
    passed = _laid_out(tmp_path, book_path, docx)
    if not passed.get("verified"):
        pytest.skip("nothing on this machine lays a document out")
    assert passed["ok"] is True

    # A different book, so a different document — and the review describes the
    # one that no longer exists.
    other = _book(tmp_path, targets=PERSIAN + ["و باران گرفت."])
    after = docqa.check_document(tmp_path, other, _built(tmp_path, other))

    assert after["verified"] is False
    assert "rendered again" in after["unverified"]


def test_a_document_that_cannot_be_laid_out_is_unverified_not_passed(
        monkeypatch, tmp_path):
    book_path = _book(tmp_path)
    docx = _built(tmp_path, book_path)
    monkeypatch.setattr(wordrender, "word_available", lambda: False)
    monkeypatch.setattr(wordrender, "find_libreoffice", lambda: None)

    report = docqa.check_document(tmp_path, book_path, docx)
    assert report["ok"] is False and report["verified"] is False
    assert "LibreOffice" in report["unverified"]
    assert docqa.report_path(tmp_path).exists(), (
        "the report must be written even when nothing could be looked at"
    )


def test_direction_is_read_from_the_document_not_the_render(tmp_path):
    """PyMuPDF's block boxes do not report RTL alignment faithfully.

    Measured: a document whose every paragraph carries `w:bidi` came back from
    the render looking flush-left on every line. The file says what Word will
    actually do, so the file is what gets asked.
    """
    book_path = _book(tmp_path)
    assert pagecheck.check_direction_in_document(_built(tmp_path, book_path)) == []


def test_a_document_set_left_to_right_is_reported(tmp_path):
    """The check has to be able to fire, or it says nothing when it passes."""
    book_path = _book(tmp_path)
    docx = _built(tmp_path, book_path)

    with zipfile.ZipFile(docx) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    for name in ("word/document.xml", "word/styles.xml"):
        parts[name] = parts[name].decode("utf-8").replace(
            "<w:bidi/>", "").replace('<w:bidi w:val="1"/>', "").encode("utf-8")
    stripped = tmp_path / "ltr.docx"
    with zipfile.ZipFile(stripped, "w", zipfile.ZIP_DEFLATED) as out:
        for name, blob in parts.items():
            out.writestr(name, blob)

    findings = pagecheck.check_direction_in_document(stripped)
    assert [f["code"] for f in findings] == ["document-not-rtl"]


# --------------------------------------------------------------------------- #
# What the whole render is asked, independent of pagination
# --------------------------------------------------------------------------- #

def test_expectations_are_flat_and_carry_no_page_numbers(tmp_path):
    """The one question whose answer must not depend on where anything landed."""
    book = ir.load_book(_book(tmp_path))
    expected = docqa.document_expectations(book)

    assert expected["texts"] == PERSIAN
    assert expected["translatable"] == len(PERSIAN)
    assert expected["page"] == 0, "a document-wide expectation has no page"


def test_the_cli_checks_and_reviews(tmp_path, capsys):
    pytest.importorskip("pymupdf")
    book_path = _book(tmp_path)
    docx = _built(tmp_path, book_path)
    arguments = ["check", "--book", str(book_path), "--work", str(tmp_path),
                 "--docx", str(docx)]

    if docqa.main(arguments) != 0:
        written = json.loads(capsys.readouterr().out)
        if written.get("unverified") and "nobody has looked" not in written["unverified"]:
            pytest.skip(f"cannot lay out here: {written['unverified']}")
        assert "nobody has looked at the document" in written["unverified"]

    assert docqa.main(["review", "--work", str(tmp_path),
                       *[f"--answer={name}=yes" for name in review.QUESTIONS],
                       "--note", "read the whole thing"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert docqa.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
