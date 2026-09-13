"""A rendered PDF, read back as measurements — and nothing about what is correct.

Every check in this project that looks at a laid-out page starts here: open the
PDF, get one view per page (its size, its text blocks with boxes, its drawn
objects, the fonts it really used), and optionally a PNG of it for a person to
look at. `pagecheck` decides whether those measurements are acceptable;
this module only produces them.

The split is a real boundary rather than a line count. Everything here talks to
PyMuPDF and the filesystem, so it is the part that needs a renderer installed,
the part that fails with `ImportError` on a machine without one, and the part
whose numbers change when the PDF library is upgraded — which is exactly why
`constraints-ci.txt` pins it. Nothing here knows a tolerance, a severity or a
book; `pagecheck` imports this, and this imports nothing of the project's.

**What this deliberately cannot tell you** is whether the Persian text is
present and whether a paragraph is right-to-left. PyMuPDF drops the zero-width
non-joiner, transposes Arabic-script letters, and does not report alignment in
its block boxes — measured on a document whose every paragraph carries `w:bidi`,
it called all of them left-to-right. Both questions are asked of the built
document's own XML instead; see `pagedocx`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bookir as ir

#: Raster resolution for the PNGs a person looks at. High enough to read Persian
#: at body size, low enough that a 300-page book does not fill a disk.
DEFAULT_DPI = 110


# --------------------------------------------------------------------------- #
# Opening and measuring
# --------------------------------------------------------------------------- #

def _pymupdf():
    """PyMuPDF, or ``None``.

    Imported here rather than at module scope so that a machine without it
    reports a page as *unverified* instead of failing to import the checker at
    all — "we could not look" and "we looked and it was fine" must never be the
    same answer.
    """
    try:
        import pymupdf  # noqa: PLC0415  (deliberate optional import)
        return pymupdf
    except ImportError:
        try:
            import fitz  # noqa: PLC0415
            return fitz
        except ImportError:
            return None


def _basefont(name: str) -> str:
    """``"BCDEEE+Calibri"`` -> ``"Calibri"``. PDF subset tags are per-file noise."""
    return str(name).split("+", 1)[-1].strip()


def _view_of_open(document: Any, index: int, pdf_path: Path) -> dict[str, Any]:
    """`page_view`'s measuring half, on a document somebody else opened.

    Split out so a caller with every page to measure opens the file once.
    `page_view` keeps its contract exactly: it raises on an unopenable file and
    on an out-of-range index.
    """
    if not 0 <= index < len(document):
        raise IndexError(f"{pdf_path} has {len(document)} pages; "
                         f"wanted index {index}")
    page = document[index]
    blocks = [
        {"text": item[4], "bbox": [round(float(v), 2) for v in item[:4]]}
        for item in page.get_text("blocks")
        if len(item) > 6 and item[6] == 0 and str(item[4]).strip()
    ]
    images = []
    for info in page.get_images(full=True):
        for rect in page.get_image_rects(info[0]):
            images.append({
                "bbox": [round(float(v), 2) for v in rect],
                "width_pt": round(float(rect.width), 2),
                "height_pt": round(float(rect.height), 2),
            })
    # Which fonts the page was *actually* set in. Measured: a book built
    # with `--font Vazirmatn` came back set in Calibri on a machine that
    # has Vazirmatn installed - the installed build is a variable font and
    # Word will not resolve one for `w:cs`, so it fell back to the theme's
    # minorBidi without a word. Every other check here passed, because
    # every other check measures a layout that is not the one the reader
    # gets - and a fallback's metrics differ, so those findings describe a
    # page nobody will see.
    fonts = sorted({_basefont(span["font"])
                    for block in page.get_text("dict")["blocks"]
                    for line in block.get("lines", ())
                    for span in line.get("spans", ())
                    if span.get("text", "").strip()})
    return {
        "width_pt": round(float(page.rect.width), 2),
        "height_pt": round(float(page.rect.height), 2),
        "blocks": blocks,
        "images": images,
        "fonts": fonts,
    }


def page_view(pdf_path: Path, index: int) -> dict[str, Any]:
    """One PDF page reduced to the geometry the checks compare.

    ``{"width_pt", "height_pt", "blocks": [{"text", "bbox"}],
       "images": [{"bbox", "width_pt", "height_pt"}], "fonts": [str]}``
    """
    pymupdf = _pymupdf()
    if pymupdf is None:
        raise RuntimeError("PyMuPDF is not installed")
    document = pymupdf.open(str(pdf_path))
    try:
        return _view_of_open(document, index, Path(pdf_path))
    finally:
        document.close()


def _png_of_open(document: Any, index: int, out_path: Path,
                 dpi: int) -> Path | None:
    """`render_png`'s rasterising half. Returns ``None`` rather than raising."""
    if not 0 <= index < len(document):
        return None
    page = document[index]
    try:
        # A page declares its own size and PyMuPDF renders whatever it is
        # told; a legal 200-inch page at this dpi is gigabytes. Refused from
        # the declared size, before any pixel exists — and reported the way
        # every other "could not render" is here: no artefact, unverified.
        ir.check_render_area(page.rect.width, page.rect.height, dpi)
    except ir.RenderTooLarge:
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(dpi=dpi).save(str(out_path))
    return out_path


