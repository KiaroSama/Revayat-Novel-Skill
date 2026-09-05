"""Defects found by running the pipeline on real books.

Every test here corresponds to something that was silently wrong on a real
file and would not have been caught by reasoning about the code. They are kept
together because that provenance is the point: each one is cheap insurance
against a class of failure this project has already shipped once.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

import bookir as ir
import chunk as chunking
import merge as merging
import qa


# --------------------------------------------------------------------------- #
# The translator's own footnotes
# --------------------------------------------------------------------------- #

def test_worksheet_header_accepts_every_id_shape():
    """A hyphen in the id must not stop the header being recognised.

    When it did, a translator's `@@ tr-01 footnote` was not seen as a header at
    all: its body was swallowed into the previous paragraph and merge still
    reported success. Silent corruption, not a rejection.
    """
    for unit_id in ("b00042", "b00075#alt", "fn0007", "tr-01", "tr-note_2"):
        match = chunking.HEADER.match(f"@@ {unit_id} para")
        assert match and match.group("id") == unit_id, unit_id


def test_translator_footnote_becomes_a_real_book_footnote(tmp_path):
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", 1, text="He ate turkey on Thanksgiving."),
        ir.make_block("paragraph", 2, text="Then he left."),
    ]
    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)
    chunks = tmp_path / "chunks"
    manifest = chunking.build(book_path, chunks, glossary_path=None, budget=100_000)
    entry = manifest["chunks"][0]

    (chunks / entry["output"]).write_text(
        "@@ b00001 para\nاو در شکرگزاری بوقلمون خورد.[[fn:tr-01]]\n\n"
        "@@ b00002 para\nبعد رفت.\n\n"
        "@@ tr-01 footnote\nشکرگزاری جشنی سالانه در آمریکای شمالی است.\n",
        encoding="utf-8", newline="",
    )

    report = merging.merge(book_path, chunks)
    assert report["ok"], report
    assert report["translator_notes"][entry["id"]] == {"tr-01": "fn0001"}

    merged = ir.load_book(book_path)
    assert len(merged["footnotes"]) == 1
    note = merged["footnotes"][0]
    assert note["origin"] == "translator"
    assert note["anchor_block"] == "b00001"
    assert note["target"].startswith("شکرگزاری")
    # The inline marker points at the allocated id, and nothing leaked into prose.
    assert "[[fn:fn0001]]" in merged["blocks"][0]["target"]
    assert "tr-01" not in merged["blocks"][0]["target"]
    assert "@@" not in merged["blocks"][0]["target"]
    assert ir.validate_book(merged) == []


def test_a_leftover_translator_id_is_still_an_error():
    """The canonical token pattern stays strict, so an unallocated id is caught."""
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, text="x [[fn:tr-99]]")]
    assert ir.footnote_refs("x [[fn:tr-99]]") == []
    assert ir.ANY_FOOTNOTE_TOKEN.findall("x [[fn:tr-99]]") == ["tr-99"]


# --------------------------------------------------------------------------- #
# Caseless scripts
# --------------------------------------------------------------------------- #

def test_paragraphs_rejoin_in_a_caseless_script():
    """Persian, Arabic, Hebrew and CJK have no case.

    The merge rule tested for a lower-case opening character, and
    ``'م'.islower()`` is False — so paragraph rejoining silently never ran for
    any of them, handing the translator a book of one-line fragments.
    """
    from read_pdf import _continues, _merge_split_paragraphs

    assert _continues("م") is True        # Persian: caseless
    assert _continues("あ") is True        # Japanese: caseless
    assert _continues("a") is True        # lower case continues
    assert _continues("A") is False       # a capital starts something new

    blocks = [
        ir.make_block("paragraph", 1, text="او به سمت پنجره رفت و"),
        ir.make_block("paragraph", 2, text="بیرون را نگاه کرد."),
    ]
    merged = _merge_split_paragraphs(blocks)
    assert len(merged) == 1
    assert merged[0]["text"] == "او به سمت پنجره رفت و بیرون را نگاه کرد."


def test_a_finished_persian_sentence_is_not_merged():
    from read_pdf import _merge_split_paragraphs

    blocks = [
        ir.make_block("paragraph", 1, text="او رفت."),
        ir.make_block("paragraph", 2, text="فردا برگشت."),
    ]
    assert len(_merge_split_paragraphs(blocks)) == 2


# --------------------------------------------------------------------------- #
# OCR text layers
# --------------------------------------------------------------------------- #

def test_ocr_size_jitter_does_not_invent_headings():
    """OCR reports a fitted size per line, not typography.

    Measured on a real scan, one uniform body paragraph produced spans from
    11.5 to 15.0pt. Read with born-digital thresholds that turned ordinary
    prose into 939 false headings across 70 pages.
    """
    from read_pdf import _heading_level

    body = 12.0
    jittered = 15.0                      # ratio 1.25 — inside the digital rules
    assert _heading_level(jittered, body, "just an ordinary line", False) == 3
    assert _heading_level(jittered, body, "just an ordinary line", False, ocr=True) is None
    # A genuine chapter opener still registers, in either language.
    assert _heading_level(body, body, "Chapter Nine", False, ocr=True) == 2
    assert _heading_level(body, body, "فصل نهم", False, ocr=True) == 2
    # And an unmistakable size jump still counts.
    assert _heading_level(24.0, body, "A Real Title", False, ocr=True) == 1


def test_ocr_output_is_judged_by_the_artefact_not_the_exit_code(tmp_path):
    """OCRmyPDF returns non-zero for conditions that still produce a good file."""
    from extract import _usable_ocr_output

    missing = tmp_path / "nope.pdf"
    ok, detail = _usable_ocr_output(missing)
    assert not ok and "no output file" in detail

    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    ok, detail = _usable_ocr_output(broken)
    assert not ok

    pymupdf = pytest.importorskip("pymupdf")
    good = tmp_path / "good.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "recognised text here", fontsize=12)
    doc.save(str(good))
    doc.close()
    ok, detail = _usable_ocr_output(good)
    assert ok and "characters" in detail


# --------------------------------------------------------------------------- #
# Watermark removal
# --------------------------------------------------------------------------- #

def _page_with_watermark(size=(300, 400)):
    """A page of grayscale "text" with a small colour watermark over it.

    The stamp is kept to roughly 2% of the page: a real one measured 0.24%, and
    the cleaner deliberately refuses to touch a page that is mostly colour,
    because that is an illustration rather than a watermark.
    """
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from PIL import ImageDraw

    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for y in range(40, 360, 20):                     # grayscale "text"
        draw.line([(30, y), (270, y)], fill=(10, 10, 10), width=3)
    draw.ellipse([130, 180, 190, 240], fill=(220, 90, 40))   # colour watermark
    return image


def test_colour_watermark_is_removed_and_text_survives():
    from scan_clean import clean_image, coloured_fraction

    page = _page_with_watermark()
    cleaned, report = clean_image(page)
    assert report["cleaned"] is True
    assert coloured_fraction(cleaned.convert("RGB")) == pytest.approx(0.0, abs=1e-4)

    # The dark strokes must still be there — an all-white page is the failure
    # mode this guards: rewriting the image stream in place once produced
    # exactly that, and OCR fell from 864 characters to zero.
    dark = sum(n for value, n in enumerate(cleaned.convert("L").histogram())
               if value < 100)
    assert dark > 1000


def test_a_colourful_page_is_left_alone_as_artwork():
    """An illustration page must not be wiped by the watermark rule."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from scan_clean import clean_image

    artwork = Image.new("RGB", (300, 400), (200, 60, 60))
    cleaned, report = clean_image(artwork)
    assert report["cleaned"] is False
    assert report["reason"] == "looks like artwork"
    assert cleaned.convert("RGB").getpixel((0, 0)) == (200, 60, 60)


