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

Two more claims are proved the same way. The final review is bound to the
**rendered sheets**, so a document whose bytes never changed but whose pages
came out different makes the review stale — and going back to the pages the
reviewer actually saw makes it current again. And a book of several sections is
judged section by section, so a landscape plate is not reported as a wrong page
size, while a page at a size no section declares still is.
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
import qa
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
                          render=docqa.visual_hash(work_dir))
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


def test_a_review_does_not_survive_the_pages_coming_out_different(tmp_path):
    """The .docx is evidence of *which book*, never of what the reviewer saw.

    Same file, byte for byte, laid out to different sheets — which is what a
    font fallback, a different backend or a newer renderer does to one
    unchanged document. Bound to the .docx hash this passes, because nothing
    the gate was looking at moved. Bound to the sheets it is stale, which is
    the truth: the pages that were approved are not the pages that are there.

    Then the second half, which is what makes it a digest rather than a
    counter: render the sheets the reviewer actually saw again, and the same
    review is current again without anybody re-filing it.
    """
    pytest.importorskip("pymupdf")
    book_path = _book(tmp_path)
    docx = _built(tmp_path, book_path)
    passed = _laid_out(tmp_path, book_path, docx)
    if not passed.get("verified"):
        pytest.skip("nothing on this machine lays a document out")
    assert passed["ok"] is True

    coarser = docqa.check_document(tmp_path, book_path, docx, dpi=60)

    assert coarser["document_sha256"] == passed["document_sha256"], (
        "the document was not touched; only the pages made from it"
    )
    assert coarser["visual_render_sha256"] != passed["visual_render_sha256"]
    assert coarser["verified"] is False
    assert "rendered again" in coarser["unverified"]

    again = docqa.check_document(tmp_path, book_path, docx)
    assert again["visual_render_sha256"] == passed["visual_render_sha256"], (
        "the same document rendered the same way is the same evidence"
    )
    assert again["verified"] is True and again["ok"] is True


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


# --------------------------------------------------------------------------- #
# A book of more than one section
# --------------------------------------------------------------------------- #

MORE_PERSIAN = [
    "باد از میان درختان می‌گذشت و صدای برگ‌ها تا ایوان می‌رسید.",
    "او نامه را تا کرد و در جیب پالتوی خاکستری‌اش گذاشت.",
    "تا غروب کسی از راه نرسید و چراغ‌های ده یکی‌یکی روشن شد.",
]


def _section(index: int, start: str, geometry: dict,
             orientation: str = "portrait") -> dict:
    """One `book["sections"]` record in the shape `read_docx` produces."""
    return {"index": index, "start_block": start, "start_type": "nextPage",
            "orientation": orientation, "gutter_pt": 0.0,
            "header_distance_pt": 36.0, "footer_distance_pt": 36.0, **geometry}


def _sectioned_book(tmp_path: Path) -> Path:
    """Three sections: the house trim, a landscape plate, and a smaller trim.

    Derived from `default_page_setup` rather than written out as numbers, so a
    change to the house trim can never read as a rendering defect here — the
    trap seven other tests already fell into once.
    """
    page = ir.default_page_setup()
    landscape = {**page, "width_pt": page["height_pt"],
                 "height_pt": page["width_pt"]}
    smaller = {**page, "width_pt": round(page["width_pt"] * 0.75, 2),
               "height_pt": round(page["height_pt"] * 0.75, 2)}

    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    book["page"] = dict(page)
    book["blocks"] = []
    for index, target in enumerate(PERSIAN + MORE_PERSIAN, start=1):
        block = ir.make_block("paragraph", index, page=1, bbox=[72, 90, 320, 140],
                              text=f"An English paragraph number {index} here.")
        block["target"] = target
        book["blocks"].append(block)
    book["sections"] = [
        _section(0, "b00001", page),
        _section(1, "b00003", landscape, orientation="landscape"),
        _section(2, "b00005", smaller),
    ]
    path = tmp_path / "sectioned.json"
    ir.save_book(book, path)
    return path


