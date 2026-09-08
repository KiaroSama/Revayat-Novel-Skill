"""Importing an extraction somebody else already did.

`extract` decides what a file *is* — born-digital, scanned, mixed — and how to
get text out of it. This module answers a different question: a book has already
been read by a better tool, and what is needed is to turn its output into the
Book IR without pretending to have reimplemented it.

Two side doors. **MinerU** brings a layout model that finds illustrations which
are not separate PDF image objects at all — the ordinary case in a scan, where
the picture is part of the page raster — and `merge_mineru_figures` can graft
just those onto a book this project read itself, cropping each plate from the
source's own pixels rather than taking MinerU's re-encode. **Markdown** is the
escape hatch for anything else: whatever produced it, if it can write Markdown
the book can be imported.

Split out of `extract.py` at 906 lines, on the same boundary `rasters.py` and
`pagecli.py` were: not a line count, but a different question with a surface of
three functions. Nothing here knows about format detection, OCR routing or the
`extract` stage's arguments.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import bookir as ir
from rasters import crop_from_source


class ExtractError(RuntimeError):
    """Actionable failure — the message names the fix, not just the symptom."""


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


def merge_mineru_figures(
    book: dict[str, Any],
    mineru_dir: Path,
    asset_dir: Path,
    *,
    page_offset: int = 0,
    source_pdf: Path | None = None,
) -> dict[str, Any]:
    """Replace whole-page scans with the figures MinerU cropped out of them.

    This deliberately takes *only* the pictures. MinerU's own recognised text is
    ignored, and for Persian it has to be: measured on a real page, its OCR
    returned the words and the letters within them in reverse order — the
    classic right-to-left failure. Tesseract with `fas` reads the same page
    correctly, so the text keeps coming from there and MinerU is used for the
    one thing OCR cannot do, which is finding where a picture sits inside a
    flat raster.

    ``page_offset`` maps MinerU's ``page_idx`` onto the book's page numbers,
    for when MinerU was run over a page range rather than the whole file.
    """
    candidates = sorted(mineru_dir.rglob("*content_list.json"))
    if not candidates:
        raise ExtractError(
            f"no *_content_list.json under {mineru_dir} — run MinerU first, e.g. "
            f"mineru -p book.pdf -o {mineru_dir} -b pipeline"
        )
    content = json.loads(candidates[0].read_text(encoding="utf-8"))
    base = candidates[0].parent
    asset_dir.mkdir(parents=True, exist_ok=True)

    page_size = book.get("page", ir.default_page_setup())
    figures: dict[int, list[dict[str, Any]]] = {}
    seen: dict[str, str] = {}
    degraded: list[str] = []

    # Opened once for the whole run: a 400-page scan would otherwise be reopened
    # for every plate in it.
    document = None
    if source_pdf is not None and Path(source_pdf).exists():
        import pymupdf
        document = pymupdf.open(source_pdf)

    for item in content:
        if item.get("type") not in {"image", "table"}:
            continue
        relative = item.get("img_path")
        if not relative:
            continue
        source = base / relative.replace("/", os.sep)
        if not source.exists():
            matches = sorted(source.parent.glob(source.name + "*"))
            if not matches:
                continue
            source = matches[0]

        page = int(item.get("page_idx", 0)) + 1 + page_offset
        box = _mineru_bbox(item.get("bbox"), page_size)

        # MinerU's exported crop is a re-encode of its own render, at whatever
        # resolution it happened to work at. The detection is what it is good
        # for; the pixels should come from the book. Cutting the same box out
        # of the page's own raster keeps the plate at the resolution the scan
        # actually holds.
        cut = None
        if document is not None and box:
            candidate = asset_dir / f"m{page:04d}-fig{len(seen) + 1:03d}.png"
            try:
                cut = crop_from_source(document, page, box, candidate)
            except Exception as error:  # a damaged page must not lose the figure
                cut = None
                degraded.append(f"page {page}: {type(error).__name__}")
            if cut:
                data = candidate.read_bytes()
                asset_name = candidate.name
                seen[asset_name] = ir.sha256_bytes(data)

        if cut is None:
            asset_name = _copy_asset(source, asset_dir, seen, page)
            if not asset_name:
                continue

        captions = [c for c in (item.get("image_caption") or []) if str(c).strip()]
        figures.setdefault(page, []).append({
            "asset": asset_name,
            "sha256": seen[asset_name],
            "bbox": box,
            "width_pt": round(box[2] - box[0], 2) if box else None,
            "height_pt": round(box[3] - box[1], 2) if box else None,
            "top": box[1] if box else 0.0,
            "caption": captions[0] if captions else "",
            "pixel_width": (cut or {}).get("pixel_width"),
            "pixel_height": (cut or {}).get("pixel_height"),
            "crop": (cut or {}).get("crop"),
            "source": "source-raster" if cut else "mineru",
        })

    if document is not None:
        document.close()

    report = _place_figures(book, figures)
    if degraded:
        report["fell_back_to_mineru_crop"] = degraded
    return report


#: A figure of the same size in the same spot on at least this share of pages
#: is furniture — a watermark or a logo — rather than an illustration.
FIGURE_REPEAT_SHARE = 0.25
#: Rounding used when deciding "the same size in the same spot", in points.
FIGURE_REPEAT_TOLERANCE = 8


def _drop_repeated_furniture(figures: dict[int, list[dict[str, Any]]]
                             ) -> tuple[dict[int, list[dict[str, Any]]], int]:
    """Remove a figure that recurs at the same place on page after page.

    A layout model cannot tell a watermark from a picture — measured on a real
    book, MinerU cropped the publisher's translucent stamp as a figure. The
    signal that separates them is the same one that finds a running head: real
    illustrations differ from page to page, furniture does not.

    Running MinerU on ``cleaned.pdf`` avoids this entirely, because the stamp is
    already gone. This is the guard for when it is run on the original instead.
    """
    if len(figures) < 4:
        return figures, 0

    def key(figure: dict[str, Any]) -> tuple[int, ...]:
        box = figure.get("bbox") or [0, 0, 0, 0]
        return tuple(int(round(v / FIGURE_REPEAT_TOLERANCE)) for v in box)

    counts: dict[tuple[int, ...], int] = {}
    for items in figures.values():
        for shape in {key(figure) for figure in items}:
            counts[shape] = counts.get(shape, 0) + 1

    threshold = max(3, int(len(figures) * FIGURE_REPEAT_SHARE))
    furniture = {shape for shape, count in counts.items() if count >= threshold}
    if not furniture:
        return figures, 0

    dropped = 0
    kept: dict[int, list[dict[str, Any]]] = {}
    for page, items in figures.items():
        survivors = [f for f in items if key(f) not in furniture]
        dropped += len(items) - len(survivors)
        if survivors:
            kept[page] = survivors
    return kept, dropped


def _place_figures(book: dict[str, Any],
                   figures: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
    """Put each cropped figure where it belongs in the page's block order."""
    figures, furniture_dropped = _drop_repeated_furniture(figures)
    if not figures:
        return {"pages": 0, "figures_added": 0, "page_scans_replaced": 0,
                "furniture_dropped": furniture_dropped}

    blocks = book.get("blocks", [])
    highest = max(
        (int(b["id"][1:]) for b in blocks if b["id"][1:].isdigit()), default=0
    )
    counter = highest
    replaced = 0
    added = 0
    rebuilt: list[dict[str, Any]] = []
    handled: set[int] = set()

    for block in blocks:
        page = int(block.get("page") or 0)
        if page in figures and page not in handled:
            # A whole-page scan on this page is exactly what the crops replace.
            if block["type"] == "image":
                replaced += 1
                handled.add(page)
                for figure in sorted(figures[page], key=lambda f: f["top"]):
                    counter += 1
                    rebuilt.append(_figure_block(counter, page, figure))
                    added += 1
                continue
        rebuilt.append(block)

    # Pages whose whole-page scan was already dropped have nothing to replace,
    # so each figure has to be slotted back into reading order by where it sat
    # on the page. Appending them all at the end of the page — which is what
    # this used to do — moves a picture that belonged between two paragraphs to
    # after both of them, and the caption then explains the wrong thing.
    unplaced: list[str] = []
    for page, items in figures.items():
        if page in handled:
            continue
        for figure in sorted(items, key=lambda f: f["top"]):
            counter += 1
            block = _figure_block(counter, page, figure)
            position, derived = _reading_order_slot(rebuilt, page, figure["top"])
            rebuilt.insert(position, block)
            added += 1
            if not derived:
                unplaced.append(f"{block['id']} (page {page})")

    book["blocks"] = rebuilt
    report = {
        "pages": len(figures),
        "figures_added": added,
        "page_scans_replaced": replaced,
        "furniture_dropped": furniture_dropped,
    }
    if unplaced:
        # Said out loud rather than absorbed: a figure at the end of a page
        # because nothing could be measured looks identical to one that
        # genuinely belongs there.
        report["placed_at_page_end"] = unplaced
    return report


