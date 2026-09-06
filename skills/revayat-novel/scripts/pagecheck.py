"""What a rendered page is measured against - the checks themselves.

Shared by the two things that render something and look at it: `renderqa`,
which lays out one source page on its own, and `docqa`, which lays out the
finished book. They ask different questions of different artefacts, but the
questions are built from the same measurements, and a check that answered
differently depending on which of them called it would be worse than useless.

This is a *structural* comparison, not a pixel one. Persian reflows - it is a
different language set in a different direction, and the line breaks, the line
count and often the page count will not match the source. Demanding pixel
equality would fail every correct page. What must hold is structure and
geometry: the blocks that belong on this page are on it, once each; the
pictures are the same pictures in the same order at the same shape; nothing is
outside the body area or off the trim; there is no blank band where a page's
worth of text should be.

Expectations come from ``book.json`` rather than from re-reading the source
PDF, because the IR *is* what the source page became and ownership by page is
already decided there.

**Two questions are never asked of the render**, both because measurement
showed the render lies about them for Arabic script: whether the Persian text
is present, and whether a paragraph is right-to-left. PyMuPDF drops the
zero-width non-joiner and transposes letters, and its block boxes do not report
alignment. Both come from the document's own XML - `document_text` and
`check_direction_in_document` - and geometry alone comes from the page.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

import bookir as ir
import pagerun
import qa

SCHEMA = "revayat-novel/renderqa@1"

#: Failed attempts a page may make before this refuses to look again. A render
#: that fails the same way four times is not going to pass on the fifth, and a
#: caller driving this in a loop must be able to hit a wall rather than spin.
MAX_ATTEMPTS = 3

DEFAULT_DPI = 110

#: A page size within this of the profile is the same size — a converter that
#: rounds points to millimetres and back lands a fraction of a point out.
SIZE_TOLERANCE_PT = 3.0
#: Content may sit this far outside the body box before it counts as overflow:
#: a hanging quotation mark and an italic's overhang legitimately do.
BODY_TOLERANCE_PT = 6.0
#: Two pictures are the same shape within this relative difference.
ASPECT_TOLERANCE = 0.08
#: A Persian paragraph this far short of the body's right edge, while hugging
#: the left, was set left-to-right.
RTL_TOLERANCE_PT = 24.0
#: …judged only on blocks long enough to have wrapped. A three-word heading is
#: short enough to sit anywhere without meaning anything.
RTL_MIN_CHARS = 40
#: An empty band this share of the body height, *between* two inked bands, is
#: a hole in the page rather than a chapter ending early.
BLANK_BAND_SHARE = 0.35
#: How close to the bottom of the body a band must end to count as pinned
#: there — a footnote, or the last line of a full page. Measured on a real
#: build, the footnote block ended within a few points of the body bottom.
FOOTNOTE_FOOT_TOLERANCE_PT = 12.0
#: Text over a picture, as a share of the smaller of the two areas.
OVERLAP_SHARE = 0.15
#: Characters of a block's translation used to find it on the rendered page.
#: A prefix rather than the whole string, because the tail of the last block on
#: a page legitimately reflows onto the next one.
PROBE_CHARS = 48
#: Below this a probe is too generic to prove a duplicate — "«بله.»" repeats in
#: any novel — so a short block is checked for presence only.
PROBE_MIN_CHARS = 12

#: Below this, in either dimension, a drawn object is a glyph rather than an
#: illustration: a tab leader's dots, a bullet, the rule above a footnote. A
#: book plate is never this small, and treating one as a picture produced 60
#: "text sits on a picture" findings on a table of contents overlapping its own
#: leader dots.
GLYPH_MAX_PT = 24.0

_SPACE = re.compile(r"\s+")


def _flat(text: str) -> str:
    """Markup stripped and whitespace collapsed — what a renderer emits."""
    return _SPACE.sub(" ", ir.plain_text(text or "")).strip()


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _pymupdf():
    """PyMuPDF, or ``None``.

    Imported here rather than at module scope so that a machine without it
    reports a page as *unverified* instead of failing to import the checker at
    all — "we could not look" and "we looked and it was fine" must never be the
    same answer.
    """
    try:
        import pymupdf  # noqa: PLC0415  (deliberate optional import)
        return pymupdf
    except ImportError:
        try:
            import fitz  # noqa: PLC0415
            return fitz
        except ImportError:
            return None


def page_view(pdf_path: Path, index: int) -> dict[str, Any]:
    """One PDF page reduced to the geometry the checks compare.

    ``{"width_pt", "height_pt", "blocks": [{"text", "bbox"}],
       "images": [{"bbox", "width_pt", "height_pt"}]}``
    """
    pymupdf = _pymupdf()
    if pymupdf is None:
        raise RuntimeError("PyMuPDF is not installed")

    doc = pymupdf.open(str(pdf_path))
    try:
        if not 0 <= index < len(doc):
            raise IndexError(f"{pdf_path} has {len(doc)} pages; wanted index {index}")
        page = doc[index]
        blocks = [
            {"text": item[4], "bbox": [round(float(v), 2) for v in item[:4]]}
            for item in page.get_text("blocks")
            if len(item) > 6 and item[6] == 0 and str(item[4]).strip()
        ]
        images = []
        for info in page.get_images(full=True):
            for rect in page.get_image_rects(info[0]):
                images.append({
                    "bbox": [round(float(v), 2) for v in rect],
                    "width_pt": round(float(rect.width), 2),
                    "height_pt": round(float(rect.height), 2),
                })
        return {
            "width_pt": round(float(page.rect.width), 2),
            "height_pt": round(float(page.rect.height), 2),
            "blocks": blocks,
            "images": images,
        }
    finally:
        doc.close()


def render_png(pdf_path: Path, index: int, out_path: Path,
               dpi: int = DEFAULT_DPI) -> Path | None:
    """Rasterise one PDF page, or ``None`` when it cannot be rendered.

    Producing evidence must never be the thing that stops a page being
    reported on: a missing or damaged file leaves the artifact unwritten and
    the report says the page is unverified.
    """
    pymupdf = _pymupdf()
    if pymupdf is None or not Path(pdf_path).exists():
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception:  # PyMuPDF raises its own hierarchy for a damaged file
        return None
    try:
        if not 0 <= index < len(doc):
            return None
        doc[index].get_pixmap(dpi=dpi).save(str(out_path))
    finally:
        doc.close()
    return out_path


# --------------------------------------------------------------------------- #
# Expectations
def views_of(pdf_path: Path) -> list[dict[str, Any]]:
    """Every page of a preview, read back. Empty when it cannot be opened."""
    total = page_count(pdf_path)
    return [page_view(pdf_path, index) for index in range(total)]


def page_count(pdf_path: Path) -> int:
    """How many pages, or ``0`` when the file cannot be opened at all.

    Deliberately not an exception: a caller counting pages so it can rasterise
    them has its own "we could not look" path, and a raise from here jumps past
    it and turns a page that could not be rendered into a crashed run.
    """
    pymupdf = _pymupdf()
    if pymupdf is None:
        return 0
    try:
        document = pymupdf.open(str(pdf_path))
    except Exception:
        return 0
    try:
        return len(document)
    finally:
        document.close()


# --------------------------------------------------------------------------- #

def expectations(book: dict[str, Any], page: int) -> dict[str, Any]:
    """What the translated page must show, taken from the IR.

    Uses the same ownership rule as the page jobs, so what is checked for on a
    page is exactly what was sent out to be translated for it — a block that
    ran on from the previous page is that page's to prove, not this one's.
    """
    lookup = ir.blocks_by_id(book)
    job = next((j for j in pagerun.owners(book) if j["page"] == page), None)
    if job is None:
        return {"page": page, "setup": dict(book.get("page") or {}),
                "texts": [], "images": [], "translatable": 0}

    texts: list[str] = []
    translatable = 0
    for block_id in job["block_ids"]:
        block = lookup.get(block_id) or {}
        if block.get("type") not in ir.TEXT_TYPES:
            continue
        if not (block.get("text") or "").strip():
            continue
        translatable += 1
        target = (block.get("target") or "").strip()
        if target:
            texts.append(target)

    images = []
    for block_id in job["image_ids"]:
        block = lookup.get(block_id) or {}
        width, height = block.get("width_pt"), block.get("height_pt")
        images.append({
            "id": block_id,
            "aspect": (float(width) / float(height)) if width and height else None,
        })

    return {
        "page": page,
        "setup": pagerun.geometry(book, job["block_ids"], lookup, page),
        "texts": texts,
        "images": images,
        "translatable": translatable,
    }


def body_rect(setup: dict[str, Any]) -> tuple[float, float, float, float]:
    """``(left, top, right, bottom)`` of the body area, in points.

    ponytail: inner/outer are read as left/right. In a mirrored-margin book
    they swap on a verso page; the two differ by 9pt in the default profile,
    which is inside the tolerance the overflow check already allows.
    """
    width = float(setup.get("width_pt") or 0.0)
    height = float(setup.get("height_pt") or 0.0)
    return (
        float(setup.get("margin_inner_pt") or 0.0),
        float(setup.get("margin_top_pt") or 0.0),
        width - float(setup.get("margin_outer_pt") or 0.0),
        height - float(setup.get("margin_bottom_pt") or 0.0),
    )


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: list[float], b: list[float]) -> float:
    return (max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
            * max(0.0, min(a[3], b[3]) - max(a[1], b[1])))


def _same_shape(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return True  # nothing recorded to compare; counted, not judged
    return abs(a - b) <= ASPECT_TOLERANCE * max(a, b)


def _check_page_size(target: dict[str, Any], setup: dict[str, Any],
                     report: qa.Report, unit: str) -> None:
    wanted_w = float(setup.get("width_pt") or 0.0)
    wanted_h = float(setup.get("height_pt") or 0.0)
    if not wanted_w or not wanted_h:
        return
    got_w, got_h = target["width_pt"], target["height_pt"]
    if (abs(got_w - wanted_w) > SIZE_TOLERANCE_PT
            or abs(got_h - wanted_h) > SIZE_TOLERANCE_PT):
        turned = (abs(got_w - wanted_h) <= SIZE_TOLERANCE_PT
                  and abs(got_h - wanted_w) <= SIZE_TOLERANCE_PT)
        report.add(qa.ERROR, "page-size", unit,
                   f"rendered {got_w}×{got_h}pt, book is {wanted_w}×{wanted_h}pt"
                   + (" — the page is on its side" if turned else ""))


def is_illustration(image: dict[str, Any]) -> bool:
    """Big enough to be a picture in a book rather than a drawn glyph."""
    box = image.get("bbox") or [0, 0, 0, 0]
    return (box[2] - box[0]) >= GLYPH_MAX_PT and (box[3] - box[1]) >= GLYPH_MAX_PT


def is_furniture(box: list[float], setup: dict[str, Any],
                 page_height: float) -> bool:
    """Is this content the page's own furniture rather than the book's text?

    A running head sits above the top margin and a page number below the bottom
    one — that is where they belong, and reporting them as overflow means a
    correct document comes back with a finding on every page. Measured on a
    three-page build: 60 findings, every one of them a page number.

    A check that fires on correct output is worse than no check, because the
    next reader learns to skip the report.
    """
    top, bottom = setup["margin_top_pt"], page_height - setup["margin_bottom_pt"]
    return box[3] <= top + BODY_TOLERANCE_PT or box[1] >= bottom - BODY_TOLERANCE_PT


def _check_body_area(target: dict[str, Any], setup: dict[str, Any],
                     report: qa.Report, unit: str, *,
                     margins: bool = True) -> None:
    """Anything off the trim is clipped; anything past the margins overflows.

    ``margins=False`` keeps only the clipping half, and the assembled document
    is checked that way. The margin half asks "did this paragraph overflow the
    body I laid out", which is a real question about a page built from the IR
    and the wrong question about the whole book: Word's own generated
    furniture — a table of contents with hanging indents and tab leaders, a
    footnote rule — legitimately sits in the margin. Measured on a three-page
    build, asking it there produced 60 findings and every one was the TOC.

    Running off the *paper* is unambiguous at either scope, so that half stays.
    """
    left, top, right, bottom = body_rect(setup)
    page_w, page_h = target["width_pt"], target["height_pt"]

    # A glyph Word drew as an outline is text, and its *ink* box overshoots the
    # text margin by a point or two on any justified line. Measured: three
    # `text-overflow` findings per correct Persian page, each one a letter. The
    # paragraph those glyphs belong to is checked as a block, above.
    drawn = [image for image in target["images"] if is_illustration(image)]
    for item in target["blocks"] + drawn:
        box = item["bbox"]
        if is_furniture(box, setup, page_h):
            continue
        what = _flat(item.get("text", ""))[:40] or "a picture"
        if (box[0] < -BODY_TOLERANCE_PT or box[1] < -BODY_TOLERANCE_PT
                or box[2] > page_w + BODY_TOLERANCE_PT
                or box[3] > page_h + BODY_TOLERANCE_PT):
            report.add(qa.ERROR, "text-clipped", unit,
                       f"{what!r} at {box} runs off a {page_w}×{page_h}pt page")
            continue
        if margins and (
                box[0] < left - BODY_TOLERANCE_PT
                or box[1] < top - BODY_TOLERANCE_PT
                or box[2] > right + BODY_TOLERANCE_PT
                or box[3] > bottom + BODY_TOLERANCE_PT):
            report.add(qa.ERROR, "text-overflow", unit,
                       f"{what!r} at {box} is outside the body area "
                       f"[{left}, {top}, {right}, {bottom}]")


def document_text(docx: Path) -> str:
    """Every paragraph of a .docx as one string, in document order.

    Read from the file rather than from the render, for the same reason the
    direction check is: PyMuPDF's Arabic-script readback is not faithful.
    Measured on a correct Word render of a correct book, the zero-width
    non-joiner was dropped and ``بالا`` came back with its
    letters transposed. Probing that for the book's own sentences reports every
    Persian paragraph missing from a page that is perfectly set - a check that
    fails on correct output, which is worse than no check at all.

    The file says what Word will draw. Geometry still comes from the render,
    because that is the question a file cannot answer.
    """
    import zipfile

    with zipfile.ZipFile(docx) as archive:
        body = archive.read("word/document.xml").decode("utf-8")
    paragraphs = []
    for block in re.findall(r"<w:p[ >].*?</w:p>", body, re.S):
        pieces = re.findall(r"<w:t[^>]*>(.*?)</w:t>", block, re.S)
        if pieces:
            paragraphs.append("".join(pieces))
    return " ".join(paragraphs)


def check_direction_in_document(docx: Path) -> list[dict[str, Any]]:
    """Is the built document right-to-left? Asked of the file, not the render.

    The one direction check that cannot lie. A rendered page tells you where
    ink landed, and for Arabic script PyMuPDF's block boxes do not report that
    faithfully — so a correct document comes back looking flush-left. The
    `w:bidi` on a paragraph, and on the style it inherits from, is the setting
    Word actually obeys.
    """
    findings: list[dict[str, Any]] = []
    with zipfile.ZipFile(docx) as archive:
        document = archive.read("word/document.xml").decode("utf-8")
        styles = archive.read("word/styles.xml").decode("utf-8")

    normal = re.search(r'<w:style [^>]*w:styleId="Normal".*?</w:style>',
                       styles, re.S)
    inherits = bool(normal and "<w:bidi" in normal.group(0))

    paragraphs = [p for p in re.findall(r"<w:p.*?</w:p>", document, re.S)
                  if "<w:t" in p]
    without = [p for p in paragraphs if "<w:bidi" not in p]
    if without and not inherits:
        findings.append({
            "severity": qa.ERROR, "code": "document-not-rtl", "unit": "document",
            "detail": f"{len(without)} of {len(paragraphs)} paragraphs carry no "
                      f"w:bidi and the Normal style does not supply one, so "
                      f"Word will set them left-to-right",
        })
    return findings


def _probe(text: str) -> str:
    """What to look for: what a reader sees, not what the IR stores.

    Markup and footnote markers are the IR's, not the document's - probing
    ``**مالکیت**`` against a document that
    contains the word without the asterisks reports a paragraph missing that is
    right there.
    """
    return _flat(ir.plain_text(text))[:PROBE_CHARS]


def _check_text_presence(target: dict[str, Any], expected: dict[str, Any],
                         report: qa.Report, unit: str, *,
                         source: str | None = None) -> None:
    """Is every translated block here, exactly once?

    ``source`` is the document's own text when the caller has the file. Without
    it the question is asked of the render, which is reliable for Latin script
    and not for Arabic - so an unmatched Persian probe with no file behind it is
    reported as *not asked*, never as missing. A false failure and a false pass
    are both worse than an honest gap.
    """
    haystack = _flat(source) if source is not None else " ".join(
        _flat(block["text"]) for block in target["blocks"])

    if expected["translatable"] and not expected["texts"]:
        report.add(qa.ERROR, "text-missing", unit,
                   f"{expected['translatable']} blocks belong to this page and "
                   f"none of them is translated yet")
        return

    for text in expected["texts"]:
        probe = _probe(text)
        if not probe:
            continue
        seen = haystack.count(probe)
        if seen == 0:
            if source is None and ir.script_ratio(probe)[0] >= 0.5:
                report.add(qa.WARNING, "text-unverified", unit,
                           f"{probe[:30]!r} could not be looked for: only a "
                           f"rendered page was supplied, and Arabic script does "
                           f"not read back from one faithfully")
                continue
            report.add(qa.ERROR, "text-missing", unit,
                       f"nothing here begins {probe!r}")
        elif seen > 1 and len(probe) >= PROBE_MIN_CHARS:
            report.add(qa.ERROR, "text-duplicated", unit,
                       f"{probe!r} appears {seen} times")


def _check_images(target: dict[str, Any], expected: dict[str, Any],
                  report: qa.Report, unit: str) -> None:
    wanted = [entry["aspect"] for entry in expected["images"]]
    # Word draws Persian glyphs as outlines when it exports a PDF, and PyMuPDF
    # reports every one of them as an image. Measured on a ten-paragraph page:
    # 2536 of them. Counting those as illustrations makes `image-extra` fire on
    # every correct Persian page there is.
    plates = [image for image in target["images"] if is_illustration(image)]
    got = [
        (image["width_pt"] / image["height_pt"]) if image["height_pt"] else None
        for image in sorted(plates, key=lambda i: (i["bbox"][1], i["bbox"][0]))
    ]

    if len(got) < len(wanted):
        report.add(qa.ERROR, "image-missing", unit,
                   f"{len(wanted)} illustrations belong on this page, {len(got)} "
                   f"are on it")
        return
    if len(got) > len(wanted):
        report.add(qa.ERROR, "image-extra", unit,
                   f"{len(got)} illustrations on a page that owns {len(wanted)}")
        return

    wrong = [i for i, (a, b) in enumerate(zip(got, wanted))
             if not _same_shape(a, b)]
    if not wrong:
        return

    # The pictures can only be told apart here by shape, so a set that matches
    # in some other order is a reordering rather than a resized picture — which
    # is exactly what a swapped pair looks like.
    order = sorted((a for a in got if a is not None))
    same_set = sorted((a for a in wanted if a is not None))
    if len(order) == len(same_set) and all(
        _same_shape(a, b) for a, b in zip(order, same_set)
    ):
        report.add(qa.ERROR, "image-reordered", unit,
                   f"the same {len(got)} illustrations, in a different order "
                   f"(positions {[i + 1 for i in wrong]})")
        return

    for index in wrong:
        report.add(qa.ERROR, "image-aspect", unit,
                   f"illustration {index + 1} is {got[index]:.3f} wide-to-tall, "
                   f"the book's is {wanted[index]:.3f}")


def _check_blank_regions(target: dict[str, Any], setup: dict[str, Any],
                         report: qa.Report, unit: str) -> None:
    """A hole in the middle of a page is a build that gave up halfway.

    Measured from the geometry rather than from pixels: a band with no text box
    and no picture in it *is* a blank band, and counting boxes cannot be fooled
    by a light background or an anti-aliased glyph.
    """
    left, top, right, bottom = body_rect(setup)
    height = bottom - top
    if height <= 0:
        return

    spans = sorted(
        (max(top, item["bbox"][1]), min(bottom, item["bbox"][3]))
        for item in target["blocks"] + target["images"]
            if not is_furniture(item["bbox"], setup, target["height_pt"])
        if item["bbox"][3] > top and item["bbox"][1] < bottom
    )
    if not spans:
        report.add(qa.ERROR, "blank-region", unit,
                   "nothing at all inside the body area")
        return

    merged: list[list[float]] = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    # A footnote is pinned to the foot of the page however little text sits
    # above it, so the space before it is layout rather than a hole.
    #
    # What identifies it is not where it starts but that it *ends* flush with
    # the bottom of the body — a footnote is anchored there. Judging by where a
    # band starts swallows real holes: a paragraph stranded in the lower half
    # of an otherwise empty page starts low too, and that is exactly the
    # build-gave-up shape this check exists to catch.
    for index, (before, after) in enumerate(zip(merged, merged[1:])):
        gap = after[0] - before[1]
        pinned_to_the_foot = (index == len(merged) - 2
                              and after[1] >= bottom - FOOTNOTE_FOOT_TOLERANCE_PT)
        if pinned_to_the_foot:
            continue
        if gap > BLANK_BAND_SHARE * height:
            report.add(qa.ERROR, "blank-region", unit,
                       f"{gap:.0f}pt of nothing between {before[1]:.0f} and "
                       f"{after[0]:.0f}, in a {height:.0f}pt body")


def _check_overlap(target: dict[str, Any], report: qa.Report, unit: str,
                   setup: dict[str, Any] | None = None) -> None:
    """Text sitting on a picture. Page furniture is not text sitting on a picture.

    ``setup`` is optional so the existing per-page callers keep working; when it
    is given, a page number in the footer stops being reported as a collision
    with the glyphs Word puts beside it.
    """
    height = target["height_pt"]
    for block in target["blocks"]:
        if setup and is_furniture(block["bbox"], setup, height):
            continue
        for image in target["images"]:
            if not is_illustration(image):
                continue
            if setup and is_furniture(image["bbox"], setup, height):
                continue
            shared = _intersection(block["bbox"], image["bbox"])
            smaller = min(_area(block["bbox"]), _area(image["bbox"]))
            if smaller > 0 and shared / smaller > OVERLAP_SHARE:
                report.add(qa.ERROR, "text-image-overlap", unit,
                           f"{_flat(block['text'])[:40]!r} sits on a picture "
                           f"({shared / smaller:.0%} of it covered)")


def check_page(target: dict[str, Any], expected: dict[str, Any], *,
               source: str | None = None) -> qa.Report:
    """Every structural check, against one rendered page.

    ``source`` is the document's own text when the caller has the file; see
    `_check_text_presence` for why a rendered page is not asked about Persian.
    """
    report = qa.Report()
    unit = f"page{expected['page']:04d}"
    setup = expected["setup"]

    _check_page_size(target, setup, report, unit)
    _check_body_area(target, setup, report, unit)
    _check_text_presence(target, expected, report, unit, source=source)
    _check_images(target, expected, report, unit)
    _check_blank_regions(target, setup, report, unit)
    _check_overlap(target, report, unit)

    report.count("expected_blocks", len(expected["texts"]))
    report.count("rendered_blocks", len(target["blocks"]))
    report.count("expected_images", len(expected["images"]))
    report.count("rendered_images", len(target["images"]))
    return report


def combine(views: list[dict[str, Any]]) -> dict[str, Any]:
    """Every page of a preview as one surface, in reading order.

    Each sheet's boxes are pushed down by the sheets above it. Nothing measures
    against those coordinates - the geometry checks read the per-page views -
    but `_check_images` orders illustrations by where they sit, and without the
    offset a picture at the top of sheet two would sort ahead of one halfway
    down sheet one. That is a reordering finding on a page that is in order.
    """
    blocks: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    offset = 0.0
    for view in views:
        for source, sink in ((view["blocks"], blocks), (view["images"], images)):
            for item in source:
                box = item["bbox"]
                sink.append({**item, "bbox": [box[0], box[1] + offset,
                                              box[2], box[3] + offset]})
        offset += float(view["height_pt"] or 0.0)
    return {"blocks": blocks, "images": images,
            "width_pt": views[0]["width_pt"], "height_pt": views[0]["height_pt"]}


def check_preview(views: list[dict[str, Any]], expected: dict[str, Any], *,
                  source: str | None = None) -> qa.Report:
    """One source page's preview, however many sheets it came out as.

    The split is between what a *page* can be wrong about and what a *page's
    content* can be wrong about. A margin is overrun on one sheet, a hole opens
    on one sheet, a paragraph is set left-to-right on one sheet - those are
    asked of each. Whether every block that belongs to this source page is
    present exactly once is asked of the preview as a whole, or a paragraph that
    reflowed onto a second sheet would be reported missing from the first.
    """
    report = qa.Report()
    unit = f"page{expected['page']:04d}"
    setup = expected["setup"]

    if not views:
        report.add(qa.ERROR, "preview-empty", unit,
                   "the preview rendered no pages at all")
        return report

    whole = combine(views)
    _check_text_presence(whole, expected, report, unit, source=source)
    _check_images(whole, expected, report, unit)

    for index, view in enumerate(views):
        # A one-sheet preview keeps the page's own name, so the overwhelming
        # majority of reports read the way they always have.
        sheet = unit if len(views) == 1 else f"{unit}-{index + 1}"
        _check_page_size(view, setup, report, sheet)
        _check_body_area(view, setup, report, sheet)
        _check_blank_regions(view, setup, report, sheet)
        _check_overlap(view, report, sheet, setup)

    report.count("expected_blocks", len(expected["texts"]))
    report.count("rendered_blocks", len(whole["blocks"]))
    report.count("expected_images", len(expected["images"]))
    report.count("rendered_images", sum(1 for i in whole["images"]
                                        if is_illustration(i)))
    report.count("preview_sheets", len(views))
    return report
