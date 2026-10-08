"""Drawn PDF images: asset bytes and physical occurrences are distinct."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import bookir as ir

PAGE_IMAGE_AREA_SHARE = 0.85
PAGE_IMAGE_TEXT_THRESHOLD = 200


def _is_the_page_itself(image: dict[str, Any], page_area: float,
                        page_chars: int) -> bool:
    """A page-sized raster with recognized prose is not a second illustration."""
    width, height = image.get("width_pt"), image.get("height_pt")
    if not width or not height or page_area <= 0:
        return False
    return (width * height) / page_area >= PAGE_IMAGE_AREA_SHARE and (
        page_chars >= PAGE_IMAGE_TEXT_THRESHOLD)


def drawn_images(page: Any) -> list[dict[str, Any]]:
    """One item per actual draw, including inline images; no resource aliases."""
    return page.get_image_info(xrefs=True)


def full_page_raster(page: Any) -> dict[str, Any] | None:
    """Admit one axis-aligned raster exactly covering the unrotated crop box.

    Refuse tilted, mirrored, letterboxed and repeated draws: a bounding box alone
    cannot prove that replacing a stream preserves its physical placement.
    """
    images = drawn_images(page)
    if len(images) != 1 or not images[0].get("xref"):
        return None
    image = images[0]
    box, transform = image.get("bbox"), image.get("transform")
    width, height = float(page.cropbox.width), float(page.cropbox.height)
    if not box or not transform or not all(math.isfinite(float(v)) for v in (*box, *transform)):
        return None
    a, b, c, d, e, f = transform
    if a <= 0 or d <= 0 or abs(b) > 0.01 or abs(c) > 0.01:
        return None
    expected = (0.0, 0.0, width, height)
    if any(abs(float(actual) - want) > 0.5 for actual, want in zip(box, expected)):
        return None
    if any(abs(float(actual) - want) > 0.5 for actual, want in
           zip((a, d, e, f), (width, height, 0.0, 0.0))):
        return None
    return image


def _extract_images(doc: Any, page: Any, page_no: int, asset_dir: Path,
                    seen: dict[str, str]) -> list[dict[str, Any]]:
    """Store unique payloads once; keep every draw and its native transform."""
    found = []
    decoded: dict[int, dict[str, Any]] = {}
    inline = None
    for order, occurrence in enumerate(drawn_images(page), start=1):
        xref = int(occurrence.get("xref") or 0)
        if xref:
            if xref not in decoded:
                decoded[xref] = doc.extract_image(xref) or {}
            raw = decoded[xref]
        else:
            # Inline images have no independently extractable XObject. DICT
            # supplies their original encoded payload, not a page render.
            if inline is None:
                inline = {block.get("number"): block for block in
                          page.get_text("dict")["blocks"] if block.get("type") == 1}
            raw = inline.get(occurrence.get("number"), {})
        data, box = raw.get("image"), occurrence.get("bbox")
        if not data or not box:
            raise ValueError(f"PDF image occurrence unreadable on page {page_no}")
        digest = ir.sha256_bytes(data)
        asset_name = seen.get(digest)
        if asset_name is None:
            asset_name = f"p{page_no:04d}-img{order:03d}.{raw.get('ext', 'png')}"
            (asset_dir / asset_name).write_bytes(data)
            seen[digest] = asset_name
        found.append({
            "asset": asset_name, "sha256": digest, "page": page_no,
            "bbox": [round(float(v), 2) for v in box],
            "transform": [round(float(v), 6) for v in occurrence["transform"]],
            "drawing_order": order,
            "width_pt": round(box[2] - box[0], 2),
            "height_pt": round(box[3] - box[1], 2),
            "pixel_width": raw.get("width"), "pixel_height": raw.get("height"),
            "top": float(box[1]),
        })
    return found
