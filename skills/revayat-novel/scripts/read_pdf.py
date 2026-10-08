"""PDF → Book IR via PyMuPDF.

Two things this does that a naive `pdf → markdown` pass does not:

* **Image bytes are extracted, never re-rendered.** ``Document.extract_image``
  returns the original compressed stream, so the picture in the DOCX is the
  same file that was in the book, at its original resolution.
* **Physical geometry is kept.** ``page.get_image_rects`` gives the on-page
  rectangle in points, which becomes the Word picture's ``wp:extent`` — so a
  4.2 cm illustration stays 4.2 cm instead of being stretched to text width.

Running heads and page numbers are detected by repetition across pages and
dropped, because a translator should never see them and a reader never wants
them inlined in a paragraph.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bookir as ir

try:
    import pymupdf
except ImportError:  # PyMuPDF < 1.24 only exposed the legacy name
    import fitz as pymupdf  # type: ignore[no-redef]

from pdfimages import (  # noqa: F401
    PAGE_IMAGE_AREA_SHARE, PAGE_IMAGE_TEXT_THRESHOLD, _extract_images,
    _is_the_page_itself, full_page_raster,
)
from pdftext import (  # noqa: F401
    FLAG_ITALIC, FLAG_BOLD, RUNNING_HEAD_MIN_SHARE, MARGIN_BAND,
    SIZE_GROUP_TOLERANCE, OCR_SIZE_GROUP_TOLERANCE, CENTRED_MAX_WIDTH_SHARE,
    CENTRED_TOLERANCE_PT, SECTION_GAP_RATIO, _style_of, _line_text,
    _collect_running_heads, _normalise_head, _body_font_size, _alignment,
    _style_evidence, _heading_level, _join_lines, _block_groups, _union,
    _continues, _merge_split_paragraphs, _OCR_FONTS, _CHAPTER_WORD, _SENTENCE_END,
)

SCANNED_CHAR_THRESHOLD = 60


#: Sizes within this many points of each other are the same paper. PDF page
#: boxes drift by fractions of a point between pages of one book.
GEOMETRY_TOLERANCE_PT = 2.0


#: The page boxes worth recording beside the effective size. `page.rect` is the
#: crop box normalised to the origin, so it cannot show a crop moved sideways
#: across the media - a change that alters the visible page. These can.
GEOMETRY_BOXES = ("mediabox", "cropbox", "trimbox", "bleedbox", "artbox")


def _page_boxes(page) -> dict[str, list[float]]:
    """One page's declared boxes, for provenance and for debugging a mismatch.

    Only boxes that differ from the crop box are kept: a book where every page
    declares the same four identical boxes would otherwise carry five copies of
    one rectangle for every page, and say nothing.
    """
    crop = getattr(page, "cropbox", None)
    if crop is None:
        return {}
    drawn = {"cropbox": [round(v, 2) for v in (crop.x0, crop.y0, crop.x1, crop.y1)]}
    for name in GEOMETRY_BOXES:
        if name == "cropbox":
            continue
        box = getattr(page, name, None)
        if box is None:
            continue
        shape = [round(v, 2) for v in (box.x0, box.y0, box.x1, box.y1)]
        if shape != drawn["cropbox"]:
            drawn[name] = shape
    return drawn if len(drawn) > 1 else {}


def _survey_geometry(doc, page_count: int) -> dict[str, Any]:
    """Census every page's size and rotation, not just the first one's.

    Taking page 1 as the book's geometry is wrong in a specific and common way:
    a novel with a landscape map or a differently trimmed front matter page at
    the front would set the *whole* translated document to that shape. The
    dominant geometry is the book's; anything else is reported so the mismatch
    is visible rather than silently applied to every page.
    """
    shapes: dict[tuple[int, int, int], list[int]] = {}
    boxes: dict[str, dict[str, list[float]]] = {}
    for index in range(page_count):
        page = doc[index]
        key = (int(round(page.rect.width / GEOMETRY_TOLERANCE_PT)),
               int(round(page.rect.height / GEOMETRY_TOLERANCE_PT)),
               int(getattr(page, "rotation", 0) or 0))
        shapes.setdefault(key, []).append(index + 1)
        drawn = _page_boxes(page)
        if drawn:
            boxes[str(index + 1)] = drawn

    if not shapes:
        return {"width_pt": 396.0, "height_pt": 612.0, "uniform": True,
                "rotated_pages": [], "variants": []}

    dominant = max(shapes.items(), key=lambda item: len(item[1]))
    width = dominant[0][0] * GEOMETRY_TOLERANCE_PT
    height = dominant[0][1] * GEOMETRY_TOLERANCE_PT

    variants = [
        {"width_pt": round(key[0] * GEOMETRY_TOLERANCE_PT, 2),
         "height_pt": round(key[1] * GEOMETRY_TOLERANCE_PT, 2),
         "rotation": key[2], "pages": pages[:20], "page_count": len(pages)}
        for key, pages in sorted(shapes.items(), key=lambda item: -len(item[1]))
    ]
    # Every page that is *not* the dominant shape, in full and untruncated.
    # `variants` samples 20 pages each because it exists to be read by a person;
    # this exists to be read by the page run, which lays each source page out on
    # its own and must use that page's real trim and rotation. A sample would
    # silently give page 137 the wrong paper.
    exceptions = {
        str(page): {"width_pt": round(key[0] * GEOMETRY_TOLERANCE_PT, 2),
                    "height_pt": round(key[1] * GEOMETRY_TOLERANCE_PT, 2),
                    "rotation": key[2]}
        for key, pages in shapes.items() if key != dominant[0]
        for page in pages
    }
    return {
        "width_pt": round(width, 2),
        "height_pt": round(height, 2),
        "rotation": dominant[0][2],
        "uniform": len(shapes) == 1,
        "rotated_pages": sorted(
            page for key, pages in shapes.items() if key[2] for page in pages
        )[:20],
        "variants": variants,
        "pages": exceptions,
        "boxes": boxes,
    }


def read_pdf(
    path: str,
    asset_dir: Path,
    *,
    lang_source: str = "en",
    lang_target: str = "fa-IR",
    max_pages: int | None = None,
    ocr_text: bool = False,
    ocr_pages: list[int] | None = None,
    page_roles: dict[int, str] | None = None,
) -> dict[str, Any]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(path)
    try:
        # A PDF can legally declare millions of empty pages in a few megabytes,
        # and the page route writes one file per page. Refused from the count
        # before a single page is read.
        ir.check_page_count(len(doc), str(path))
        return _read_open_pdf(
            doc, path, asset_dir,
            lang_source=lang_source, lang_target=lang_target, max_pages=max_pages,
            ocr_text=ocr_text, ocr_pages=ocr_pages, page_roles=page_roles,
        )
    finally:
        doc.close()


def _read_open_pdf(
    doc: "pymupdf.Document",
    path: str,
    asset_dir: Path,
    *,
    lang_source: str,
    lang_target: str,
    max_pages: int | None,
    ocr_text: bool = False,
    ocr_pages: list[int] | None = None,
    page_roles: dict[int, str] | None = None,
) -> dict[str, Any]:
    page_count = len(doc) if max_pages is None else min(len(doc), max_pages)
    recognized_pages = set(range(1, page_count + 1)) if ocr_text and ocr_pages is None else set(ocr_pages or [])
    if any(type(n) is not int or not 1 <= n <= len(doc) for n in recognized_pages):
        raise ValueError("OCR page provenance indices are outside the PDF inventory")
    if page_roles is not None and (not isinstance(page_roles, dict) or any(
            type(n) is not int or not 1 <= n <= len(doc)
            or not isinstance(role, str) or role not in {"text", "image", "blank", "unknown"}
            for n, role in page_roles.items())):
        raise ValueError("PDF page roles are outside the source inventory")
    raw_pages = [
        doc[i].get_text("dict", sort=True,
                        flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
        for i in range(page_count)
    ]

    geometry = _survey_geometry(doc, page_count)
    page_w, page_h = geometry["width_pt"], geometry["height_pt"]

    for index, raw_page in enumerate(raw_pages):
        raw_page["height"] = float(doc[index].cropbox.height)
    drop = _collect_running_heads(raw_pages, page_h)
    body_size = _body_font_size(raw_pages)

    info = doc.metadata or {}
    book = ir.new_book(
        source_path=str(path),
        source_format="pdf",
        source_sha256=ir.sha256_file(path),
        pages=page_count,
        title=(info.get("title") or Path(path).stem).strip(),
        author=(info.get("author") or "").strip(),
        lang_source=lang_source,
        lang_target=lang_target,
    )
    book["page"].update({
        "width_pt": round(page_w, 2),
        "height_pt": round(page_h, 2),
    })
    # The census travels with the book so nothing downstream has to assume the
    # geometry is uniform. When it is not, `book["page"]` is the dominant shape
    # and this says plainly which pages disagree with it.
    book["source"]["page_geometry"] = geometry

    blocks: list[dict[str, Any]] = []
    seen_assets: dict[str, str] = {}
    scanned_pages: list[int] = []
    dropped_page_scans: list[int] = []
    counter = 0

    def add(block_type: str, **fields: Any) -> dict[str, Any]:
        nonlocal counter
        counter += 1
        block = ir.make_block(block_type, counter, **fields)
        blocks.append(block)
        return block

    for index in range(page_count):
        page_no = index + 1
        page = doc[index]
        page_dict = raw_pages[index]
        page_ocr = page_no in recognized_pages
        tolerance = OCR_SIZE_GROUP_TOLERANCE if page_ocr else SIZE_GROUP_TOLERANCE

        images = _extract_images(doc, page, page_no, asset_dir, seen_assets)
        text_items: list[tuple[float, str, dict[str, Any]]] = []

        page_chars = 0
        for raw_block in page_dict.get("blocks", []):
            if raw_block.get("type") != 0:
                continue
            for group in _block_groups(raw_block, drop, tolerance, ocr=page_ocr,
                                       page_height=float(page.cropbox.height)):
                page_chars += len(group["markup"])
                text_items.append((group["bbox"][1], "text", group))

        if page_chars < SCANNED_CHAR_THRESHOLD and images:
            scanned_pages.append(page_no)

        page_area = float(page.rect.width) * float(page.rect.height)
        preserve_artwork = (page_roles or {}).get(page_no) == "image"
        verified_scan = full_page_raster(page) if page_ocr else None
        for image in images:
            if not preserve_artwork and ((verified_scan is not None and len(images) == 1 and page_chars > 0) or _is_the_page_itself(image, page_area, page_chars)):
                dropped_page_scans.append(page_no)
                continue
            image["source_role"] = (
                "unknown-page-raster" if page_ocr and not preserve_artwork
                and image["width_pt"] * image["height_pt"] >= page_area * 0.5
                else "illustration")
            text_items.append((image["top"], "image", image))

        text_items.sort(key=lambda item: item[0])

        # The text column, measured rather than assumed: a book's real margins
        # are whatever its own lines occupy, not the page box.
        boxes = [p["bbox"] for _, k, p in text_items if k == "text" and p.get("bbox")]
        text_left = min((b[0] for b in boxes), default=0.0)
        text_right = max((b[2] for b in boxes), default=float(page.rect.width))

        if index > 0:
            add("pagebreak", page=page_no, soft=True)

        previous_bottom: float | None = None
        seen_text_on_page = False
        for _, kind, payload in text_items:
            if kind == "image":
                add(
                    "image",
                    page=page_no,
                    asset=payload["asset"],
                    sha256=payload["sha256"],
                    bbox=payload["bbox"],
                    transform=payload["transform"],
                    drawing_order=payload["drawing_order"],
                    source_role=payload["source_role"],
                    width_pt=payload["width_pt"],
                    height_pt=payload["height_pt"],
                    pixel_width=payload["pixel_width"],
                    pixel_height=payload["pixel_height"],
                    alt="",
                    target_alt=None,
                )
                continue

            markup = payload["markup"]
            box = payload["bbox"]
            gap_before = (box[1] - previous_bottom) if previous_bottom is not None else None
            evidence = _style_evidence(
                payload, body_size=body_size, page_width=float(page.rect.width),
                text_left=text_left, text_right=text_right,
                gap_before=gap_before, starts_page=not seen_text_on_page,
                ocr=page_ocr,
            )
            previous_bottom, seen_text_on_page = box[3], True

            level = _heading_level(
                payload["size"], body_size, ir.plain_text(markup), payload["bold"],
                ocr=page_ocr, evidence=evidence,
            )
            if level is not None:
                add("heading", page=page_no, level=level, bbox=box, text=markup,
                    font_size_pt=round(payload["size"], 2), style_evidence=evidence)
            else:
                add("paragraph", page=page_no, bbox=box, text=markup)

    book["blocks"] = _merge_split_paragraphs(blocks)
    book["source"]["scanned_pages"] = scanned_pages
    book["source"]["body_font_pt"] = body_size
    book["source"]["running_heads_dropped"] = sorted(drop)[:20]
    book["source"]["from_ocr"] = ocr_text
    book["source"]["ocr_pages"] = sorted(recognized_pages)
    if recognized_pages:
        # Stated rather than left to be inferred from an absence: a scanned
        # book that comes back with no italics anywhere has not necessarily
        # lost them in translation — they were never in the text layer to read.
        book["source"]["emphasis"] = {
            "recovered": False,
            "pages": sorted(recognized_pages),
            "reason": "OCR-recognized pages have synthetic text typography; "
                      "native pages retain their emphasis",
        }
    book["source"]["page_scans_dropped"] = len(dropped_page_scans)
    return book
