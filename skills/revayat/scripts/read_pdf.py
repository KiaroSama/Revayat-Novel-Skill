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

import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

import bookir as ir

try:
    import pymupdf
except ImportError:  # PyMuPDF < 1.24 only exposed the legacy name
    import fitz as pymupdf  # type: ignore[no-redef]

# PyMuPDF span flag bits (see Document.get_text("dict") docs).
FLAG_ITALIC = 1 << 1
FLAG_BOLD = 1 << 4

#: Fraction of pages a margin line must appear on to count as a running head.
RUNNING_HEAD_MIN_SHARE = 0.25
#: Height of the top/bottom band searched for running heads, as page fraction.
MARGIN_BAND = 0.08
#: A page with fewer than this many characters is treated as image-only.
SCANNED_CHAR_THRESHOLD = 60


def _style_of(span: dict[str, Any]) -> tuple[bool, bool]:
    """Bold/italic for a span, from render flags and the font name.

    Flags alone miss synthetic faces (e.g. ``AGaramondPro-BoldItalic`` embedded
    with flags=0), so the font name is consulted as well.
    """
    flags = int(span.get("flags", 0))
    name = str(span.get("font", "")).lower()
    bold = bool(flags & FLAG_BOLD) or "bold" in name or "black" in name or "heavy" in name
    italic = bool(flags & FLAG_ITALIC) or "italic" in name or "oblique" in name
    return bold, italic


def _line_text(line: dict[str, Any]) -> str:
    return "".join(span.get("text", "") for span in line.get("spans", []))


def _collect_running_heads(pages: list[dict[str, Any]], page_height: float) -> set[str]:
    """Normalised text of lines that repeat in the top/bottom margin bands."""
    top_limit = page_height * MARGIN_BAND
    bottom_limit = page_height * (1 - MARGIN_BAND)
    counts: Counter[str] = Counter()

    for page in pages:
        seen_on_page: set[str] = set()
        for block in page.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                y0, y1 = line["bbox"][1], line["bbox"][3]
                if y1 > top_limit and y0 < bottom_limit:
                    continue
                key = _normalise_head(_line_text(line))
                if key:
                    seen_on_page.add(key)
        counts.update(seen_on_page)

    threshold = max(3, int(len(pages) * RUNNING_HEAD_MIN_SHARE))
    return {key for key, count in counts.items() if count >= threshold}


def _normalise_head(text: str) -> str:
    """Collapse a margin line to a comparison key.

    Digits become ``#`` so ``Page 12`` and ``Page 13`` collapse together; a
    bare page number therefore normalises to ``#`` and repeats on every page.
    """
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed or len(collapsed) > 90:
        return ""
    return re.sub(r"\d+", "#", collapsed).lower()


def _body_font_size(pages: list[dict[str, Any]]) -> float:
    """The most common span size — everything larger is a candidate heading."""
    sizes: Counter[float] = Counter()
    for page in pages:
        for block in page.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if len(text) >= 3:
                        sizes[round(float(span.get("size", 0)), 1)] += len(text)
    if not sizes:
        return 10.0
    return sizes.most_common(1)[0][0]


#: Words that open a chapter even when the type size does not change, in the
#: source languages this skill actually sees.
_CHAPTER_WORD = re.compile(
    r"^\s*(?:chapter|part|book|prologue|epilogue|interlude"
    r"|فصل|بخش|پیش‌?گفتار|مقدمه|پس‌?گفتار|درآمد)\b",
    re.I,
)


def _heading_level(size: float, body_size: float, text: str, bold: bool,
                   *, ocr: bool = False) -> int | None:
    """Classify a short line as a heading, or ``None`` for body text.

    On an OCR layer the size is a per-line *estimate* that jitters, so only an
    unambiguous jump counts and the weaker signals — a bold run-in subheading,
    a modest size bump — are dropped. Trusting them there turned ordinary
    paragraphs into 939 false headings on a real 70-page scan.
    """
    if len(text) > 120 or not text.strip():
        return None
    ratio = size / body_size if body_size else 1.0

    if ocr:
        if ratio >= 1.9:
            return 1
        if ratio >= 1.55:
            return 2
        return 2 if _CHAPTER_WORD.match(text) else None

    if ratio >= 1.8:
        return 1
    if ratio >= 1.45:
        return 2
    if ratio >= 1.18:
        return 3
    # Same size as body, but short, bold and standalone: a run-in subheading.
    if bold and ratio >= 1.0 and len(text) <= 60 and not text.rstrip().endswith((".", "!", "?")):
        return 4
    # "CHAPTER SEVEN" style small-caps headings keep the body size.
    if _CHAPTER_WORD.match(text):
        return 2
    return None