def test_the_geometry_a_page_is_judged_by_is_the_one_it_is_nearest(tmp_path):
    """No render needed: the mapping itself, and what it admits it cannot do."""
    setups = docqa.page_setups(ir.load_book(_sectioned_book(tmp_path)))
    house, landscape, smaller = setups[1], setups[2], setups[3]

    for wanted in (house, landscape, smaller):
        view = {"width_pt": wanted["width_pt"], "height_pt": wanted["height_pt"]}
        picked = docqa.setup_for(view, setups)
        assert (picked["width_pt"], picked["height_pt"]) == (
            wanted["width_pt"], wanted["height_pt"])

    # A size nothing declares still gets the nearest one, so the page-size
    # finding names the geometry the page most nearly is rather than whichever
    # section happens to be first.
    stray = {"width_pt": smaller["width_pt"] + 40, "height_pt": smaller["height_pt"]}
    assert docqa.setup_for(stray, setups)["height_pt"] == smaller["height_pt"]


def test_a_landscape_and_a_resized_section_are_not_reported_as_wrong_pages(tmp_path):
    """The whole document, end to end, with three geometries in it.

    Judged against the opening section alone, every page of the other two comes
    back as a `page-size` error — a correct book failing, which is the shape of
    defect that teaches a reader to stop reading the report.
    """
    pytest.importorskip("pymupdf")
    book_path = _sectioned_book(tmp_path)
    report = _laid_out(tmp_path, book_path, _built(tmp_path, book_path))

    assert report["verified"] is True
    assert report["findings"] == [], "a correctly assembled book reported nothing"
    assert report["ok"] is True
    assert report["pages"] >= 3, "one page per section at least"


def _resized_section(docx: Path, destination: Path) -> Path:
    """Give the landscape section a page size no section of the book declares.

    The package is edited, not the book: the IR must go on saying what the
    sections are, because the defect being modelled is a build that produced a
    page nobody asked for.
    """
    import re

    with zipfile.ZipFile(docx) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}

    body = parts["word/document.xml"].decode("utf-8")
    landscape = re.findall(r'<w:pgSz [^>]*w:orient="landscape"[^>]*/>', body)
    assert len(landscape) == 1, (
        f"the fixture is not what this test assumes: {len(landscape)} landscape "
        f"sections in the built document"
    )
    # 900x1200pt: nothing near any declared geometry, and larger than all of
    # them, so the margins stay valid and the only thing wrong is the size.
    parts["word/document.xml"] = body.replace(
        landscape[0], '<w:pgSz w:w="18000" w:h="24000"/>').encode("utf-8")

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as out:
        for name, blob in parts.items():
            out.writestr(name, blob)
    return destination


def test_a_page_at_a_size_no_section_declares_is_still_reported(tmp_path):
    """The other half of the same claim, and the reason it is not a loosening.

    Knowing more than one geometry is legitimate must not become accepting any
    geometry at all. A page at a size the book never asked for is what a lost or
    corrupted section break looks like from the outside, and it still fails.
    """
    pytest.importorskip("pymupdf")
    book_path = _sectioned_book(tmp_path)
    damaged = _resized_section(_built(tmp_path, book_path),
                              tmp_path / "resized.docx")
    after = _laid_out(tmp_path, book_path, damaged)

    assert after["verified"] is True, "the damaged document still renders"
    assert after["ok"] is False, "a page at an undeclared size passed"
    wrong = [f for f in after["findings"] if f["code"] == "page-size"]
    assert wrong, "the page-size check did not fire"
    assert any(page["ok"] for page in after["per_page"]), (
        "only the resized section is wrong; the rest of the book is not"
    )


# --------------------------------------------------------------------------- #
# Section order, not only section membership
# --------------------------------------------------------------------------- #

def _page_sizes(docx: Path) -> list[str]:
    """Every `w:pgSz` in the built document, in document order."""
    import re

    with zipfile.ZipFile(docx) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
    return re.findall(r"<w:pgSz [^>]*/>", body)