def test_cleaned_pdf_keeps_every_page_and_stays_readable(tmp_path):
    """The rebuilt document must have the same pages, still decodable.

    Patching the image stream in place left the XObject's /Filter describing
    the old bytes, so every cleaned page became an unreadable image while the
    PDF still opened. Page count alone would not have caught it.
    """
    pymupdf = pytest.importorskip("pymupdf")
    pytest.importorskip("PIL.Image", reason="Pillow not installed")
    from scan_clean import clean_pdf

    buffer = io.BytesIO()
    _page_with_watermark().save(buffer, format="JPEG", quality=95)

    source = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page(width=300, height=400)
        page.insert_image(pymupdf.Rect(0, 0, 300, 400), stream=buffer.getvalue())
    doc.save(str(source))
    doc.close()

    report = clean_pdf(source, tmp_path / "clean.pdf")
    assert report["cleaned"] == 3

    result = pymupdf.open(tmp_path / "clean.pdf")
    try:
        assert len(result) == 3
        for page in result:
            images = page.get_images(full=True)
            assert images, "a cleaned page lost its image"
            raw = result.extract_image(images[0][0])
            assert raw["width"] > 0 and len(raw["image"]) > 0
    finally:
        result.close()


# --------------------------------------------------------------------------- #
# Fidelity options
# --------------------------------------------------------------------------- #

