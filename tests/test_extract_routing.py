"""Which pages get OCR'd, and when a cached OCR may be believed.

Two decisions with the same failure mode: the book looks complete, every page
is present, and one page of prose is silently missing or silently out of date.
Neither is visible downstream — a page that contributed no text is just a short
page, and a reused `ocr.pdf` carries no record of what produced it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "revayat-novel" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import extract  # noqa: E402
import runstate  # noqa: E402

pymupdf = pytest.importorskip("pymupdf")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _prose_page(doc, number: int) -> None:
    """A page with a real text layer, comfortably over the per-page threshold."""
    page = doc.new_page(width=396, height=612)
    y = 90.0
    for line in (
        f"Chapter {number}",
        "The morning came slowly over the ridge and Elizabeth",
        "Bennet stood beside the window, watching the light",
        "move across the valley floor towards the river below.",
        "She had not slept at all that night, nor the one before.",
    ):
        page.insert_text((54, y), line, fontsize=11, fontname="helv")
        y += 22


def _rasterised_prose_page(doc, source_page) -> None:
    """The same prose, as pixels only — a scanned page inside a digital book."""
    pixmap = source_page.get_pixmap(dpi=150)
    doc.new_page(width=396, height=612).insert_image(
        pymupdf.Rect(0, 0, 396, 612), pixmap=pixmap)


@pytest.fixture
def minority_scan_pdf(tmp_path: Path) -> Path:
    """Twelve pages with text and one page that is a scan of prose.

    12/13 is 0.923, which clears the 0.92 digital threshold — so the whole book
    is called digital and the one page that needed OCR never gets it.
    """
    source = pymupdf.open()
    _prose_page(source, 1)

    book = pymupdf.open()
    for number in range(1, 13):
        _prose_page(book, number)
    _rasterised_prose_page(book, source[0])

    path = tmp_path / "minority.pdf"
    book.save(str(path))
    book.close()
    source.close()
    return path


@pytest.fixture
def other_scan_pdf(tmp_path: Path) -> Path:
    """A second minority-scan book, different prose — a corrected scan's shape."""
    source = pymupdf.open()
    _prose_page(source, 99)

    book = pymupdf.open()
    for number in range(20, 32):
        _prose_page(book, number)
    _rasterised_prose_page(book, source[0])

    path = tmp_path / "other.pdf"
    book.save(str(path))
    book.close()
    source.close()
    return path


@pytest.fixture
def blank_tail_pdf(tmp_path: Path) -> Path:
    """Twelve pages with text and one genuinely blank page.

    The control for the fixture above: a blank verso is not a scan, and calling
    this book mixed would send a perfectly good digital text layer through OCR.
    """
    book = pymupdf.open()
    for number in range(1, 13):
        _prose_page(book, number)
    book.new_page(width=396, height=612)

    path = tmp_path / "blank-tail.pdf"
    book.save(str(path))
    book.close()
    return path


# --------------------------------------------------------------------------- #
# S01 — a minority of scanned pages
# --------------------------------------------------------------------------- #

def test_a_single_scanned_page_in_a_digital_book_is_not_called_digital(
        minority_scan_pdf):
    """One page of prose, reachable only through OCR, in a book the router
    declares needs none."""
    probe = extract.probe_pdf(minority_scan_pdf)

    assert probe["pages"] == 13
    assert probe["text_share"] >= extract.DIGITAL_PAGE_SHARE, (
        "the fixture must clear the digital threshold, or it proves nothing")
    assert probe["kind"] == "mixed", probe
    assert probe["scan_candidates"] == [13], probe


def test_a_blank_page_does_not_make_a_digital_book_mixed(blank_tail_pdf):
    """The negative control, and the reason this cannot simply be "any page
    without text": a blank verso would send every good page through OCR."""
    probe = extract.probe_pdf(blank_tail_pdf)

    assert probe["pages"] == 13
    assert probe["kind"] == "digital", probe
    assert probe["scan_candidates"] == [], probe


def test_a_book_with_no_text_at_all_is_still_scanned(tmp_path):
    """Unchanged behaviour for the ordinary scan."""
    source = pymupdf.open()
    _prose_page(source, 1)
    scan = pymupdf.open()
    for _ in range(3):
        _rasterised_prose_page(scan, source[0])
    path = tmp_path / "scan.pdf"
    scan.save(str(path))
    scan.close()
    source.close()

    probe = extract.probe_pdf(path)
    assert probe["kind"] == "scanned", probe
    assert probe["scan_candidates"] == [1, 2, 3], probe


def test_a_mixed_book_ocrs_without_retyping_the_pages_that_have_text():
    """``--skip-text`` is what makes reclassifying safe: the digital pages keep
    their accurate characters and only the scan is recognised."""
    command = extract.ocr_command(["ocrmypdf"], Path("in.pdf"), Path("out.pdf"),
                                  kind="mixed", language="eng")
    assert "--skip-text" in command
    assert "--force-ocr" not in command
    assert "--deskew" not in command, (
        "deskew rewrites the raster, which would damage the good pages")


# --------------------------------------------------------------------------- #
# S02 — a cached OCR is only as good as its key
# --------------------------------------------------------------------------- #

class _Args:
    """The argument surface `_extract_native` actually reads."""

    def __init__(self, source: Path, **overrides):
        self.input = str(source)
        self.source_lang = "en"
        self.target_lang = "fa-IR"
        self.ocr = "auto"
        self.ocr_lang = "eng"
        self.clean_scan = "off"
        self.ghost_threshold = None
        self.deskew = None
        self.force_ocr = False
        self.ocr_timeout = 60
        self.max_pages = 0
        self.figures_from_mineru = None
        self.figures_page_offset = 0
        self.from_mineru = None
        self.from_markdown = None
        self.out = ""
        self.__dict__.update(overrides)


