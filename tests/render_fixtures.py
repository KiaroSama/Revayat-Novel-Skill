"""Synthetic measurement views, PDFs and native documents for render contracts."""

from __future__ import annotations

from pathlib import Path
import pytest
import bookir as ir

PERSIAN = "صبح به آرامی از فراز تپه‌ها بالا آمد و الیزابت کنار پنجره ایستاده بود."
PERSIAN_OTHER = "دارسی هیچ نگفت و او رویش را از پنجره برگرداند و به راه افتاد."
SETUP = ir.default_page_setup()
BODY_RIGHT = SETUP["width_pt"] - SETUP["margin_outer_pt"]


def _text(text: str, box: list[float]) -> dict:
    return {"text": text, "bbox": [float(v) for v in box]}


def _image(box: list[float]) -> dict:
    return {"bbox": [float(v) for v in box],
            "width_pt": float(box[2] - box[0]),
            "height_pt": float(box[3] - box[1])}


def _view(*, blocks=(), images=(), width=None, height=None) -> dict:
    return {
        "width_pt": SETUP["width_pt"] if width is None else width,
        "height_pt": SETUP["height_pt"] if height is None else height,
        "blocks": list(blocks),
        "images": list(images),
    }


def _expected(*, texts=(), images=(), page: int = 1, translatable=None) -> dict:
    return {
        "page": page,
        "setup": dict(SETUP),
        "texts": list(texts),
        "images": [{"id": f"b{n:05d}", "aspect": aspect}
                   for n, aspect in enumerate(images, start=1)],
        "translatable": len(texts) if translatable is None else translatable,
    }


def _codes(report) -> set[str]:
    return {finding["code"] for finding in report.summary()["findings"]}


def _well_set_page() -> dict:
    """Two right-anchored Persian paragraphs, inside the body, no pictures."""
    return _view(blocks=[
        _text(PERSIAN, [60, 100, BODY_RIGHT, 140]),
        _text(PERSIAN_OTHER, [60, 160, BODY_RIGHT, 200]),
    ])


def _paged_book(tmp_path: Path) -> Path:
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, text="The first page's paragraph.",
                      target=PERSIAN),
        ir.make_block("pagebreak", 2, page=2, soft=True),
        ir.make_block("image", 3, page=2, asset="plate.png", alt="",
                      width_pt=180.0, height_pt=120.0),
        ir.make_block("paragraph", 4, page=2, text="The second page's paragraph.",
                      target=PERSIAN_OTHER),
    ]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


def _pdf_page(path: Path, lines: list[tuple[str, float, float]], *,
              width: float = SETUP["width_pt"],
              height: float = SETUP["height_pt"]) -> Path:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    for text, x, y in lines:
        page.insert_text((x, y), text, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def _latin_book(tmp_path: Path, target: str) -> Path:
    book = ir.new_book()
    book["blocks"] = [
        ir.make_block("paragraph", 1, page=1, text="A line of source prose.",
                      target=target)
    ]
    path = tmp_path / "book.json"
    ir.save_book(book, path)
    return path


@pytest.fixture
def sample_book_and_docx(tmp_path):
    """A saved book and the document built from it — what a caller really has."""
    import argparse

    from build_docx import Builder, add_arguments

    book = ir.new_book(lang_source="en", lang_target="fa-IR")
    block = ir.make_block("paragraph", 1, page=1, bbox=[72, 90, 320, 140],
                          text="A paragraph on the only page of this book.")
    block["target"] = "بندی فارسی که به اندازهٔ کافی بلند است تا از گیت‌ها رد شود."
    book["blocks"] = [block]

    book_path = tmp_path / "book.json"
    ir.save_book(book, book_path)

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = parser.parse_args(["--book", "x", "--out", "y", "--font", "Tahoma",
                                 "--no-toc"])
    docx_path = tmp_path / "book.fa.docx"
    Builder(book, tmp_path, options).build(docx_path)
    return book_path, docx_path


def _two_sheet_pdf(path: Path, first: str, second: str) -> Path:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for text in (first, second):
        page = doc.new_page(width=SETUP["width_pt"], height=SETUP["height_pt"])
        page.insert_text((100, 150), text, fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return path


def _hostile_pdf(path, inches: float = 200.0):
    """A legal one-page PDF whose page is `inches` square. Tiny on disk."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page(width=inches * 72, height=inches * 72)
    page.insert_text((72, 72), "a page this size is not a book page", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


def _five_documents(tmp_path, count=3):
    """Small real documents, built the way the preview path builds them."""
    import build_docx
    import preview

    book = ir.new_book(title="t", source_format="text", pages=1)
    book["blocks"] = [ir.make_block("paragraph", n, page=1, text="x",
                                    target=PERSIAN)
                      for n in range(1, 4)]
    made = []
    out = tmp_path / "docs"
    out.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        dest = out / f"p{index:02d}.docx"
        build_docx.Builder(book, tmp_path / "assets",
                           preview.production_options()).build(dest)
        made.append(dest)
    return made
