"""Stage 1 — turn a book file into ``book.json`` + an ``assets/`` directory.

Handles the three real-world shapes a book arrives in:

* **born-digital PDF** — read straight through PyMuPDF;
* **scanned PDF** — no text layer at all, every page is a picture;
* **mixed PDF** — the common case for older titles: some pages carry text,
  some are scans, and re-OCRing the good pages would only make them worse.

Scan detection is per page, and OCR is routed accordingly: ``--skip-text``
leaves intact pages alone, ``--force-ocr`` is only used when the whole book is
a scan. ``--optimize 0 --output-type pdf`` keeps OCRmyPDF from recompressing
the illustrations, which is the whole reason the images survive at full
quality into the Word file.

When OCRmyPDF is not enough (dense layout, illustrations that are not separate
PDF image objects), a MinerU or Markdown side-door imports someone else's
better extraction instead of pretending to reimplement it.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

import bookir as ir
import runstate

# The side doors live in their own module now. Re-exported here because
# `extract` is the stage everything calls and the tests import these names from
# it: a move should not also be a rename. The dependency points one way only —
# `adapters` knows nothing about detection, OCR routing or this stage's
# arguments, so importing it here cannot cycle.
from adapters import (  # noqa: F401
    ExtractError,
    _mineru_bbox,
    from_markdown,
    from_mineru,
    merge_mineru_figures,
)
# Its home is `rasters`, but `extract` has always exposed it and the OCR tier
# imports it from here. That tier skips wherever Tesseract is absent, so this
# re-export disappearing broke nothing any developer machine would notice — the
# `nothing skipped` CI job caught it, which is the hole that job exists to close.
from rasters import crop_from_source  # noqa: F401

#: A page with fewer characters than this has no usable text layer.
PAGE_TEXT_THRESHOLD = 80
#: Fraction of pages that must carry text before a PDF counts as born-digital.
DIGITAL_PAGE_SHARE = 0.92
#: Wall-clock ceiling for one OCRmyPDF run (a 400-page scan is slow but finite).
OCR_TIMEOUT_SECONDS = 5400


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #

def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".pdf", ".epub", ".docx"}:
        return suffix[1:]
    with open(path, "rb") as handle:
        magic = handle.read(4)
    if magic[:4] == b"%PDF":
        return "pdf"
    if magic[:2] == b"PK":
        return "epub"  # could be docx; the reader will complain clearly
    raise ExtractError(
        f"cannot tell what {path.name} is — supported inputs are .pdf, .epub, .docx"
    )


def probe_pdf(path: Path) -> dict[str, Any]:
    """Per-page text census, used to decide whether and how to OCR."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - exercised only on old installs
        import fitz as pymupdf  # type: ignore

    doc = pymupdf.open(path)
    try:
        counts = [len(page.get_text("text").strip()) for page in doc]
        image_counts = [len(page.get_images(full=True)) for page in doc]
    finally:
        doc.close()

    pages = len(counts)
    if pages == 0:
        raise ExtractError(f"{path.name} has no pages")

    with_text = [i + 1 for i, n in enumerate(counts) if n >= PAGE_TEXT_THRESHOLD]
    without_text = [i + 1 for i, n in enumerate(counts) if n < PAGE_TEXT_THRESHOLD]
    share = len(with_text) / pages

    if share >= DIGITAL_PAGE_SHARE:
        kind = "digital"
    elif not with_text:
        kind = "scanned"
    else:
        kind = "mixed"

    return {
        "pages": pages,
        "kind": kind,
        "pages_with_text": len(with_text),
        "pages_without_text": without_text[:60],
        "text_share": round(share, 3),
        "total_images": sum(image_counts),
        "median_chars_per_page": sorted(counts)[pages // 2],
    }


# --------------------------------------------------------------------------- #
# OCR
# --------------------------------------------------------------------------- #

def find_ocrmypdf() -> list[str] | None:
    """How to launch OCRmyPDF, or ``None`` if it is not available.

    Prefers the executable on PATH, then falls back to running it as a module
    in *this* interpreter — which is the normal case when the skill's
    dependencies live in a virtual environment that is not on PATH.
    """
    binary = shutil.which("ocrmypdf")
    if binary:
        return [binary]
    try:
        import ocrmypdf  # noqa: F401
    except ImportError:
        return None
    return [sys.executable, "-m", "ocrmypdf"]


#: Where an ordinary install leaves a tool *without* putting it on PATH.
#: `<drive>` expands to each fixed drive, because a large optional tool is
#: routinely installed off the system drive — the MinerU this was measured
#: against lives on `G:`.
#:
#: Same shape and the same measured reason as `wordrender.BUNDLED_LIBREOFFICE`:
#: `shutil.which` alone reported MinerU and Tesseract missing on a machine that
#: had both installed, so `doctor` printed install instructions for software the
#: reader already had and the OCR tier skipped itself. A tool that is present but
#: not on PATH is the ordinary case on Windows, not the exception.
#:
#: It lives here rather than in the dispatcher because `extract` is the stage
#: that drives these tools and already owns `find_ocrmypdf` — and because the
#: OCR tests need the same answer `doctor` gives. Two places deciding separately
#: is how they came to disagree.
BUNDLED_TOOLS = {
    "tesseract": (
        r"<drive>\Program Files\Tesseract-OCR\tesseract.exe",
        r"<drive>\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ),
    "ghostscript": (
        r"<drive>\Program Files\gs\gs*\bin\gswin64c.exe",
        r"<drive>\Program Files\gs\gs*\bin\gswin32c.exe",
    ),
    "mineru": (
        r"<drive>\Program Files\MinerU\venv\Scripts\mineru.exe",
        r"<drive>\MinerU\venv\Scripts\mineru.exe",
    ),
}


def _drives() -> list[str]:
    """Every fixed drive root, or nothing off Windows so the patterns no-op."""
    if os.name != "nt":
        return []
    listed = getattr(os, "listdrives", None)          # 3.12+
    if listed is not None:
        try:
            return [drive.rstrip("\\/") for drive in listed()]
        except OSError:
            pass
    return [f"{letter}:" for letter in string.ascii_uppercase
            if os.path.isdir(f"{letter}:\\")]


def find_tool(names: list[str], label: str = "") -> str | None:
    """The tool's path: on PATH first, then where an installer leaves it."""
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for pattern in BUNDLED_TOOLS.get(label, ()):
        for drive in _drives():
            # Reversed, so a versioned directory yields the newest first —
            # `gs10.07.1` before `gs10.02.0`.
            for match in sorted(glob.glob(pattern.replace("<drive>", drive)),
                                reverse=True):
                if os.path.isfile(match):
                    return match
    return None


def ocr_command(
    launcher: list[str],
    source: Path,
    destination: Path,
    *,
    kind: str,
    language: str = "eng",
    deskew: bool | None = None,
) -> list[str]:
    """Build the OCRmyPDF argv for a book of this ``kind``.

    Separated from execution so the routing decisions — which are the part with
    real consequences — can be tested without the binary installed.
    """
    command = [
        *launcher,
        "--language", language,
        "--rotate-pages",
        # Keep the book's own pictures byte-identical: no re-encoding, no PDF/A
        # rewrite. Image fidelity matters more here than archival conformance.
        "--optimize", "0",
        "--output-type", "pdf",
    ]
    # Deskewing rewrites the page raster, so it is only worth it when every
    # page is a scan anyway. On a mixed book it would damage the good pages.
    if deskew if deskew is not None else (kind == "scanned"):
        command.append("--deskew")
    # --skip-text leaves pages that already carry text untouched; re-recognising
    # them would replace accurate characters with guessed ones.
    command.append("--force-ocr" if kind == "scanned" else "--skip-text")
    return command + [str(source), str(destination)]


def _usable_ocr_output(destination: Path) -> tuple[bool, str]:
    """Can this OCR result actually be read, and did it gain any text?"""
    if not destination.exists() or destination.stat().st_size == 0:
        return False, "no output file was written"
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        import fitz as pymupdf  # type: ignore
    try:
        doc = pymupdf.open(destination)
    except Exception as error:
        return False, f"the output PDF cannot be opened: {error}"
    try:
        pages = len(doc)
        if pages == 0:
            return False, "the output PDF has no pages"
        characters = sum(len(page.get_text("text").strip()) for page in doc)
    finally:
        doc.close()
    if characters == 0:
        return False, "the output PDF has no text layer at all"
    return True, f"{characters} characters across {pages} pages"


def run_ocr(
    source: Path,
    destination: Path,
    *,
    kind: str,
    language: str = "eng",
    deskew: bool | None = None,
    timeout: int = OCR_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Add a text layer with OCRmyPDF, preserving the original rasters."""
    launcher = find_ocrmypdf()
    if not launcher:
        raise ExtractError(
            "this PDF needs OCR, but OCRmyPDF is not available.\n"
            "  1. pip install ocrmypdf\n"
            "  2. Tesseract:   winget install tesseract-ocr.tesseract\n"
            "     (macOS: brew install tesseract · Debian: apt install tesseract-ocr)\n"
            "  3. Ghostscript: https://ghostscript.com/releases/gsdnld.html\n"
            "     Not in winget; on macOS `brew install ghostscript`, on Debian\n"
            "     `apt install ghostscript`. Make sure its bin/ is on PATH.\n"
            "  Then run `revayat-novel.py doctor` to confirm all three are found.\n"
            "  Or re-run with --ocr off to extract only the pages that already\n"
            "  have a text layer."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    command = ocr_command(launcher, source, destination, kind=kind,
                          language=language, deskew=deskew)

    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ExtractError(
            f"ocrmypdf exceeded {timeout}s. Split the PDF, or raise --ocr-timeout."
        ) from None

    # Judge the artefact, not the exit code. OCRmyPDF reports non-zero for
    # conditions that still leave a perfectly usable file — exit 4 means qpdf
    # disliked the structure, which a book exported by some tools inherits from
    # its own source. What matters is whether the PDF opens and gained text.
    usable, detail = _usable_ocr_output(destination)
    if not usable:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-8:]
        raise ExtractError(
            "ocrmypdf failed (exit %s): %s\n  %s"
            % (completed.returncode, detail, "\n  ".join(tail))
        )

    warning = None
    if completed.returncode not in (0, 2):
        warning = (
            f"ocrmypdf exited {completed.returncode} but produced a readable PDF "
            f"({detail}); continuing. Inspect the output if the text looks wrong."
        )

    return {
        "warning": warning,
        "launcher": " ".join(launcher),
        "mode": "force-ocr" if kind == "scanned" else "skip-text",
        "exit": completed.returncode,
        "output": str(destination),
    }


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def extract(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out)
    asset_dir = out_dir / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}

    if args.figures_from_mineru:
        # Operates on the book that is already there: extraction and OCR have
        # run, and this only swaps whole-page scans for the real figures.
        book_path = out_dir / "book.json"
        if not book_path.exists():
            raise ExtractError(
                f"no book.json in {out_dir} — run extract on the PDF first, then "
                f"re-run with --figures-from-mineru"
            )
        book = ir.load_book(book_path)
        # The *original* file, deliberately: the watermark cleaner and OCR both
        # rewrite page rasters, and a plate cut from the original keeps the
        # pixels the book was scanned at rather than the ones a preprocessing
        # step left behind.
        report["figures"] = merge_mineru_figures(
            book, Path(args.figures_from_mineru), asset_dir,
            page_offset=args.figures_page_offset,
            source_pdf=Path(args.input) if args.input else None,
        )
    elif args.from_mineru:
        book = from_mineru(
            Path(args.from_mineru), asset_dir,
            source_name=Path(args.input).stem if args.input else "book",
            lang_source=args.source_lang, lang_target=args.target_lang,
        )
    elif args.from_markdown:
        book = from_markdown(
            Path(args.from_markdown), asset_dir,
            lang_source=args.source_lang, lang_target=args.target_lang,
        )
    else:
        book = _extract_native(args, out_dir, asset_dir, report)

    problems = ir.validate_book(book)
    if problems:
        raise ExtractError("extraction produced an invalid book:\n  " + "\n  ".join(problems))

    book_path = out_dir / "book.json"
    ir.save_book(book, book_path)

    # What this extraction was run against, so a later stage can tell whether
    # the book it is reading came from the file and settings it thinks it did.
    runstate.RunState(out_dir).record("extract", {
        "source": runstate.file_hash(args.input) if args.input else "",
        "ocr_lang": str(getattr(args, "ocr_lang", "")),
        "ocr": str(getattr(args, "ocr", "")),
        "clean_scan": str(getattr(args, "clean_scan", "")),
    }, {"book": runstate.source_digest(book)})

    report.update({
        "book": str(book_path),
        "assets": str(asset_dir),
        "stats": book["stats"],
        "title": book["meta"]["title"],
        "author": book["meta"]["author"],
    })
    return report


def _extract_native(args, out_dir: Path, asset_dir: Path,
                    report: dict[str, Any]) -> dict[str, Any]:
    if not args.input:
        raise ExtractError("no input file given")
    source = Path(args.input)
    if not source.exists():
        raise ExtractError(f"input not found: {source}")

    kind = detect_format(source)
    report["format"] = kind

    if kind == "epub":
        from read_epub import read_epub
        return read_epub(str(source), asset_dir,
                         lang_source=args.source_lang, lang_target=args.target_lang)
    if kind == "docx":
        from read_docx import read_docx
        return read_docx(str(source), asset_dir,
                         lang_source=args.source_lang, lang_target=args.target_lang)

    probe = probe_pdf(source)
    report["probe"] = probe
    read_from = source
    #: True only once the text actually being read came out of an OCR pass.
    #: Deriving this from ``report["ocr"]`` was wrong: the *skipped* branch
    #: writes there too, so `--ocr off` claimed `from_ocr` in the book's own
    #: provenance and put the extractor on OCR's loose size tolerances over a
    #: perfectly good digital text layer.
    from_ocr = False

    # Strip a colour watermark before OCR: a stamp across a line of text costs
    # recognition accuracy, and the cleaned raster is what OCR should read.
    if args.clean_scan != "off" and probe["kind"] != "digital":
        import scan_clean
        try:
            cleaned_pdf = out_dir / "cleaned.pdf"
            if cleaned_pdf.exists() and not args.force_ocr:
                report["clean_scan"] = {"reused": str(cleaned_pdf)}
            else:
                report["clean_scan"] = scan_clean.clean_pdf(
                    read_from, cleaned_pdf,
                    force=args.clean_scan == "force",
                    ghost_threshold=args.ghost_threshold,
                )
            if (report["clean_scan"].get("cleaned")
                    or report["clean_scan"].get("reused")):
                read_from = cleaned_pdf
        except scan_clean.Unavailable as error:
            report["clean_scan"] = {"skipped": str(error)}

    if probe["kind"] != "digital" and args.ocr != "off":
        ocr_pdf = out_dir / "ocr.pdf"
        if ocr_pdf.exists() and not args.force_ocr:
            report["ocr"] = {"reused": str(ocr_pdf)}
        else:
            report["ocr"] = run_ocr(
                read_from, ocr_pdf, kind=probe["kind"], language=args.ocr_lang,
                deskew=args.deskew, timeout=args.ocr_timeout,
            )
            report["ocr"]["probe_after"] = probe_pdf(ocr_pdf)
        read_from = ocr_pdf
        from_ocr = True
    elif probe["kind"] != "digital":
        report["ocr"] = {"skipped": "--ocr off", "warning":
                         f"{len(probe['pages_without_text'])}+ pages have no text layer"}

    from read_pdf import read_pdf
    book = read_pdf(str(read_from), asset_dir,
                    lang_source=args.source_lang, lang_target=args.target_lang,
                    max_pages=args.max_pages,
                    ocr_text=from_ocr)
    book["source"]["original_path"] = str(source)
    book["source"]["probe"] = probe
    return book


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("input", nargs="?", help="book file (.pdf, .epub, .docx)")
    parser.add_argument("--out", required=True, help="working directory for this run")
    parser.add_argument("--source-lang", default="en")
    parser.add_argument("--target-lang", default="fa-IR")
    parser.add_argument("--ocr", choices=["auto", "off"], default="auto",
                        help="auto (default) OCRs scanned/mixed PDFs; off never does")
    parser.add_argument("--ocr-lang", default="eng", help="Tesseract language code")
    parser.add_argument("--ocr-timeout", type=int, default=OCR_TIMEOUT_SECONDS)
    parser.add_argument("--force-ocr", action="store_true",
                        help="re-run OCR even if ocr.pdf already exists")
    parser.add_argument("--deskew", action=argparse.BooleanOptionalAction, default=None,
                        help="override deskew (default: on for fully scanned books only)")
    parser.add_argument("--max-pages", type=int, default=None, help="stop after N pages")
    parser.add_argument("--clean-scan", choices=["auto", "off", "force"], default="auto",
                        help="remove a colour watermark from scanned pages before OCR")
    parser.add_argument("--ghost-threshold", type=int, default=None, metavar="N",
                        help="also whiten grey pixels lighter than N (0-255). Clears the "
                             "grey remnant a translucent watermark leaves, but damages "
                             "glyphs it overlapped. Off by default.")
    parser.add_argument("--from-mineru", metavar="DIR",
                        help="import a MinerU output directory instead of parsing")
    parser.add_argument("--from-markdown", metavar="FILE",
                        help="import Markdown from Marker/Docling instead of parsing")
    parser.add_argument("--figures-from-mineru", metavar="DIR",
                        help="merge the figures MinerU cropped out of scanned pages "
                             "into the book already extracted in --out. Takes only "
                             "the pictures; the text keeps coming from the OCR pass")
    parser.add_argument("--figures-page-offset", type=int, default=0, metavar="N",
                        help="add N to MinerU's page numbers, for when it was run "
                             "over a page range rather than the whole book")


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel extract", description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)
    try:
        report = extract(args)
    except ExtractError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