@pytest.fixture
def counted_ocr(monkeypatch, tmp_path):
    """A stand-in for OCRmyPDF that records every time it is asked to run.

    The decision under test is whether the cached result is *believed*, which is
    the part that has consequences; recognising characters is not.
    """
    calls: list[dict] = []

    def fake_run_ocr(source, destination, *, kind, language, deskew, timeout):
        calls.append({"source": Path(source).read_bytes()[:16],
                      "language": language})
        doc = pymupdf.open(source)
        doc.save(str(destination))
        doc.close()
        return {"engine": "stub", "pages": 1}

    monkeypatch.setattr(extract, "run_ocr", fake_run_ocr)
    return calls


def _extract(args, out_dir: Path) -> dict:
    """Through the public entry point, which is where the key is recorded."""
    args.out = str(out_dir)
    return extract.extract(args)


def test_an_unchanged_source_reuses_the_cached_ocr(minority_scan_pdf, tmp_path,
                                                   counted_ocr):
    """The positive control. Re-running must not redo an hour of OCR."""
    work = tmp_path / "work"
    _extract(_Args(minority_scan_pdf), work)
    assert len(counted_ocr) == 1

    _extract(_Args(minority_scan_pdf), work)
    assert len(counted_ocr) == 1, "the cached OCR was thrown away"


def test_a_replaced_source_does_not_reuse_the_old_ocr(minority_scan_pdf,
                                                      other_scan_pdf, tmp_path,
                                                      counted_ocr):
    """The defect: `ocr.pdf` was reused because it existed.

    A corrected scan put in place of the old one therefore produced a book whose
    text came from the file that was replaced, with the new file's provenance
    recorded against it.
    """
    work = tmp_path / "work"
    _extract(_Args(minority_scan_pdf), work)
    assert len(counted_ocr) == 1

    replaced = tmp_path / "replaced.pdf"
    replaced.write_bytes(minority_scan_pdf.read_bytes())
    _extract(_Args(minority_scan_pdf), work)          # same file, still cached
    assert len(counted_ocr) == 1

    # Now the source really is a different document — still one that needs OCR,
    # or no OCR would be attempted and this would prove nothing.
    different = pymupdf.open(other_scan_pdf)
    different.save(str(minority_scan_pdf), incremental=False)
    different.close()

    _extract(_Args(minority_scan_pdf), work)
    assert len(counted_ocr) == 2, "a replaced source reused the old OCR"


def test_a_changed_ocr_language_does_not_reuse_the_old_ocr(minority_scan_pdf,
                                                           tmp_path,
                                                           counted_ocr):
    """A different model reads different words; the old text answers the old
    option."""
    work = tmp_path / "work"
    _extract(_Args(minority_scan_pdf), work)
    assert len(counted_ocr) == 1

    _extract(_Args(minority_scan_pdf, ocr_lang="fas"), work)
    assert len(counted_ocr) == 2, "a changed --ocr-lang reused the old OCR"
    assert counted_ocr[-1]["language"] == "fas"


def test_a_failed_converter_does_not_leave_the_old_ocr_in_place(
        minority_scan_pdf, other_scan_pdf, tmp_path, counted_ocr, monkeypatch):
    """The worst shape: the converter fails and the stale file is still there.

    Reusing it would attach the new source's provenance to the old text, which
    is indistinguishable downstream from a correct extraction.
    """
    work = tmp_path / "work"
    _extract(_Args(minority_scan_pdf), work)

    different = pymupdf.open(other_scan_pdf)
    different.save(str(minority_scan_pdf), incremental=False)
    different.close()

    def fail(*_args, **_kwargs):
        raise extract.ExtractError("ocrmypdf fell over")

    monkeypatch.setattr(extract, "run_ocr", fail)

    with pytest.raises(extract.ExtractError):
        _extract(_Args(minority_scan_pdf), work)


def test_the_recorded_inputs_cover_every_option_that_changes_the_result(tmp_path):
    """The key is only as good as what it contains.

    A recorded key missing an option means that option can move without the
    cache noticing, which is the whole defect restated.
    """
    recorded = extract.ocr_inputs(_Args(tmp_path / "x.pdf"))
    for option in ("source", "ocr", "ocr_lang", "deskew", "clean_scan",
                   "ghost_threshold"):
        assert option in recorded, (option, recorded)


def test_force_ocr_redoes_the_work_even_when_the_key_matches(minority_scan_pdf,
                                                             tmp_path,
                                                             counted_ocr):
    """``--force-ocr`` is the operator overriding the cache, and must still."""
    work = tmp_path / "work"
    _extract(_Args(minority_scan_pdf), work)
    assert len(counted_ocr) == 1

    _extract(_Args(minority_scan_pdf, force_ocr=True), work)
    assert len(counted_ocr) == 2


def test_the_extract_record_is_written_where_the_reuse_check_reads_it(
        minority_scan_pdf, tmp_path, counted_ocr):
    """A key nobody can find is not a key."""
    work = tmp_path / "work"
    _extract(_Args(minority_scan_pdf), work)

    state = runstate.RunState(work)
    assert state.recorded("extract") is not None
    assert not state.is_stale("extract",
                              extract.ocr_inputs(_Args(minority_scan_pdf)))[0]
