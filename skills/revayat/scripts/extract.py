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
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import bookir as ir

#: A page with fewer characters than this has no usable text layer.
PAGE_TEXT_THRESHOLD = 80
#: Fraction of pages that must carry text before a PDF counts as born-digital.
DIGITAL_PAGE_SHARE = 0.92
#: Wall-clock ceiling for one OCRmyPDF run (a 400-page scan is slow but finite).
OCR_TIMEOUT_SECONDS = 5400


class ExtractError(RuntimeError):
    """Actionable failure — the message names the fix, not just the symptom."""


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
            "  Then run `revayat.py doctor` to confirm all three are found.\n"
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
# Side doors: someone else's extraction
# --------------------------------------------------------------------------- #

def from_mineru(
    mineru_dir: Path,
    asset_dir: Path,
    *,
    source_name: str,
    lang_source: str,
    lang_target: str,
) -> dict[str, Any]:
    """Import a MinerU run (``*_content_list.json`` plus its ``images/``)."""
    candidates = sorted(mineru_dir.rglob("*content_list.json"))
    if not candidates:
        raise ExtractError(
            f"no *_content_list.json under {mineru_dir} — run MinerU first, e.g.\n"
            f"  mineru -p book.pdf -o {mineru_dir}"
        )
    content_list = json.loads(candidates[0].read_text(encoding="utf-8"))
    base = candidates[0].parent
    asset_dir.mkdir(parents=True, exist_ok=True)

    book = ir.new_book(
        source_path=str(candidates[0]), source_format="mineru",
        title=source_name, lang_source=lang_source, lang_target=lang_target,
    )
    blocks: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    counter = 0
    last_page = None

    def add(block_type: str, **fields: Any) -> dict[str, Any]:
        nonlocal counter
        counter += 1
        block = ir.make_block(block_type, counter, **fields)
        blocks.append(block)
        return block

    for item in content_list:
        page = int(item.get("page_idx", 0)) + 1
        if last_page is not None and page != last_page:
            add("pagebreak", page=page, soft=True)
        last_page = page
        kind = item.get("type")

        if kind == "text":
            text = (item.get("text") or "").strip()
            if not text:
                continue
            level = int(item.get("text_level") or 0)
            if level > 0:
                add("heading", page=page, level=min(6, level), text=ir.escape_markup(text))
            else:
                add("paragraph", page=page, text=ir.escape_markup(text))

        elif kind in {"image", "table", "equation"}:
            rel = item.get("img_path")
            if rel:
                asset_name = _copy_asset(base / rel, asset_dir, seen, page)
                if asset_name:
                    box = _mineru_bbox(item.get("bbox"), book["page"])
                    add("image", page=page, asset=asset_name,
                        sha256=seen[asset_name], bbox=box,
                        width_pt=round(box[2] - box[0], 2) if box else None,
                        height_pt=round(box[3] - box[1], 2) if box else None,
                        pixel_width=None, pixel_height=None,
                        alt="", target_alt=None, mineru_type=kind)
            for caption in item.get(f"{kind}_caption", []) or []:
                if caption.strip():
                    add("caption", page=page, text=ir.escape_markup(caption.strip()))

    book["blocks"] = blocks
    book["source"]["pages"] = last_page or 0
    return book


def _mineru_bbox(bbox: Any, page: dict[str, Any]) -> list[float] | None:
    """MinerU reports boxes normalised to 0-1000; convert them to points.

    Discarding this was throwing away the one thing the MinerU path exists to
    provide — an illustration's real size and position on a scanned page.
    """
    if not bbox or len(bbox) != 4:
        return None
    try:
        left, top, right, bottom = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    width = float(page.get("width_pt") or 0) or 595.3
    height = float(page.get("height_pt") or 0) or 841.9
    return [round(left / 1000 * width, 2), round(top / 1000 * height, 2),
            round(right / 1000 * width, 2), round(bottom / 1000 * height, 2)]


def _copy_asset(src: Path, asset_dir: Path, seen: dict[str, str], page: int) -> str | None:
    if not src.exists():
        return None
    data = src.read_bytes()
    digest = ir.sha256_bytes(data)
    for name, existing in seen.items():
        if existing == digest:
            return name
    asset_name = f"m{page:04d}-{src.name}"
    (asset_dir / asset_name).write_bytes(data)
    seen[asset_name] = digest
    return asset_name


_MD_IMAGE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\((?P<src><?[^)\s]+>?)(?:\s+\"[^\"]*\")?\)\s*$")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_LIST = re.compile(r"^\s*(?:[-*+]|(\d+)[.)])\s+(.*)$")


def from_markdown(
    md_path: Path,
    asset_dir: Path,
    *,
    lang_source: str,
    lang_target: str,
) -> dict[str, Any]:
    """Import pre-extracted Markdown (Marker, Docling, or a hand-made file).

    Images are resolved relative to the Markdown file and copied into the run's
    asset directory so later stages have one place to look.
    """
    asset_dir.mkdir(parents=True, exist_ok=True)
    book = ir.new_book(
        source_path=str(md_path), source_format="markdown",
        source_sha256=ir.sha256_file(md_path), title=md_path.stem,
        lang_source=lang_source, lang_target=lang_target,
    )

    blocks: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    counter = 0
    paragraph: list[str] = []

    def add(block_type: str, **fields: Any) -> None:
        nonlocal counter
        counter += 1
        blocks.append(ir.make_block(block_type, counter, **fields))

    def flush(kind: str = "paragraph") -> None:
        nonlocal paragraph
        text = " ".join(part.strip() for part in paragraph if part.strip()).strip()
        if text:
            add(kind, page=0, text=text)
        paragraph = []

    for raw in md_path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue

        image = _MD_IMAGE.match(line.strip())
        if image:
            flush()
            src = (md_path.parent / image.group("src").strip("<>")).resolve()
            asset_name = _copy_asset(src, asset_dir, seen, 0)
            if asset_name:
                add("image", page=0, asset=asset_name, sha256=seen[asset_name],
                    bbox=None, width_pt=None, height_pt=None,
                    pixel_width=None, pixel_height=None,
                    alt=image.group("alt"), target_alt=None)
            continue

        heading = _MD_HEADING.match(line)
        if heading:
            flush()
            add("heading", page=0, level=len(heading.group(1)), text=heading.group(2).strip())
            continue

        if line.strip() in {"---", "***", "___"}:
            flush()
            add("separator", page=0)
            continue

        if line.lstrip().startswith(">"):
            paragraph.append(line.lstrip().lstrip(">").strip())
            continue

        listed = _MD_LIST.match(line)
        if listed:
            flush()
            add("listitem", page=0, level=1, ordered=bool(listed.group(1)),
                text=listed.group(2).strip())
            continue

        paragraph.append(line)

    flush()
    book["blocks"] = blocks
    return book


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def extract(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out)
    asset_dir = out_dir / "assets"
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}

    if args.from_mineru:
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
    elif probe["kind"] != "digital":
        report["ocr"] = {"skipped": "--ocr off", "warning":
                         f"{len(probe['pages_without_text'])}+ pages have no text layer"}

    from read_pdf import read_pdf
    book = read_pdf(str(read_from), asset_dir,
                    lang_source=args.source_lang, lang_target=args.target_lang,
                    max_pages=args.max_pages,
                    ocr_text=bool(report.get("ocr")))
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


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat extract", description=__doc__)
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
