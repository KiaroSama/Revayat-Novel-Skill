"""The OCR path against the real binaries, when they are installed.

Everything else in this suite tests the *decisions*: which OCR mode a book
gets, how confidence folds up, where a figure belongs. That is deliberate —
those are the parts with consequences, and they can be checked in a second
without installing anything.

But a decision that is right and a pipeline that works are different claims,
and the second one cannot be made from mocks. These tests run OCRmyPDF,
Tesseract and the crop path over a PDF built to be genuinely unreadable
without them, and skip themselves when the tools are absent so ordinary CI
stays fast and stable.

The integration workflow installs the binaries and runs this file. Nothing
here is required for the unit tier to pass.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import bookir as ir
import qa
from extract import find_ocrmypdf, ocr_command, run_ocr

#: A tessdata directory inside the project, used when the system one cannot be
#: written to. It must be the *whole* directory, not just the language file:
#: Tesseract reads `configs/` from TESSDATA_PREFIX too, and a prefix holding
#: only `.traineddata` files fails with "read_params_file: Can't open hocr".
PROJECT_TESSDATA = Path(__file__).resolve().parents[1] / ".sample" / "tessdata"
if PROJECT_TESSDATA.is_dir() and not os.environ.get("TESSDATA_PREFIX"):
    os.environ["TESSDATA_PREFIX"] = str(PROJECT_TESSDATA)


def language_available(code: str) -> bool:
    """Is this language pack reachable by the tesseract we would actually run?"""
    prefix = os.environ.get("TESSDATA_PREFIX")
    roots = [Path(prefix)] if prefix else []
    binary = shutil.which("tesseract")
    if binary:
        roots.append(Path(binary).resolve().parent / "tessdata")
    return any((root / f"{code}.traineddata").exists() for root in roots)


HAVE_OCRMYPDF = find_ocrmypdf() is not None
HAVE_TESSERACT = shutil.which("tesseract") is not None
HAVE_GHOSTSCRIPT = any(shutil.which(name) for name in ("gs", "gswin64c", "gswin32c"))

needs_ocr = pytest.mark.skipif(
    not (HAVE_OCRMYPDF and HAVE_TESSERACT and HAVE_GHOSTSCRIPT),
    reason="OCRmyPDF, Tesseract and Ghostscript are not all installed",
)
needs_tesseract = pytest.mark.skipif(
    not HAVE_TESSERACT, reason="Tesseract is not installed"
)

SENTENCE = "The morning came slowly over the ridge and she stood there."
PLATE_TEXT = "PLATE"


def _typeset(draw, text, position, size):
    from PIL import ImageFont
    try:
        font = ImageFont.truetype("arial.ttf", size)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size)
        except OSError:
            font = ImageFont.load_default()
    draw.text(position, text, fill=(10, 10, 10), font=font)


@pytest.fixture(scope="module")
def image_only_pdf(tmp_path_factory) -> Path:
    """A page that is a picture of text — no text layer at all.

    Built at 300 DPI because that is what Tesseract is tuned for; a 96 DPI
    render of the same words recognises badly and the test would be measuring
    the fixture rather than the pipeline.
    """
    import pymupdf
    from PIL import Image, ImageDraw

    directory = tmp_path_factory.mktemp("integration")
    raster = Image.new("RGB", (2550, 3300), (255, 255, 255))
    draw = ImageDraw.Draw(raster)
    _typeset(draw, SENTENCE, (200, 300), 64)
    _typeset(draw, "A second line of ordinary prose follows it.", (200, 420), 64)
    # A dark plate low on the page, to be found and cropped later.
    draw.rectangle([700, 1800, 1850, 2600], fill=(30, 50, 110))
    _typeset(draw, PLATE_TEXT, (1150, 2150), 90)

    page_png = directory / "page.png"
    raster.save(page_png)

    document = pymupdf.open()
    page = document.new_page(width=612.0, height=792.0)
    page.insert_image(pymupdf.Rect(0, 0, 612, 792), filename=str(page_png))
    destination = directory / "image-only.pdf"
    document.save(destination)
    document.close()
    return destination


@pytest.fixture(scope="module")
def ocred(image_only_pdf, tmp_path_factory) -> Path:
    directory = tmp_path_factory.mktemp("ocr-output")
    destination = directory / "ocr.pdf"
    run_ocr(image_only_pdf, destination, kind="scanned", language="eng")
    return destination


# --------------------------------------------------------------------------- #
# 1. An image-only PDF becomes a readable book
# --------------------------------------------------------------------------- #

@needs_ocr
def test_the_fixture_really_has_no_text_before_ocr(image_only_pdf):
    """If this ever passes trivially, the test below proves nothing."""
    from extract import probe_pdf

    probe = probe_pdf(image_only_pdf)
    assert probe["kind"] == "scanned"
    assert probe["pages_with_text"] == 0


@needs_ocr
def test_ocr_gives_the_scan_a_text_layer(ocred):
    from extract import probe_pdf

    probe = probe_pdf(ocred)
    assert probe["text_share"] > 0.9, "OCR produced no usable text layer"


@needs_ocr
def test_the_recognised_words_reach_the_book(ocred, tmp_path):
    from read_pdf import read_pdf

    book = read_pdf(str(ocred), tmp_path / "assets", ocr_text=True)
    text = " ".join(b.get("text") or "" for b in book["blocks"]).lower()
    # Not an exact match: OCR is allowed to be imperfect, but the distinctive
    # words of the sentence have to be there or nothing was really read.
    for word in ("morning", "ridge", "stood"):
        assert word in text, f"{word!r} was not recognised: {text[:200]!r}"


@needs_ocr
def test_a_scanned_book_declares_it_cannot_report_emphasis(ocred, tmp_path):
    from read_pdf import read_pdf

    book = read_pdf(str(ocred), tmp_path / "assets", ocr_text=True)
    assert book["source"]["emphasis"]["recovered"] is False


# --------------------------------------------------------------------------- #
# 2 & 3. Confidence, in the source language, reaching the blocks
# --------------------------------------------------------------------------- #

@needs_tesseract
def test_the_sidecar_scores_an_english_scan_in_english(ocred, tmp_path):
    """The bug this guards: scoring English pages with the Persian model."""
    import ocr_sidecar as ocr

    sidecar = ocr.build(ocred, language="eng", dpi=300, max_pages=1)
    assert sidecar["engine"]["language"] == "eng"
    assert sidecar["summary"]["confidence"] is not None
    assert sidecar["summary"]["confidence"] > 50, sidecar["summary"]

    words = [w for page in sidecar["pages"] for block in page["blocks"]
             for line in block["lines"] for w in line["words"]]
    assert words, "no per-word records were written"
    assert all("conf" in w and "bbox" in w for w in words)


@needs_ocr
@needs_tesseract
def test_confidence_reaches_the_blocks_it_belongs_to(ocred, tmp_path):
    import ocr_sidecar as ocr
    from read_pdf import read_pdf

    book = read_pdf(str(ocred), tmp_path / "assets", ocr_text=True)
    sidecar = ocr.build(ocred, language="eng", dpi=300, max_pages=1)
    summary = ocr.attach(book, sidecar)

    assert summary["matched"] > 0, (
        f"nothing matched: {summary}. Either the boxes or the two recognitions "
        f"disagree, and both are defects worth seeing."
    )
    scored = [b for b in ir.iter_text_blocks(book) if b.get("ocr")]
    assert scored and all(b["ocr"].get("grade") for b in scored)


@needs_ocr
@needs_tesseract
def test_a_wrong_language_is_visible_rather_than_silent(ocred, tmp_path):
    """Recognised with the wrong model, the text disagrees and is refused.

    This is the whole point of the text-identity guard: a confident score for
    words the book does not contain would silence the warning instead of
    raising it.
    """
    import ocr_sidecar as ocr
    from read_pdf import read_pdf

    if not shutil.which("tesseract"):
        pytest.skip("tesseract missing")
    if not language_available("fas"):
        pytest.skip("the Persian language pack is not reachable from here")
    book = read_pdf(str(ocred), tmp_path / "assets", ocr_text=True)
    sidecar = ocr.build(ocred, language="fas", dpi=300, max_pages=1)

    summary = ocr.attach(book, sidecar)
    assert summary["disputed"] >= summary["matched"], (
        f"an English page scored with the Persian model was accepted: {summary}"
    )


# --------------------------------------------------------------------------- #
# 4. The plate is cut from the scan's own pixels
# --------------------------------------------------------------------------- #

@needs_ocr
def test_a_plate_is_cropped_from_the_original_raster(image_only_pdf, tmp_path):
    """MinerU finds the box; this checks the pixels come from the book.

    The detection is stubbed rather than run — MinerU is far too heavy to make
    an integration test depend on, and what is being checked here is the crop,
    which is ours.
    """
    from PIL import Image

    import pymupdf
    from extract import crop_from_source

    document = pymupdf.open(image_only_pdf)
    try:
        # The plate occupies roughly x 700..1850, y 1800..2600 of 2550x3300,
        # which on a 612x792pt page is x 168..444pt, y 432..624pt.
        cut = crop_from_source(document, 1, [168.0, 432.0, 444.0, 624.0],
                               tmp_path / "plate.png")
    finally:
        document.close()

    assert cut is not None
    assert cut["crop"]["method"] == "embedded-page-image", cut["crop"]
    assert cut["crop"]["resized"] is False

    image = Image.open(tmp_path / "plate.png")
    assert (image.width, image.height) == (cut["pixel_width"], cut["pixel_height"])
    assert image.width > 900, f"the crop was resampled down: {image.size}"
    # The plate is dark; a crop of the wrong region would be mostly white.
    from PIL import ImageStat
    average = ImageStat.Stat(image.convert("L")).mean[0]
    assert average < 140, f"the crop does not look like the plate (mean {average:.0f})"


# --------------------------------------------------------------------------- #
# 5. A mixed book keeps its good pages
# --------------------------------------------------------------------------- #

@needs_ocr
def test_a_native_page_is_not_re_recognised(tmp_path_factory):
    """`--skip-text` exists so accurate characters are not replaced by guesses."""
    import pymupdf
    from extract import probe_pdf

    directory = tmp_path_factory.mktemp("mixed")
    document = pymupdf.open()
    good = document.new_page(width=612.0, height=792.0)
    # Comfortably over PAGE_TEXT_THRESHOLD: a page has to carry 80 characters
    # before it counts as having a text layer, and one short sentence does not.
    for line_number, line in enumerate([
        SENTENCE,
        "A second line of ordinary prose follows it on the same page.",
        "And a third, so the page is unmistakably born digital.",
    ]):
        good.insert_text((72, 120 + line_number * 20), line, fontsize=12)
    document.new_page(width=612.0, height=792.0)  # blank, needs OCR
    source = directory / "mixed.pdf"
    document.save(source)
    document.close()

    probe = probe_pdf(source)
    assert probe["kind"] == "mixed"
    assert "--skip-text" in ocr_command(["ocrmypdf"], source,
                                        directory / "out.pdf", kind=probe["kind"])

    destination = directory / "mixed.ocr.pdf"
    run_ocr(source, destination, kind=probe["kind"], language="eng")

    reread = pymupdf.open(destination)
    try:
        assert SENTENCE in reread[0].get_text("text").replace("\n", " "), (
            "the page that already had text came back altered"
        )
    finally:
        reread.close()


# --------------------------------------------------------------------------- #
# 6. And the whole way to a Persian document
# --------------------------------------------------------------------------- #

@needs_ocr
def test_a_scan_reaches_a_verified_persian_document(ocred, tmp_path):
    import argparse

    from build_docx import Builder, add_arguments
    from read_pdf import read_pdf

    assets = tmp_path / "assets"
    book = read_pdf(str(ocred), assets, ocr_text=True)
    for block in ir.iter_text_blocks(book):
        if (block.get("text") or "").strip():
            block["target"] = (
                "ترجمهٔ این بند که به اندازهٔ کافی بلند است تا از نسبت طول رد شود "
                f"({block['id']})."
            )
    book["meta"]["title_target"] = "کتاب اسکن‌شده"

    gate = qa.check_book(book, assets=assets).summary()
    assert gate["ok"], gate["findings"]

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma"])
    destination = tmp_path / "scan.fa.docx"
    report = Builder(book, assets, options).build(destination)
    assert report["warning_count"] == 0, report["warnings"]

    package = qa.check_docx(destination, book).summary()
    assert package["ok"], package["findings"]