def _reading_order_slot(blocks: list[dict[str, Any]], page: int,
                        top: float) -> tuple[int, bool]:
    """Where a figure sitting at ``top`` belongs among ``page``'s blocks.

    Returns ``(index, derived_from_geometry)``. The flag matters: a figure put
    at the end of a page because its neighbours had no boxes to compare against
    is a guess, and the caller reports it rather than letting it pass as a
    measured placement.
    """
    first = last = -1
    for index, block in enumerate(blocks):
        if int(block.get("page") or 0) != page:
            continue
        if first < 0:
            first = index
        last = index
        box = block.get("bbox")
        # The first block that starts below the figure is the one it goes
        # before. `>=` keeps a tie deterministic: the figure wins the position.
        if box and float(box[1]) >= top:
            return index, True

    if last >= 0:
        # Every block on the page sits above it — the end of the page is the
        # measured answer, not a fallback, as long as something was measurable.
        measured = any(b.get("bbox") for b in blocks[first:last + 1]
                       if int(b.get("page") or 0) == page)
        return last + 1, measured
    return len(blocks), False


def _figure_block(index: int, page: int, figure: dict[str, Any]) -> dict[str, Any]:
    width = figure.get("pixel_width")
    height = figure.get("pixel_height")
    return ir.make_block(
        "image", index, page=page, asset=figure["asset"], sha256=figure["sha256"],
        bbox=figure["bbox"], width_pt=figure["width_pt"],
        height_pt=figure["height_pt"],
        pixel_width=width, pixel_height=height,
        aspect_ratio=round(width / height, 4) if width and height else None,
        alt=figure["caption"], target_alt=None,
        source=figure.get("source", "mineru"),
        crop=figure.get("crop"),
    )


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
