"""PDF scan classification, page geometry and emphasis limitations."""

from __future__ import annotations

from pathlib import Path
import bookir as ir
from extract import probe_pdf

def test_probe_classifies_born_digital(sample_pdf):
    probe = probe_pdf(Path(sample_pdf))
    assert probe["kind"] == "digital"
    assert probe["pages_without_text"] == []


def test_probe_classifies_a_full_scan(scanned_pdf):
    probe = probe_pdf(scanned_pdf)
    assert probe["kind"] == "scanned"
    assert probe["pages_with_text"] == 0
    assert probe["median_chars_per_page"] == 0


def test_probe_classifies_a_mixed_book_and_names_the_scanned_pages(mixed_pdf):
    """The case that matters: OCR must not touch the page that is already fine."""
    probe = probe_pdf(mixed_pdf)
    assert probe["kind"] == "mixed"
    assert probe["pages_with_text"] == 1
    assert probe["pages_without_text"] == [2]


def _mixed_geometry_pdf(tmp_path) -> Path:
    """A book whose first page is a landscape plate and the rest is a novel."""
    import pymupdf

    document = pymupdf.open()
    landscape = document.new_page(width=612.0, height=396.0)
    landscape.insert_text((60, 120), "A fold-out map at the front.", fontsize=11)
    for _ in range(4):
        page = document.new_page(width=396.0, height=612.0)
        page.insert_text((60, 120), "Ordinary novel prose on this page.",
                         fontsize=11)
    destination = tmp_path / "mixed-geometry.pdf"
    document.save(destination)
    document.close()
    return destination


def test_the_dominant_page_shape_wins_not_the_first_one(tmp_path):
    """A landscape map at the front used to make the whole book landscape."""
    from read_pdf import read_pdf

    book = read_pdf(str(_mixed_geometry_pdf(tmp_path)), tmp_path / "assets")
    assert (book["page"]["width_pt"], book["page"]["height_pt"]) == (396.0, 612.0)


def test_the_pages_that_disagree_are_recorded(tmp_path):
    from read_pdf import read_pdf

    book = read_pdf(str(_mixed_geometry_pdf(tmp_path)), tmp_path / "assets")
    geometry = book["source"]["page_geometry"]
    assert geometry["uniform"] is False
    assert [v["page_count"] for v in geometry["variants"]] == [4, 1]
    assert geometry["variants"][1]["pages"] == [1]


def test_a_uniform_book_says_so(tmp_path, sample_pdf):
    from read_pdf import read_pdf

    book = read_pdf(str(sample_pdf), tmp_path / "assets")
    geometry = book["source"]["page_geometry"]
    assert geometry["uniform"] is True and len(geometry["variants"]) == 1


def test_qa_warns_that_the_odd_pages_will_be_reflowed(tmp_path):
    """Reflowing them is a fair choice; doing it silently is not."""
    import qa
    from read_pdf import read_pdf

    book = read_pdf(str(_mixed_geometry_pdf(tmp_path)), tmp_path / "assets")
    for block in ir.iter_text_blocks(book):
        block["target"] = "ترجمهٔ این بند که به اندازهٔ کافی بلند است تا رد شود."

    summary = qa.check_book(book).summary()
    mixed = [f for f in summary["findings"] if f["code"] == "page-geometry-mixed"]
    assert len(mixed) == 1
    assert "612x396pt on 1 page" in mixed[0]["detail"]
    # A warning, not a block: the book is still buildable.
    assert summary["ok"] is True


def test_an_ocr_text_layer_is_not_evidence_of_typography():
    """OCRmyPDF writes one invisible glyphless face; it knows no italics.

    Worse than useless as evidence: a font name that happens to contain a style
    word would make the whole scanned book bold.
    """
    from read_pdf import _style_of

    ocr_span = {"font": "GlyphLessFont", "flags": 0}
    assert _style_of(ocr_span) == (False, False)
    # Even a span that claims a style is refused on the OCR path.
    assert _style_of({"font": "SomeBoldItalic", "flags": 0}, ocr=True) == (False, False)
    # A born-digital span is still read normally.
    assert _style_of({"font": "AGaramondPro-BoldItalic", "flags": 0}) == (True, True)


def test_a_scanned_book_says_its_emphasis_could_not_be_read(scanned_pdf, tmp_path):
    """Otherwise "no italics anywhere" reads as a fact about the book."""
    from read_pdf import read_pdf

    book = read_pdf(str(scanned_pdf), tmp_path / "assets", ocr_text=True)
    emphasis = book["source"]["emphasis"]
    assert emphasis["recovered"] is False and emphasis["reason"]


def test_a_digital_book_makes_no_such_claim(sample_pdf, tmp_path):
    from read_pdf import read_pdf

    book = read_pdf(str(sample_pdf), tmp_path / "assets")
    assert "emphasis" not in book["source"]


def test_qa_passes_the_limitation_on_to_the_reader(scanned_pdf, tmp_path):
    import qa
    from read_pdf import read_pdf

    book = read_pdf(str(scanned_pdf), tmp_path / "assets", ocr_text=True)
    for block in ir.iter_text_blocks(book):
        block["target"] = "ترجمهٔ این بند که به اندازهٔ کافی بلند است تا رد شود."

    summary = qa.check_book(book).summary()
    flagged = [f for f in summary["findings"] if f["code"] == "emphasis-unrecoverable"]
    assert len(flagged) == 1 and flagged[0]["severity"] == qa.WARNING
