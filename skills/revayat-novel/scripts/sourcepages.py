"""The source PDF as an artefact: what a page looks like, and where it lives.

`pagerun` decides what a page *is* — who owns which block, what it is worth
sending to a translator, when it has earned `accepted`. This module answers the
questions underneath that, all of them about the file rather than the job: what
does page 7 look like in the source, reproducibly; which of the original, the
cleaned copy and the OCR copy the book was actually read from; and how to cut
one real PDF per page without stepping on files somebody else wrote.

Split out of `pagerun.py` at 1064 lines, on the same kind of boundary
`pagecli.py` was: a different question, not a line count. Nothing here knows
about worksheets, budgets, gates or run state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import bookir as ir

#: The single-page source PDFs, under the worksheet directory.
SOURCE_DIR = "source"


class SourceCollision(RuntimeError):
    """Files this build did not write are sitting in the page-PDF directory."""


#: Every page box PDF defines, in a fixed order. `page.rect` is not enough and
#: not a substitute: it is the crop box *normalised to the origin*, so a crop
#: box moved sideways across the media reports the identical rect. Measured -
#: two files with one content stream, one 400x600 rect and crop boxes at x=0
#: and x=50 rendered different pixels and hashed the same.
PAGE_BOXES = ("mediabox", "cropbox", "trimbox", "bleedbox", "artbox")


def _visible_page_parts(document: Any, sheet: Any) -> list[bytes]:
    """What is drawn on one page, in the order a reader would see it change."""
    parts = [f"@{getattr(sheet, 'rotation', 0) or 0}".encode("utf-8")]
    for name in PAGE_BOXES:
        box = getattr(sheet, name, None)
        # A file that declares no trim box is a different state from one that
        # declares a trim box equal to the media box, and both are recorded.
        shape = "absent" if box is None else \
            f"{box.x0:.2f},{box.y0:.2f},{box.x1:.2f},{box.y1:.2f}"
        parts.append(f"{name}={shape}".encode("utf-8"))

    for xref in sheet.get_contents():
        parts.append(document.xref_stream(xref) or b"")

    # Sorted by xref so the order is the file's, not the traversal's.
    for image in sorted(sheet.get_images(full=True)):
        try:
            parts.append(document.extract_image(image[0])["image"])
        except Exception:
            # An image we cannot extract still took part in the page; a marker
            # keeps it in the identity rather than silently out.
            parts.append(b"unreadable-image")

    # Annotations are not in the content stream. A highlight added over a
    # paragraph changes the rendered page and nothing above it - measured, same
    # stream hash, different pixels - so the object and its appearance join too.
    for annotation in sheet.annots() or ():
        xref = getattr(annotation, "xref", 0)
        try:
            parts.append((document.xref_object(xref) or "").encode("utf-8"))
        except Exception:
            parts.append(b"unreadable-annotation")
        try:
            kind, value = document.xref_get_key(xref, "AP/N")
            if kind == "xref":
                appearance = int(str(value).split()[0])
                parts.append(document.xref_stream(appearance) or b"")
        except Exception:
            parts.append(b"unreadable-appearance")
    return parts


def page_fingerprints(pdf_path: Path, pages: Iterable[int]) -> dict[int, str]:
    """Many pages' fingerprints, opening the document once.

    `page_fingerprint` opens the file per call, which is right for one page and
    wrong for a book: `build` asks for every page, so the open is paid once per
    page and the cost grows faster than the page count. Measured, per-page open
    against one open: 10 pages 5.1x, 200 pages 8.5x, 400 pages 8.9x — 3.40s
    against 0.38s at 400. Small in absolute terms and still the wrong shape,
    and the fix reads no worse than the loop it replaces.
    """
    pages = list(pages)
    try:
        import pymupdf  # noqa: PLC0415
    except ImportError:
        return {page: "" for page in pages}
    try:
        document = pymupdf.open(str(pdf_path))
    except Exception:
        return {page: "" for page in pages}
    try:
        return {
            page: (ir.sha256_bytes(b"\x1e".join(
                _visible_page_parts(document, document[page - 1])))
                if 1 <= page <= document.page_count else "")
            for page in pages
        }
    finally:
        document.close()


def page_fingerprint(pdf_path: Path, page: int) -> str:
    """What one source page *looks like*, as a reproducible identity.

    Deliberately not the hash of the split one-page PDF, which was the obvious
    choice and is wrong: PyMuPDF stamps what it writes, so splitting one
    unchanged source three times produced three different hashes. Measured.
    Keying a page on that would invalidate every page of the book on every
    rebuild - a resumable run that throws away all its progress each time, which
    is worse than the bug it was meant to catch.

    So the page's own bytes *in the source* are hashed instead: every page box
    and the rotation, its content streams, the data of every image it draws, and
    every annotation drawn over it. That changes exactly when the page changes -
    a replaced plate, a new trim, a shifted crop, a re-scan at another angle, a
    highlight someone left behind - and not otherwise.
    """
    try:
        import pymupdf  # noqa: PLC0415  (only a PDF book ever reaches here)
    except ImportError:
        return ""
    try:
        document = pymupdf.open(str(pdf_path))
    except Exception:
        return ""
    try:
        if not 1 <= page <= document.page_count:
            return ""
        return ir.sha256_bytes(
            b"\x1e".join(_visible_page_parts(document, document[page - 1])))
    finally:
        document.close()


def reference_pdf(book: dict[str, Any], book_path: Path) -> Path | None:
    """The PDF this book was actually read from, or ``None`` for a non-PDF.

    Not ``$WORK/ocr.pdf`` by convention: a born-digital book never has that
    file, and a mixed one has both an original and an OCR-normalised copy, so
    naming the artefact instead of recording it picks the wrong one about half
    the time. ``read_pdf`` writes down the file it opened — whichever of the
    three that was — and that is the only answer right for all of them.
    """
    source = book.get("source") or {}
    if source.get("format") != "pdf" or not source.get("path"):
        return None
    direct = Path(str(source["path"]))
    if direct.exists():
        return direct
    # The path is stored as it was typed and a page run is routinely resumed
    # from somewhere else, so look beside the book before giving up.
    beside = Path(book_path).parent / direct.name
    return beside if beside.exists() else None


def split_source_pages(reference: Path, pages: list[int],
                       out_dir: Path) -> dict[int, dict[str, Any]]:
    """One real PDF per source page, written beside the worksheets.

    Copied, never rasterised: ``insert_pdf`` carries the page's boxes, its
    rotation, its resources and its embedded image streams across untouched, so
    what a reviewer opens is the printed page rather than a photograph of one,
    and render QA can rasterise it later at whatever resolution it likes.
    """
    import pymupdf  # noqa: PLC0415  (only a PDF book ever reaches here)

    directory = out_dir / SOURCE_DIR
    wanted = {page: f"page-{page:04d}.pdf" for page in pages}

    # Another stage packages this tree. Writing over a file this build did not
    # produce would destroy someone's work, and leaving one here would smuggle
    # it into their package, so a stranger stops the run by name.
    if directory.exists():
        mine = set(wanted.values())
        strangers = sorted(item.name for item in directory.iterdir()
                           if item.name not in mine)
        if strangers:
            raise SourceCollision(
                f"{directory} already holds "
                f"{', '.join(strangers[:5])}{'…' if len(strangers) > 5 else ''}"
                f", which this build did not write. Move or delete them; "
                f"nothing here was overwritten."
            )
    directory.mkdir(parents=True, exist_ok=True)

    written: dict[int, dict[str, Any]] = {}
    source = pymupdf.open(str(reference))
    try:
        for page, name in wanted.items():
            # A book read with --max-pages, or one whose blocks were renumbered,
            # can name a page the PDF does not have. Skipping leaves the entry's
            # source_pdf empty, which reads as "there is none" rather than as a
            # path to a file nobody wrote.
            if not 1 <= page <= source.page_count:
                continue
            single = pymupdf.open()
            try:
                single.insert_pdf(source, from_page=page - 1, to_page=page - 1)
                single.save(str(directory / name))
            finally:
                single.close()
            written[page] = {
                "file": f"{SOURCE_DIR}/{name}",
                "source_page": page,
                "sha256": ir.sha256_file(directory / name),
            }
    finally:
        source.close()
    return written