def _join_lines(lines: list[str]) -> str:
    """Join the lines of a paragraph, undoing end-of-line hyphenation."""
    out = ""
    for raw in lines:
        piece = raw.strip()
        if not piece:
            continue
        if not out:
            out = piece
            continue
        if out.endswith("-") and not out.endswith("--"):
            out = out[:-1] + piece  # word split across a line break
        else:
            out = f"{out} {piece}"
    return out


#: Two lines belong to the same paragraph while their sizes agree this closely.
SIZE_GROUP_TOLERANCE = 0.06

#: In a text layer produced by OCR the "font size" is an *estimate* fitted to
#: each recognised line, not typography. Measured on a real scanned page, spans
#: of one uniform body paragraph ranged 11.5–15.0pt — a ±14% spread. At the
#: born-digital tolerance that splits almost every line into its own block, so
#: OCR gets a much wider one.
OCR_SIZE_GROUP_TOLERANCE = 0.28


def _block_groups(block: dict[str, Any], drop: set[str],
                  tolerance: float = SIZE_GROUP_TOLERANCE) -> list[dict[str, Any]]:
    """Split one PyMuPDF text block into same-size line groups.

    A single PDF text block routinely holds the tail of a paragraph *and* the
    subheading that follows it. Classifying the block as a whole would let the
    subheading's font size swallow the paragraph, so lines are grouped by size
    first and each group is classified on its own.

    Returns dicts of ``{markup, size, bold, bbox}`` in reading order.
    """
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in block.get("lines", []):
        if _normalise_head(_line_text(line)) in drop:
            continue
        spans: list[tuple[str, bool, bool]] = []
        size = 0.0
        bold_line = False
        for span in line.get("spans", []):
            text = span.get("text", "")
            if not text:
                continue
            bold, italic = _style_of(span)
            bold_line = bold_line or bold
            size = max(size, float(span.get("size", 0)))
            spans.append((text, bold, italic))
        if not spans:
            continue

        markup = ir.render_markup(spans)
        bbox = list(line.get("bbox", block.get("bbox", [0, 0, 0, 0])))
        same = (
            current is not None
            and current["size"] > 0
            and abs(size - current["size"]) / current["size"] <= tolerance
        )
        if same:
            current["lines"].append(markup)
            current["size"] = max(current["size"], size)
            current["bold"] = current["bold"] or bold_line
            current["bbox"] = _union(current["bbox"], bbox)
        else:
            current = {"lines": [markup], "size": size, "bold": bold_line, "bbox": bbox}
            groups.append(current)

    # Joining happens on already-marked-up strings, which is safe because a
    # marker never straddles a line break here.
    return [
        {
            "markup": _join_lines(group["lines"]),
            "size": group["size"],
            "bold": group["bold"],
            "bbox": [round(v, 2) for v in group["bbox"]],
        }
        for group in groups
        if _join_lines(group["lines"]).strip()
    ]


def _union(a: list[float], b: list[float]) -> list[float]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _extract_images(
    doc: "pymupdf.Document",
    page: "pymupdf.Page",
    page_no: int,
    asset_dir: Path,
    seen: dict[str, str],
) -> list[dict[str, Any]]:
    """Save every image on the page and describe it in points."""
    found: list[dict[str, Any]] = []
    for order, info in enumerate(page.get_images(full=True), start=1):
        xref = info[0]
        try:
            raw = doc.extract_image(xref)
        except Exception:  # damaged or unsupported stream
            continue
        data = raw.get("image")
        if not data:
            continue

        digest = ir.sha256_bytes(data)
        if digest in seen:
            asset_name = seen[digest]  # same picture reused (e.g. a chapter ornament)
        else:
            asset_name = f"p{page_no:04d}-img{order:03d}.{raw.get('ext', 'png')}"
            asset_path = asset_dir / asset_name
            asset_path.write_bytes(data)
            seen[digest] = asset_name

        rects = page.get_image_rects(xref)
        rect = rects[0] if rects else None
        found.append({
            "asset": asset_name,
            "sha256": digest,
            "page": page_no,
            "bbox": [round(v, 2) for v in rect] if rect else None,
            "width_pt": round(rect.width, 2) if rect else None,
            "height_pt": round(rect.height, 2) if rect else None,
            "pixel_width": raw.get("width"),
            "pixel_height": raw.get("height"),
            "top": rect.y0 if rect else 0.0,
        })
    return found


