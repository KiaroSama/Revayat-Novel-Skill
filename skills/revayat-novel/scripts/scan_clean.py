"""Remove a colour watermark stamped onto scanned pages.

A scanned book is one raster per page, so a watermark is *burned into the
pixels* — there is no image object to drop and no text run to filter. It has to
come out of the raster or it stays in the book, and it also degrades OCR
wherever it crosses a line of text.

The signal this uses is narrow and measurable: body text in a scan is
**grayscale** — measured on a real 1785×2577 page, every text pixel had HSV
saturation exactly 0 — while a colour watermark reaches saturation 255. So
"pixels with meaningful saturation" selects the watermark and nothing else.

Two guards keep it from eating real content:

* a page is only cleaned when its coloured fraction is *small*. A watermark
  covers well under 1% of a page (0.24% measured); a genuine colour
  illustration covers far more, so illustration pages are skipped
  automatically rather than being wiped;
* only saturated pixels are touched. Grayscale content — text, line art,
  black-and-white photographs — is left byte-identical.

**Where a translucent watermark sits on top of text, removal is lossy and
cannot be otherwise.** Those glyph pixels were blended with the watermark when
it was stamped, so the original ink value is simply not in the file any more.
Measured on a real page: pushing the mid-tones to white does erase the ghost,
but it also eats the letters underneath it, leaving visible gaps. So the
default removes colour only — which clears the watermark over blank paper and
leaves a faint grey remnant over text — and the aggressive mid-tone cut is
opt-in via ``ghost_threshold``.

For this project that trade-off is usually moot: the deliverable is a DOCX
built from OCR'd *text*, so the watermark never reaches the output either way.
What matters is which version Tesseract reads more accurately, and that is
worth measuring per book rather than assuming.

A purely grayscale watermark cannot be separated at all: there is no signal
distinguishing it from the text it overlaps.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any

import bookir as ir

#: Saturation above this counts as coloured. Text measured 0, so this is a
#: wide margin that still catches faint, heavily-transparent watermark edges.
SATURATION_THRESHOLD = 16

#: A page whose coloured fraction exceeds this is treated as real artwork and
#: left alone. Watermarks measured 0.24%; illustrations run orders higher.
MAX_COLOURED_FRACTION = 0.06

#: Watermark edges fade into the paper. After the coloured core is removed,
#: pixels this light are flattened to pure white to clear the halo — well above
#: antialiased text, which stays far darker.
HALO_VALUE_THRESHOLD = 244

#: Removing the colour leaves a grey ghost wherever the watermark was
#: desaturated. Measured on a real page: in a text-only band 3.7% of pixels are
#: darker than 100 and only 1.4% fall in 100–224, while in the ghost band the
#: mid-tone share is 15.7% — the ghost is mid-grey, the text is near-black. So a
#: cut in the mid-tones erases the ghost and leaves the strokes.
#:
#: This is off by default. It is a real trade-off: on a greyscale scan with
#: genuine tonal artwork it would flatten shading, so it must be asked for.
DEFAULT_GHOST_THRESHOLD = 120


class Unavailable(RuntimeError):
    """Pillow is not installed, so raster cleaning cannot run."""


def _pillow():
    try:
        from PIL import Image, ImageChops
    except ImportError as error:  # pragma: no cover - depends on the install
        raise Unavailable(
            "cleaning a scan's watermark needs Pillow:  pip install pillow"
        ) from error
    return Image, ImageChops


def coloured_fraction(image) -> float:
    """Share of pixels with meaningful colour saturation."""
    saturation = image.convert("HSV").getchannel("S")
    histogram = saturation.histogram()
    total = sum(histogram)
    if not total:
        return 0.0
    return sum(histogram[SATURATION_THRESHOLD + 1:]) / total


def clean_image(image, *, force: bool = False,
                ghost_threshold: int | None = None) -> tuple[Any, dict[str, Any]]:
    """Whiten the coloured watermark on one page image.

    ``ghost_threshold`` additionally whitens grey pixels lighter than that
    value, which clears the desaturated remnant a translucent watermark leaves
    behind. Pass ``None`` to keep every grey tone.

    Returns ``(image, report)``. The image is returned unchanged when the page
    looks like real artwork, unless ``force`` is set.
    """
    Image, _ = _pillow()
    rgb = image.convert("RGB")
    fraction = coloured_fraction(rgb)

    if fraction == 0.0 and ghost_threshold is None:
        return image, {"cleaned": False, "reason": "no colour", "fraction": 0.0}
    if fraction > MAX_COLOURED_FRACTION and not force:
        return image, {"cleaned": False, "reason": "looks like artwork",
                       "fraction": round(fraction, 5)}

    white = Image.new("RGB", rgb.size, (255, 255, 255))
    saturation = rgb.convert("HSV").getchannel("S")
    mask = saturation.point(lambda s: 255 if s > SATURATION_THRESHOLD else 0, mode="1")

    cleaned = rgb.copy()
    cleaned.paste(white, mask=mask)

    # The watermark fades out through near-white; flatten that halo so OCR sees
    # clean paper. Text is far darker than the threshold and is unaffected.
    cut = HALO_VALUE_THRESHOLD if ghost_threshold is None else max(ghost_threshold,
                                                                  SATURATION_THRESHOLD)
    grey = cleaned.convert("L")
    halo = grey.point(lambda v: 255 if v >= cut else 0, mode="1")
    cleaned.paste(white, mask=halo)

    return cleaned, {"cleaned": True, "reason": "colour watermark",
                     "fraction": round(fraction, 5), "ghost_cut": cut}


def _page_raster(doc: Any, page: Any) -> Any | None:
    """The page's single full-page raster, or ``None`` if it is not a scan page.

    One definition, used by `clean_pdf` and by `survey_pdf`, because a survey
    that disagrees with the cleaner about which pages are scan pages is a lie.
    A page with real text or several images is not a scan page: that is what
    makes running this over a mixed book safe.
    """
    images = page.get_images(full=True)
    if len(images) != 1 or page.get_text("text").strip():
        return None
    try:
        raw = doc.extract_image(images[0][0])
    except Exception:
        return None
    if raw is None:
        return None
    # Not `Image.open`: Pillow only warns in the band between one and two times
    # its pixel ceiling, and decodes anyway.
    return ir.open_image(io.BytesIO(raw["image"]))


def survey_pdf(source: Path) -> dict[str, Any]:
    """What cleaning *would* do, page by page. Reads only; writes nothing.

    The decision a reader has to make is whether to clean at all, and at what
    ghost threshold — and until this existed the only way to find out was to run
    a whole extraction and read the report afterwards. On a 400-page scan that is
    an hour to answer a question that takes seconds.

    Every page gets one of four verdicts, and they are the cleaner's own:
    ``would-clean``, ``looks-like-artwork``, ``no-colour``, ``not-a-single-raster``.
    """
    _pillow()
    import pymupdf  # noqa: PLC0415

    doc = pymupdf.open(source)
    pages: list[dict[str, Any]] = []
    try:
        for index, page in enumerate(doc):
            number = index + 1
            image = _page_raster(doc, page)
            if image is None:
                pages.append({"page": number, "verdict": "not-a-single-raster",
                              "fraction": None})
                continue
            fraction = coloured_fraction(image.convert("RGB"))
            if fraction == 0.0:
                verdict = "no-colour"
            elif fraction > MAX_COLOURED_FRACTION:
                verdict = "looks-like-artwork"
            else:
                verdict = "would-clean"
            pages.append({"page": number, "verdict": verdict,
                          "fraction": round(fraction, 5)})
    finally:
        doc.close()

    counts: dict[str, int] = {}
    for entry in pages:
        counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    measured = [p["fraction"] for p in pages if p["fraction"]]
    return {
        "source": str(source),
        "pages": len(pages),
        "counts": counts,
        # The ceiling that separated artwork from a stamp, so a reader who
        # disagrees with a verdict can see what it was compared against.
        "artwork_fraction_ceiling": MAX_COLOURED_FRACTION,
        "saturation_threshold": SATURATION_THRESHOLD,
        "coloured_fraction_range": ([min(measured), max(measured)]
                                    if measured else None),
        "per_page": pages,
    }


def preview_page(source: Path, page_number: int, out_dir: Path, *,
                 ghost_threshold: int | None = None,
                 force: bool = False) -> dict[str, Any]:
    """One page as PNGs: original, cleaned, and — if asked — ghost-cut.

    `references/extraction.md` states that where the watermark lies over text the
    removal is lossy and cannot be otherwise, and that a ghost threshold "does
    erase the grey remnant, but it eats the letters underneath it too". That is a
    judgement the reader is asked to make; this is what lets them make it by
    looking rather than by trusting the sentence.

    Deliberately not evidence: these PNGs are a tuning aid and never feed
    `review.evidence_digest`, which binds a reviewer's verdict to what they saw.
    """
    _pillow()
    import pymupdf  # noqa: PLC0415

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(source)
    try:
        if not 1 <= page_number <= doc.page_count:
            return {"ok": False, "refused": "no-such-page",
                    "detail": f"{source} has {doc.page_count} pages; "
                              f"asked for {page_number}"}
        page = doc[page_number - 1]
        image = _page_raster(doc, page)
        if image is None:
            return {"ok": False, "refused": "not-a-single-raster",
                    "detail": f"page {page_number} is not one full-page raster, "
                              f"so it is not a page this stage would touch"}

        written: dict[str, str] = {}
        stem = f"page-{page_number:04d}"
        original = out_dir / f"{stem}-original.png"
        image.convert("RGB").save(original, format="PNG")
        written["original"] = str(original)

        cleaned, outcome = clean_image(image, force=force)
        target = out_dir / f"{stem}-cleaned.png"
        cleaned.convert("RGB").save(target, format="PNG")
        written["cleaned"] = str(target)

        ghost = None
        if ghost_threshold is not None:
            ghosted, ghost = clean_image(image, force=force,
                                         ghost_threshold=ghost_threshold)
            path = out_dir / f"{stem}-ghost-{ghost_threshold}.png"
            ghosted.convert("RGB").save(path, format="PNG")
            written[f"ghost_{ghost_threshold}"] = str(path)
    finally:
        doc.close()

    return {"ok": True, "page": page_number, "renders": written,
            "colour_pass": outcome,
            "ghost_pass": ghost,
            "detail": "open these side by side; where the stamp crossed a letter "
                      "the ink is gone either way, and a ghost cut takes more."}


def clean_pdf(
    source: Path,
    destination: Path,
    *,
    force: bool = False,
    ghost_threshold: int | None = None,
    quality: int = 92,
) -> dict[str, Any]:
    """Rewrite a scanned PDF with the colour watermark removed.

    Only pages that are a single full-page raster are touched — that is what a
    scan looks like. A page with real text or several images is left exactly as
    it was, so this is safe to run over a mixed book.
    """
    Image, _ = _pillow()
    import pymupdf

    doc = pymupdf.open(source)
    out = pymupdf.open()
    report: dict[str, Any] = {"pages": len(doc), "cleaned_pages": [],
                              "skipped_pages": [], "untouched_pages": []}
    try:
        for index, page in enumerate(doc):
            number = index + 1
            cleaned_bytes = None

            # The same decision `survey_pdf` reports, so the two cannot disagree
            # about which pages this stage would touch.
            original = _page_raster(doc, page)
            if original is not None:
                cleaned, outcome = clean_image(
                    original, force=force, ghost_threshold=ghost_threshold
                )
                if outcome["cleaned"]:
                    buffer = io.BytesIO()
                    cleaned.convert("RGB").save(
                        buffer, format="JPEG", quality=quality, optimize=True
                    )
                    cleaned_bytes = buffer.getvalue()
                    report["cleaned_pages"].append(number)
                else:
                    report["skipped_pages"].append({"page": number, **outcome})

            if cleaned_bytes is None:
                # Carry the page through untouched, structure and all.
                if number not in [s["page"] for s in report["skipped_pages"]]:
                    report["untouched_pages"].append(number)
                out.insert_pdf(doc, from_page=index, to_page=index)
                continue

            # Rebuild the page around the cleaned raster rather than patching the
            # image stream in place. Overwriting a stream leaves the XObject's own
            # /Filter and /ColorSpace describing the *old* bytes, which silently
            # produces an unreadable image — measured: OCR fell to zero characters
            # on a page Tesseract had read perfectly well before.
            new_page = out.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(page.rect, stream=cleaned_bytes)

        destination.parent.mkdir(parents=True, exist_ok=True)
        out.save(str(destination), garbage=3, deflate=True)
    finally:
        out.close()
        doc.close()

    report["cleaned"] = len(report["cleaned_pages"])
    report["output"] = str(destination)
    return report


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #

def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)

    p_survey = sub.add_parser(
        "survey", help="what cleaning would do, page by page; writes nothing")
    p_survey.add_argument("--pdf", required=True, help="the scanned book")
    p_survey.add_argument("--pages-listed", type=int, default=40,
                          help="how many per-page rows to print (0 for all)")

    p_run = sub.add_parser(
        "run", help="write a cleaned copy; the original is never touched")
    p_run.add_argument("--pdf", required=True)
    p_run.add_argument("--out", required=True, help="the cleaned PDF to write")
    p_run.add_argument("--force", action="store_true",
                       help="clean even pages whose coloured fraction looks "
                            "like artwork")
    p_run.add_argument("--ghost-threshold", type=int, default=None,
                       metavar="N",
                       help=f"also whiten greys lighter than N (0-255). Lossy "
                            f"over text — preview it first. Try "
                            f"{DEFAULT_GHOST_THRESHOLD}")
    p_run.add_argument("--quality", type=int, default=92,
                       help="JPEG quality for a rewritten page")

    p_preview = sub.add_parser(
        "preview", help="one page as PNGs, before and after, to look at")
    p_preview.add_argument("--pdf", required=True)
    p_preview.add_argument("--page", type=int, required=True)
    p_preview.add_argument("--out", required=True, help="directory for the PNGs")
    p_preview.add_argument("--ghost-threshold", type=int, default=None,
                           metavar="N",
                           help="also write a third PNG with greys lighter than "
                                "N whitened, so the trade-off is visible")
    p_preview.add_argument("--force", action="store_true")


def main(argv: list[str] | None = None) -> int:
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(prog="revayat-novel clean-scan",
                                     description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)

    try:
        if args.action == "survey":
            report = survey_pdf(Path(args.pdf))
            listed = args.pages_listed
            shown = report["per_page"] if listed == 0 else report["per_page"][:listed]
            printable = {**report, "per_page": shown}
            if listed and len(report["per_page"]) > listed:
                printable["per_page_truncated"] = (
                    f"{len(report['per_page']) - listed} more; "
                    f"--pages-listed 0 for all")
            print(json.dumps(printable, ensure_ascii=False, indent=1))
            return 0

        if args.action == "preview":
            made = preview_page(Path(args.pdf), args.page, Path(args.out),
                                ghost_threshold=args.ghost_threshold,
                                force=args.force)
            print(json.dumps(made, ensure_ascii=False, indent=1))
            return 0 if made["ok"] else 2

        report = clean_pdf(Path(args.pdf), Path(args.out), force=args.force,
                           ghost_threshold=args.ghost_threshold,
                           quality=args.quality)
    except Unavailable as missing:
        print(json.dumps({"ok": False, "refused": "pillow-missing",
                          "detail": str(missing)}, ensure_ascii=False, indent=1))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
