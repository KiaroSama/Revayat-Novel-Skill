"""Cutting an illustration out of the page it was printed on.

A layout model finds *where* a picture is; it does not hand you the picture.
MinerU exports its own crop, and taking that is the easy path — but it is a
re-encode of MinerU's render, at whatever resolution MinerU happened to work
at. The detection is what MinerU is good for; the pixels should come from the
book.

Split out of `extract.py` because it answers a different question. Extraction
decides what a file *is* and how to read it; this decides which pixels on a page
are the best available and cuts them without resampling. Nothing here knows
about OCR routing, input formats or the Book IR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bookir as ir


#: Rendering fallback resolution. Only reached when the page has no single
#: embedded raster to cut from; 400 DPI keeps a half-page plate above the
#: 300 DPI that print work assumes.
CROP_RENDER_DPI = 400
#: An embedded image must cover at least this share of the page to be treated
#: as the scan of the whole page rather than one picture already placed on it.
#: Not 0.9: a scan is placed with its own aspect ratio preserved, so a page
#: whose proportions differ from the paper's is letterboxed by a few percent —
#: measured on a real 1200x1600 scan on a 396x612pt page, coverage is 0.86.
FULL_PAGE_COVERAGE = 0.8


def best_page_raster(document, page_number: int):
    """The best pixels this page has, and the rectangle they occupy on it.

    Returns ``(PIL image, provenance, placement)`` where ``placement`` is the
    ``(left, top, right, bottom)`` in points that the image covers. A scanned
    page is normally one large embedded raster, and those are its *original*
    pixels — re-rendering the page instead resamples them to whatever DPI was
    asked for, which either throws detail away or, worse, invents it by
    upscaling. So the embedded image wins whenever one covers the page, and
    rendering is the fallback for a page assembled from several objects.
    """
    from io import BytesIO

    from PIL import Image

    page = document[page_number - 1]
    rect = page.rect
    page_area = float(rect.width) * float(rect.height)

    best = None
    for block in page.get_image_info(xrefs=True):
        box = block.get("bbox")
        xref = block.get("xref")
        if not box or not xref:
            continue
        covered = (box[2] - box[0]) * (box[3] - box[1])
        if page_area and covered / page_area >= FULL_PAGE_COVERAGE:
            pixels = int(block.get("width") or 0) * int(block.get("height") or 0)
            if best is None or pixels > best[1]:
                best = (xref, pixels, box)

    if best is not None:
        extracted = document.extract_image(best[0])
        # `ir.open_image`, not `Image.open`: Pillow only *warns* in the band
        # between one and two times its pixel ceiling and decodes anyway.
        image = ir.open_image(BytesIO(extracted["image"]))
        return image, {
            "method": "embedded-page-image",
            "pixel_width": image.width,
            "pixel_height": image.height,
            "original_format": extracted.get("ext"),
        }, tuple(best[2])

    # At 400 dpi a legal 200-inch page is 6.4 Gpx. The declared size is checked
    # before a pixel exists; a page that size is not a book page, so this raises
    # rather than degrading to a smaller render nobody asked for.
    ir.check_render_area(rect.width, rect.height, CROP_RENDER_DPI)
    pixmap = page.get_pixmap(dpi=CROP_RENDER_DPI)
    image = ir.open_image(BytesIO(pixmap.tobytes("png")))
    return (image,
            {"method": "rendered-page", "dpi": CROP_RENDER_DPI,
             "pixel_width": image.width, "pixel_height": image.height},
            (0.0, 0.0, float(rect.width), float(rect.height)))


def crop_from_source(document, page_number: int, bbox_pt: list[float],
                     destination: Path) -> dict[str, Any] | None:
    """Cut ``bbox_pt`` out of the best raster this page has, without resampling.

    MinerU exports its own crop, and taking that is the easy path — but it is a
    re-encode of a render, at whatever resolution MinerU happened to work at.
    The detection is what MinerU is good for; the pixels should come from the
    book. Nothing here resizes: the crop is exactly the pixels inside the box,
    written as PNG so the cut itself adds no further loss.

    The box is mapped through the raster's own placement on the page, not
    through the page box. A scan rarely fills the paper exactly — it is centred
    with its aspect ratio kept — and measuring from the page corner instead of
    the image's own corner slides every crop by the size of the margin.
    """
    page = document[page_number - 1]
    if float(page.rect.width) <= 0 or float(page.rect.height) <= 0:
        return None

    image, provenance, placement = best_page_raster(document, page_number)
    placed_width = placement[2] - placement[0]
    placed_height = placement[3] - placement[1]
    if placed_width <= 0 or placed_height <= 0:
        return None

    scale_x = image.width / placed_width
    scale_y = image.height / placed_height
    left = max(0, int(round((bbox_pt[0] - placement[0]) * scale_x)))
    top = max(0, int(round((bbox_pt[1] - placement[1]) * scale_y)))
    right = min(image.width, int(round((bbox_pt[2] - placement[0]) * scale_x)))
    bottom = min(image.height, int(round((bbox_pt[3] - placement[1]) * scale_y)))
    if right - left < 2 or bottom - top < 2:
        return None

    cut = image.crop((left, top, right, bottom))
    cut.save(destination, format="PNG")
    return {
        "pixel_width": cut.width,
        "pixel_height": cut.height,
        "crop": {
            **provenance,
            "source_page": page_number,
            "bbox_pt": [round(v, 2) for v in bbox_pt],
            # Where the raster itself sits on the page, so a reviewer can
            # recompute the mapping instead of trusting it.
            "placement_pt": [round(v, 2) for v in placement],
            "pixel_box": [left, top, right, bottom],
            "resized": False,
            "encoding": "png",
        },
    }