def read_pdf(
    path: str,
    asset_dir: Path,
    *,
    lang_source: str = "en",
    lang_target: str = "fa-IR",
    max_pages: int | None = None,
    ocr_text: bool = False,
) -> dict[str, Any]:
    asset_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(path)
    try:
        return _read_open_pdf(
            doc, path, asset_dir,
            lang_source=lang_source, lang_target=lang_target, max_pages=max_pages,
            ocr_text=ocr_text,
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
) -> dict[str, Any]:
    tolerance = OCR_SIZE_GROUP_TOLERANCE if ocr_text else SIZE_GROUP_TOLERANCE
    page_count = len(doc) if max_pages is None else min(len(doc), max_pages)
    raw_pages = [
        doc[i].get_text("dict", sort=True) for i in range(page_count)
    ]

    first = doc[0] if page_count else None
    page_w = float(first.rect.width) if first else 396.0
    page_h = float(first.rect.height) if first else 612.0

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

    blocks: list[dict[str, Any]] = []
    seen_assets: dict[str, str] = {}
    scanned_pages: list[int] = []
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

        images = _extract_images(doc, page, page_no, asset_dir, seen_assets)
        text_items: list[tuple[float, str, dict[str, Any]]] = []

        page_chars = 0
        for raw_block in page_dict.get("blocks", []):
            if raw_block.get("type") != 0:
                continue
            for group in _block_groups(raw_block, drop, tolerance):
                page_chars += len(group["markup"])
                text_items.append((group["bbox"][1], "text", group))

        if page_chars < SCANNED_CHAR_THRESHOLD and images:
            scanned_pages.append(page_no)

        for image in images:
            text_items.append((image["top"], "image", image))

        text_items.sort(key=lambda item: item[0])

        if index > 0:
            add("pagebreak", page=page_no, soft=True)

        for _, kind, payload in text_items:
            if kind == "image":
                add(
                    "image",
                    page=page_no,
                    asset=payload["asset"],
                    sha256=payload["sha256"],
                    bbox=payload["bbox"],
                    width_pt=payload["width_pt"],
                    height_pt=payload["height_pt"],
                    pixel_width=payload["pixel_width"],
                    pixel_height=payload["pixel_height"],
                    alt="",
                    target_alt=None,
                )
                continue

            markup = payload["markup"]
            level = _heading_level(
                payload["size"], body_size, ir.plain_text(markup), payload["bold"],
                ocr=ocr_text,
            )
            if level is not None:
                add("heading", page=page_no, level=level, bbox=payload["bbox"],
                    text=markup, font_size_pt=round(payload["size"], 2))
            else:
                add("paragraph", page=page_no, bbox=payload["bbox"], text=markup)

    book["blocks"] = _merge_split_paragraphs(blocks)
    book["source"]["scanned_pages"] = scanned_pages
    book["source"]["body_font_pt"] = body_size
    book["source"]["running_heads_dropped"] = sorted(drop)[:20]
    book["source"]["from_ocr"] = ocr_text
    return book


#: Sentence-final characters. A paragraph ending in one of these is complete.
#: Includes the Persian question mark, semicolon and ellipsis, so a Persian
#: paragraph is recognised as finished by the same rule as an English one.
_SENTENCE_END = ".!?:;»”\"'*`)]؟؛…،"


def _continues(first: str) -> bool:
    """Does this opening character suggest the previous paragraph ran on?

    In a cased script a lower-case opener is the signal. Persian, Arabic,
    Hebrew and CJK have no case at all, and ``'م'.islower()`` is ``False`` —
    so testing case alone silently disables paragraph merging for every one of
    them, leaving the translator a book of one-line fragments. For a caseless
    opener the decision rests entirely on the previous paragraph having ended
    mid-sentence, which is the stronger half of the signal anyway.
    """
    return not first.isupper()


def _merge_split_paragraphs(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rejoin a paragraph that a line, column or page break cut in half.

    The signal is conservative and needs both halves to agree: the earlier
    paragraph ends mid-sentence *and* the later one starts lower-case. Prose
    that genuinely starts a new paragraph practically always ends the previous
    one with punctuation, so this leaves real paragraph breaks intact — while
    repairing the extractor artefacts that would otherwise reach the
    translator as two half-sentences with no shared context.
    """
    merged: list[dict[str, Any]] = []
    for block in blocks:
        if block["type"] != "paragraph" or not merged:
            merged.append(block)
            continue

        previous = None
        for candidate in reversed(merged):
            if candidate["type"] == "paragraph":
                previous = candidate
                break
            if candidate["type"] == "pagebreak":
                continue  # a page break alone does not end a paragraph
            break  # a heading, image or separator genuinely separates the two

        if previous is None:
            merged.append(block)
            continue

        tail = ir.plain_text(previous.get("text", "")).rstrip()
        head = ir.plain_text(block.get("text", "")).lstrip()
        if tail and head and tail[-1] not in _SENTENCE_END and _continues(head[0]):
            previous["text"] = f"{previous['text'].rstrip()} {block['text'].lstrip()}"
            if block.get("bbox") and previous.get("bbox"):
                previous["bbox"] = _union(previous["bbox"], block["bbox"])
            continue
        merged.append(block)
    return merged
