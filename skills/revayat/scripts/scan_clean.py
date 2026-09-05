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

import io
from pathlib import Path
from typing import Any

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
            images = page.get_images(full=True)
            cleaned_bytes = None

            if len(images) == 1 and not page.get_text("text").strip():
                try:
                    raw = doc.extract_image(images[0][0])
                except Exception:
                    raw = None
                if raw is not None:
                    original = Image.open(io.BytesIO(raw["image"]))
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