def render_png(pdf_path: Path, index: int, out_path: Path,
               dpi: int = DEFAULT_DPI) -> Path | None:
    """Rasterise one PDF page, or ``None`` when it cannot be rendered.

    Producing evidence must never be the thing that stops a page being
    reported on: a missing or damaged file leaves the artifact unwritten and
    the report says the page is unverified.
    """
    pymupdf = _pymupdf()
    if pymupdf is None or not Path(pdf_path).exists():
        return None
    try:
        document = pymupdf.open(str(pdf_path))
    except Exception:  # PyMuPDF raises its own hierarchy for a damaged file
        return None
    try:
        return _png_of_open(document, index, out_path, dpi)
    finally:
        document.close()


def views_and_pngs(pdf_path: Path, out_paths: list[Path], *,
                   dpi: int = DEFAULT_DPI
                   ) -> tuple[list[dict[str, Any]], list[Path | None]]:
    """Every page measured and rasterised, opening the document once.

    `page_view` and `render_png` each open the file, read one page and close it,
    which is right for one page and wrong for a book: `docqa.check_document`
    calls both per page, so a 300-page render opened the same file 600 times.
    Measured on a generated 300-page book, 30 lines a page: `views_of` 7.81s
    against 2.27s for one open — 3.4x, paid twice per `doc-qa check`, and
    `doc-qa check` runs at least twice per book by the documented workflow.

    The same shape, and the same fix, as `sourcepages.page_fingerprints`.

    Each failure contract is preserved: a measurement raises, an unwritable PNG
    is ``None`` in its slot. ``out_paths`` should have one entry per page; a
    shorter list simply leaves the rest unrendered.
    """
    pymupdf = _pymupdf()
    if pymupdf is None:
        raise RuntimeError("PyMuPDF is not installed")
    document = pymupdf.open(str(pdf_path))
    try:
        views = [_view_of_open(document, index, Path(pdf_path))
                 for index in range(len(document))]
        pngs = [_png_of_open(document, index, out_paths[index], dpi)
                if index < len(out_paths) else None
                for index in range(len(document))]
        return views, pngs
    finally:
        document.close()


# --------------------------------------------------------------------------- #
# Whole documents
# --------------------------------------------------------------------------- #

def views_of(pdf_path: Path) -> list[dict[str, Any]]:
    """Every page of a preview, read back. Empty when it cannot be opened."""
    pymupdf = _pymupdf()
    if pymupdf is None:
        return []
    try:
        document = pymupdf.open(str(pdf_path))
    except Exception:
        return []
    try:
        return [_view_of_open(document, index, Path(pdf_path))
                for index in range(len(document))]
    finally:
        document.close()


def page_count(pdf_path: Path) -> int:
    """How many pages, or ``0`` when the file cannot be opened at all.

    Deliberately not an exception: a caller counting pages so it can rasterise
    them has its own "we could not look" path, and a raise from here jumps past
    it and turns a page that could not be rendered into a crashed run.
    """
    pymupdf = _pymupdf()
    if pymupdf is None:
        return 0
    try:
        document = pymupdf.open(str(pdf_path))
    except Exception:
        return 0
    try:
        return len(document)
    finally:
        document.close()





def combine(views: list[dict[str, Any]]) -> dict[str, Any]:
    """Every page of a preview as one surface, in reading order.

    Each sheet's boxes are pushed down by the sheets above it. Nothing measures
    against those coordinates - the geometry checks read the per-page views -
    but `_check_images` orders illustrations by where they sit, and without the
    offset a picture at the top of sheet two would sort ahead of one halfway
    down sheet one. That is a reordering finding on a page that is in order.
    """
    blocks: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    offset = 0.0
    for view in views:
        for source, sink in ((view["blocks"], blocks), (view["images"], images)):
            for item in source:
                box = item["bbox"]
                sink.append({**item, "bbox": [box[0], box[1] + offset,
                                              box[2], box[3] + offset]})
        offset += float(view["height_pt"] or 0.0)
    return {"blocks": blocks, "images": images,
            "width_pt": views[0]["width_pt"], "height_pt": views[0]["height_pt"]}