def test_first_mention_is_decided_once_not_per_chunk():
    """Parallel chunks cannot each decide they hold the first mention."""
    import glossary as gl

    entry = gl.make_entry(1, "Elizabeth Bennet", frequency=9)
    entry.update({
        "target": "الیزابت بنت", "later_form": "الیزابت بنت",
        "first_form": "الیزابت بنت (Elizabeth Bennet)",
        "first_block_id": "b00007",
    })
    glossary = gl.new_glossary()
    glossary["entries"] = [entry]

    introducing = gl.render_term_table([entry], glossary["policy"],
                                       block_ids=["b00006", "b00007"])
    assert "(Elizabeth Bennet)" in introducing
    assert "first mention" in introducing

    later = gl.render_term_table([entry], glossary["policy"],
                                 block_ids=["b00100", "b00101"])
    assert "(Elizabeth Bennet)" not in later
    assert "الیزابت بنت" in later


def test_scan_records_where_each_name_first_appears():
    import glossary as gl

    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", 1, text="A quiet opening with nobody in it."),
        ir.make_block("paragraph", 2, text="Then Elizabeth Bennet arrived at last."),
        ir.make_block("paragraph", 3, text="Elizabeth Bennet sat, and Elizabeth spoke."),
    ]
    entry = next(e for e in gl.scan(book, minimum=2) if e["source"] == "Elizabeth Bennet")
    assert entry["first_block_id"] == "b00002"


def test_strict_qa_promotes_fidelity_warnings_to_errors():
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, text="He said *no* to her.")]
    book["blocks"][0]["target"] = "او به او نه گفت."      # emphasis dropped

    relaxed = qa.check_book(book).summary()
    assert relaxed["ok"] is True
    assert "emphasis-parity" in relaxed["by_code"]

    strict = qa.check_book(book, strict=True).summary()
    assert strict["ok"] is False
    assert strict["errors"] >= 1


def test_heading_size_can_reproduce_the_source(translated_book, tmp_path):
    import argparse

    from docx import Document
    from build_docx import Builder, add_arguments

    book, assets = translated_book
    for block in book["blocks"]:
        if block["type"] == "heading":
            block["font_size_pt"] = 19.0

    parser = argparse.ArgumentParser()
    add_arguments(parser)

    default = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma"])
    exact = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma",
                               "--heading-size", "source"])

    Builder(book, assets, default).build(tmp_path / "styled.docx")
    Builder(book, assets, exact).build(tmp_path / "exact.docx")

    def heading_sizes(path):
        return [
            run.font.size.pt
            for paragraph in Document(str(path)).paragraphs
            if paragraph.style.name.startswith("Heading")
            for run in paragraph.runs
            if run.font.size is not None
        ]

    assert 19.0 in heading_sizes(tmp_path / "exact.docx")
    assert 19.0 not in heading_sizes(tmp_path / "styled.docx")


def test_mineru_bbox_is_converted_to_points_not_discarded():
    from extract import _mineru_bbox

    page = {"width_pt": 600.0, "height_pt": 800.0}
    assert _mineru_bbox([0, 0, 1000, 1000], page) == [0.0, 0.0, 600.0, 800.0]
    assert _mineru_bbox([100, 250, 500, 750], page) == [60.0, 200.0, 300.0, 600.0]
    assert _mineru_bbox(None, page) is None
    assert _mineru_bbox([1, 2, 3], page) is None


# --------------------------------------------------------------------------- #
# Scanned pages are not illustrations
# --------------------------------------------------------------------------- #

def test_a_scanned_text_page_is_not_emitted_as_an_illustration():
    """In a scanned book every page is one full-page raster.

    Emitting those as pictures puts the whole book into the output twice — once
    as recognised text, once as photographs of the same pages. Measured on a
    real 70-page scan: 70 of 70 "images" were the pages themselves, and the
    DOCX came out at 13.8 MB instead of 2.6 MB.
    """
    from read_pdf import _is_the_page_itself

    page_area = 595.0 * 842.0
    full_page = {"width_pt": 595.0, "height_pt": 842.0}
    plate = {"width_pt": 300.0, "height_pt": 200.0}

    # A page-sized raster on a page full of words is the scan of that page.
    assert _is_the_page_itself(full_page, page_area, page_chars=1130) is True
    # The same raster on a page with no words is a real full-page plate.
    assert _is_the_page_itself(full_page, page_area, page_chars=0) is False
    assert _is_the_page_itself(full_page, page_area, page_chars=40) is False
    # A picture that only covers part of the page is always content.
    assert _is_the_page_itself(plate, page_area, page_chars=1130) is False
    # Missing geometry must never cause a silent drop.
    assert _is_the_page_itself({"width_pt": None, "height_pt": None},
                               page_area, page_chars=1130) is False


def test_page_scan_dropping_is_reported_not_silent(scanned_pdf, tmp_path):
    """Dropping content needs to be visible in the extract report."""
    from read_pdf import read_pdf

    book = read_pdf(str(scanned_pdf), tmp_path / "assets", ocr_text=True)
    assert "page_scans_dropped" in book["source"]