def _rewrite_page_sizes(docx: Path, destination: Path,
                        sizes: list[str]) -> Path:
    """Put a different ordered list of `w:pgSz` into the same document.

    Every element is one the book genuinely declares — the whole point is that
    each page is individually a legal size, so nearest-geometry matching, which
    only ever asks whether a page belongs to the allowed set, cannot see it.
    """
    import re

    with zipfile.ZipFile(docx) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    body = parts["word/document.xml"].decode("utf-8")
    replacement = iter(sizes)
    body = re.sub(r"<w:pgSz [^>]*/>", lambda _: next(replacement), body)
    parts["word/document.xml"] = body.encode("utf-8")

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as out:
        for name, blob in parts.items():
            out.writestr(name, blob)
    return destination


def test_valid_section_sizes_in_the_wrong_order_are_caught(tmp_path):
    """A, C, B out of every size the book declares, and none of them wrong.

    This is what nearest-geometry matching cannot see and never claimed to:
    each page is measured against the declared shape it is closest to, so a
    document whose sections came out in the wrong order is three runs of
    perfectly legal pages. The package knows the order; the paper does not.
    """
    pytest.importorskip("pymupdf")
    book_path = _sectioned_book(tmp_path)
    docx = _built(tmp_path, book_path)

    sizes = _page_sizes(docx)
    assert len(sizes) == 3, f"the fixture changed shape: {sizes}"
    swapped = _rewrite_page_sizes(docx, tmp_path / "swapped.docx",
                                  [sizes[0], sizes[2], sizes[1]])

    after = _laid_out(tmp_path, book_path, swapped)
    codes = {finding["code"] for finding in after["findings"]}
    assert {"section-size", "section-order"} & codes, (
        f"sections B and C swapped and nothing objected: {after['findings']}")
    assert after["ok"] is False


def test_a_duplicated_section_with_one_omitted_is_caught(tmp_path):
    """A, B, B: every size declared, one section silently gone."""
    pytest.importorskip("pymupdf")
    book_path = _sectioned_book(tmp_path)
    docx = _built(tmp_path, book_path)

    sizes = _page_sizes(docx)
    doubled = _rewrite_page_sizes(docx, tmp_path / "doubled.docx",
                                  [sizes[0], sizes[1], sizes[1]])

    after = _laid_out(tmp_path, book_path, doubled)
    codes = {finding["code"] for finding in after["findings"]}
    assert {"section-size", "section-order", "section-property"} & codes, (
        f"a section was duplicated and another lost, unreported: "
        f"{after['findings']}")
    assert after["ok"] is False


def test_the_built_section_count_is_compared_with_the_book(tmp_path):
    """One `sectPr` more or fewer than the book declares is a defect.

    Checked at the package rather than on the paper, because a dropped section
    break does not necessarily change any page's size — it changes which pages
    belong to which section, and that is invisible once rendered.
    """
    book_path = _sectioned_book(tmp_path)
    docx = _built(tmp_path, book_path)

    book = ir.load_book(book_path)
    book["sections"] = book["sections"] + [dict(book["sections"][0])]

    report = qa.Report()
    docqa.check_section_package(docx, book, report)
    codes = {finding["code"] for finding in report.summary()["findings"]}
    assert "section-count" in codes, report.summary()["findings"]


def test_the_render_walk_accepts_what_it_genuinely_cannot_tell_apart(tmp_path):
    """The order check must not invent failures where the paper is ambiguous.

    Repeated pages inside one section, and adjacent sections of the same trim,
    are indistinguishable on paper and legal. A gate that reported either would
    fail every ordinary book, which is the way this kind of check usually dies.
    """
    book = ir.load_book(_sectioned_book(tmp_path))
    declared = docqa.declared_sections(book)
    assert len(declared) == 3

    def codes(order: list[int]) -> set[str]:
        views = [{"width_pt": declared[i]["width_pt"],
                  "height_pt": declared[i]["height_pt"]} for i in order]
        report = qa.Report()
        docqa.check_section_order(views, declared, report)
        return {finding["code"] for finding in report.summary()["findings"]}

    assert codes([0, 1, 2]) == set(), "the correct order was reported"
    assert codes([0, 0, 0, 1, 1, 2, 2, 2]) == set(), (
        "several pages inside each section were reported as an order defect")
    assert codes([0, 1]) == set(), "a book that stops early is not out of order"
    assert "section-order" in codes([0, 2, 1]), (
        "the rendered pages went back to an earlier section's shape and the "
        "walk did not object")
