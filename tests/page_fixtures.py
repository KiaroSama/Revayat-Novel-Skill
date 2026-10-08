"""Shared page-run books, source PDFs and lifecycle evidence."""

from __future__ import annotations

from pathlib import Path
import pytest
import bookir as ir
import pagerun
import review
import runstate

TARGET = "Sample rendered line that must appear on the page."
SETUP = ir.default_page_setup()


def _book(items: list[tuple[int, str, dict]]) -> dict:
    """``items`` are ``(page, block type, fields)`` in reading order."""
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block(block_type, index, page=page, **fields)
        for index, (page, block_type, fields) in enumerate(items, start=1)
    ]
    return book


def _prose(page: int, marker: str, sentences: int = 3) -> tuple[int, str, dict]:
    text = " ".join(f"{marker} sentence {n} of ordinary prose." for n in range(sentences))
    return (page, "paragraph", {"text": text, "bbox": [54, 100, 340, 200]})


def _save(book: dict, tmp_path: Path) -> Path:
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _built(book: dict, tmp_path: Path, **options) -> tuple[dict, Path]:
    manifest = pagerun.build(_save(book, tmp_path), tmp_path / "pages", **options)
    return manifest, tmp_path / "pages"


def _job(manifest: dict, page: int) -> dict:
    return next(entry for entry in manifest["chunks"] if entry["page"] == page)


def _worksheet(pages: Path, page: int) -> str:
    return (pages / f"page{page:04d}.md").read_text(encoding="utf-8")


def _translate_section(text: str) -> str:
    """Only the part of a worksheet the translator is told to answer."""
    marker = "## Translate"
    assert marker in text
    return text[text.index(marker):]


def _mark(tmp_path: Path, page: int, state: str, **kwargs) -> None:
    runstate.RunState(tmp_path).set_page(page, state, **({"hashes": {"translation": pagerun.translation_hash(tmp_path / "book.json", page)}, **kwargs} if state == "accepted" else kwargs))


def _odd_pdf(path: Path, png: Path) -> Path:
    """Three pages nothing about is standard: a rotation, two odd trims, a
    picture. The point of splitting by copy rather than by camera is that all
    of it survives."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()

    first = doc.new_page(width=396, height=612)          # not A4
    first.insert_text((40, 60), "Page one of the source.", fontsize=11)
    first.insert_image(pymupdf.Rect(40, 100, 220, 220), filename=str(png))

    second = doc.new_page(width=842, height=595)         # landscape, then turned
    second.insert_text((40, 60), "Page two of the source.", fontsize=11)
    second.set_rotation(90)

    third = doc.new_page(width=200, height=800)          # a tall narrow trim
    third.insert_text((20, 60), "Page three of the source.", fontsize=11)

    doc.save(str(path))
    doc.close()
    return path


def _book_from_pdf(pdf: Path, pages: int) -> dict:
    """A book that says, as an extraction does, which PDF it was read from."""
    book = ir.new_book(source_path=str(pdf), source_format="pdf",
                       source_sha256=ir.sha256_file(pdf), pages=pages)
    book["blocks"] = [
        ir.make_block("paragraph", number, page=number,
                      text=f"Page {number} of the source.")
        for number in range(1, pages + 1)
    ]
    return book


def _one_page_book(tmp_path: Path) -> Path:
    book = ir.new_book()
    book["blocks"] = [ir.make_block("paragraph", 1, page=1,
                                    text="A line of source prose.")]
    return _save(book, tmp_path)


def _rendered(path: Path, text: str) -> Path:
    """The built document, laid out — what render QA is handed to look at."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page(width=SETUP["width_pt"], height=SETUP["height_pt"])
    page.insert_text((100, 150), text, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def _reviewed(work_dir, page: int = 1) -> dict:
    """File a clean reviewer verdict, the way a reviewer with eyes would.

    Every question answered explicitly: `review.record` refuses a partial
    answer sheet, so a test cannot accidentally pass a page by leaving one out.
    """
    filed = review.record(work_dir, page,
                          dict.fromkeys(review.QUESTIONS, True))
    assert filed["ok"], filed
    return filed


def _mixed_pdf(path: Path) -> Path:
    """Three pages, three shapes: portrait, landscape, and a wider trim.

    A real book has these — a map, a plate, a differently trimmed front matter
    leaf — and every one of them is a page the run has to lay out on its own
    paper rather than on the book's average.
    """
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for width, height, text in ((396, 612, "The portrait page of ordinary prose."),
                                (612, 396, "The landscape plate and its caption."),
                                (468, 612, "A wider leaf, trimmed differently.")):
        page = doc.new_page(width=width, height=height)
        page.insert_text((60, 100), text, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def _translated_pdf_run(tmp_path: Path, png: Path) -> tuple[Path, Path, Path]:
    """A real PDF book, built into pages, with every page's Persian in place."""
    pdf = _odd_pdf(tmp_path / "source.pdf", png)
    book = _book_from_pdf(pdf, 3)
    for block in book["blocks"]:
        block["target"] = f"ترجمهٔ {block['id']} با طول کافی برای آزمون."
    book_path = _save(book, tmp_path)
    pagerun.build(book_path, tmp_path / "pages")
    return pdf, book_path, tmp_path / "pages"
